#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# SV-PRO Backup Script — Cron backup dữ liệu PostgreSQL + embeddings → S3/MinIO
#
# Chức năng:
#   1. pg_dump database svpro_db → gzip → upload S3/MinIO.
#   2. Nén thư mục output/ (ảnh crop biển số + khuôn mặt) → upload S3.
#   3. Giữ lại tối đa BACKUP_RETAIN_DAYS ngày backup cũ → xóa cũ hơn.
#   4. Gửi thông báo Telegram khi backup thành công/thất bại.
#   5. Ghi log vào /var/log/svpro_backup.log.
#
# Cron gợi ý (chạy lúc 2:00 sáng mỗi ngày):
#   0 2 * * * /opt/svpro/scripts/backup.sh >> /var/log/svpro_backup.log 2>&1
#
# Biến môi trường cần thiết (đặt trong .env hoặc cron env):
#   POSTGRES_DSN          — DSN kết nối PostgreSQL
#   S3_BUCKET             — Tên bucket (e.g. svpro-backups)
#   S3_ENDPOINT           — MinIO endpoint (e.g. http://minio:9000) hoặc để trống cho AWS S3
#   AWS_ACCESS_KEY_ID     — Access key
#   AWS_SECRET_ACCESS_KEY — Secret key
#   TELEGRAM_BOT_TOKEN    — Token bot Telegram để gửi thông báo
#   TELEGRAM_CHAT_ID      — Chat ID nhận thông báo
#   BACKUP_RETAIN_DAYS    — Số ngày giữ backup (mặc định: 7)
#   OUTPUT_DIR            — Thư mục ảnh crop (mặc định: /opt/svpro/output)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config từ env ─────────────────────────────────────────────────────────────
POSTGRES_DSN="${POSTGRES_DSN:-postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db}"
S3_BUCKET="${S3_BUCKET:-svpro-backups}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
BACKUP_RETAIN_DAYS="${BACKUP_RETAIN_DAYS:-7}"
OUTPUT_DIR="${OUTPUT_DIR:-/opt/svpro/output}"
BACKUP_TMP="/tmp/svpro_backup"

# ── Timestamp ─────────────────────────────────────────────────────────────────
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DATE_TAG=$(date +"%Y-%m-%d")
LOG_PREFIX="[SV-PRO Backup ${TIMESTAMP}]"

# ── Helpers ──────────────────────────────────────────────────────────────────

# In log có timestamp
log() {
  echo "${LOG_PREFIX} $*"
}

# Gửi thông báo Telegram
send_telegram() {
  local msg="$1"
  if [[ -z "${TELEGRAM_BOT_TOKEN}" || -z "${TELEGRAM_CHAT_ID}" ]]; then
    return 0
  fi
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=${msg}" \
    -d "parse_mode=HTML" \
    > /dev/null || true
}

# Upload file hoặc folder lên S3/MinIO
s3_upload() {
  local src="$1"
  local dst="$2"

  if [[ -n "${S3_ENDPOINT}" ]]; then
    # MinIO hoặc S3-compatible endpoint
    aws s3 cp "${src}" "s3://${S3_BUCKET}/${dst}" \
      --endpoint-url "${S3_ENDPOINT}" \
      --no-progress
  else
    # AWS S3 chính thức
    aws s3 cp "${src}" "s3://${S3_BUCKET}/${dst}" \
      --no-progress
  fi
}

# Xóa backup cũ hơn BACKUP_RETAIN_DAYS ngày trên S3
cleanup_old_backups() {
  local prefix="$1"
  log "Xóa backup cũ hơn ${BACKUP_RETAIN_DAYS} ngày ở s3://${S3_BUCKET}/${prefix}"

  local cutoff_date
  cutoff_date=$(date -d "${BACKUP_RETAIN_DAYS} days ago" +"%Y-%m-%d" 2>/dev/null \
             || date -v "-${BACKUP_RETAIN_DAYS}d" +"%Y-%m-%d")

  if [[ -n "${S3_ENDPOINT}" ]]; then
    aws s3 ls "s3://${S3_BUCKET}/${prefix}" \
      --endpoint-url "${S3_ENDPOINT}" 2>/dev/null \
      | awk '{print $4}' \
      | while read -r file; do
        file_date=$(echo "${file}" | grep -oP '\d{4}-\d{2}-\d{2}' | head -1 || true)
        if [[ -n "${file_date}" && "${file_date}" < "${cutoff_date}" ]]; then
          aws s3 rm "s3://${S3_BUCKET}/${prefix}${file}" \
            --endpoint-url "${S3_ENDPOINT}" --quiet || true
          log "  Xóa: ${file}"
        fi
      done
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

log "=== BẮT ĐẦU BACKUP SV-PRO ==="
mkdir -p "${BACKUP_TMP}"

BACKUP_OK=true
ERROR_MSG=""

# ── 1. Backup PostgreSQL ──────────────────────────────────────────────────────
log "1. Dumping PostgreSQL database..."
DB_DUMP_FILE="${BACKUP_TMP}/svpro_db_${TIMESTAMP}.sql.gz"

if pg_dump "${POSTGRES_DSN}" | gzip > "${DB_DUMP_FILE}"; then
  DB_SIZE=$(du -sh "${DB_DUMP_FILE}" | cut -f1)
  log "   ✅ DB dump: ${DB_DUMP_FILE} (${DB_SIZE})"

  # Upload
  S3_DB_PATH="database/${DATE_TAG}/svpro_db_${TIMESTAMP}.sql.gz"
  if s3_upload "${DB_DUMP_FILE}" "${S3_DB_PATH}"; then
    log "   ✅ Upload DB → s3://${S3_BUCKET}/${S3_DB_PATH}"
  else
    log "   ❌ Upload DB thất bại!"
    BACKUP_OK=false
    ERROR_MSG="${ERROR_MSG}\n- Upload DB thất bại"
  fi
else
  log "   ❌ pg_dump thất bại!"
  BACKUP_OK=false
  ERROR_MSG="${ERROR_MSG}\n- pg_dump thất bại"
fi

# ── 2. Backup output directory (ảnh crop) ─────────────────────────────────────
if [[ -d "${OUTPUT_DIR}" ]]; then
  log "2. Nén thư mục output: ${OUTPUT_DIR}"
  OUTPUT_ARCHIVE="${BACKUP_TMP}/svpro_output_${TIMESTAMP}.tar.gz"

  if tar -czf "${OUTPUT_ARCHIVE}" -C "$(dirname ${OUTPUT_DIR})" "$(basename ${OUTPUT_DIR})"; then
    OUT_SIZE=$(du -sh "${OUTPUT_ARCHIVE}" | cut -f1)
    log "   ✅ Output archive: ${OUTPUT_ARCHIVE} (${OUT_SIZE})"

    S3_OUT_PATH="output/${DATE_TAG}/svpro_output_${TIMESTAMP}.tar.gz"
    if s3_upload "${OUTPUT_ARCHIVE}" "${S3_OUT_PATH}"; then
      log "   ✅ Upload output → s3://${S3_BUCKET}/${S3_OUT_PATH}"
    else
      log "   ❌ Upload output thất bại!"
      BACKUP_OK=false
      ERROR_MSG="${ERROR_MSG}\n- Upload output thất bại"
    fi
  else
    log "   ⚠️  tar output thất bại — bỏ qua (không critical)"
  fi
else
  log "2. ⚠️  Thư mục output '${OUTPUT_DIR}' không tồn tại — bỏ qua"
fi

# ── 3. Dọn dẹp backup cũ ──────────────────────────────────────────────────────
log "3. Dọn dẹp backup cũ hơn ${BACKUP_RETAIN_DAYS} ngày..."
cleanup_old_backups "database/"
cleanup_old_backups "output/"

# ── 4. Dọn dẹp temp ───────────────────────────────────────────────────────────
rm -rf "${BACKUP_TMP}"
log "4. Dọn dẹp temp xong."

# ── 5. Thông báo Telegram ─────────────────────────────────────────────────────
if ${BACKUP_OK}; then
  TELE_MSG="✅ <b>SV-PRO Backup thành công</b>
📅 Ngày: ${DATE_TAG}
⏰ Lúc: $(date '+%H:%M:%S')
🗄️ Bucket: ${S3_BUCKET}
📦 DB: ${DB_SIZE:-unknown}
🔄 Giữ ${BACKUP_RETAIN_DAYS} ngày gần nhất"
  send_telegram "${TELE_MSG}"
  log "=== BACKUP THÀNH CÔNG ==="
else
  TELE_MSG="❌ <b>SV-PRO Backup CÓ LỖI</b>
📅 Ngày: ${DATE_TAG}
⏰ Lúc: $(date '+%H:%M:%S')
Lỗi: ${ERROR_MSG}"
  send_telegram "${TELE_MSG}"
  log "=== BACKUP CÓ LỖI: ${ERROR_MSG} ==="
  exit 1
fi
