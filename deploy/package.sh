#!/bin/bash
# =============================================================================
# SV-PRO — Đóng gói bộ cài đặt
# =============================================================================
# Chạy TRÊN MÁY DEV trước khi copy lên server.
# Script này copy tất cả source code + models vào thư mục deploy,
# rồi đóng gói thành 1 file svpro-deploy.tar.gz
# =============================================================================

set -e

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
ARCHIVE_NAME="svpro-deploy-$(date +%Y%m%d).tar.gz"

echo "================================================================"
echo "  SV-PRO — Đóng gói bộ cài đặt"
echo "================================================================"
echo ""
echo "  Thư mục gốc: $PROJECT_ROOT"
echo "  Đích:         $DEPLOY_DIR"
echo ""

# ── 1. Build frontend ────────────────────────────────────────────────────
echo "[1/5] Build frontend React..."
if [[ ! -d "$PROJECT_ROOT/dashboard/dist" ]]; then
    echo "  Chưa có dist — build..."
    if command -v npm &>/dev/null; then
        (cd "$PROJECT_ROOT/dashboard" && npm install && npm run build)
    else
        echo "  ⚠️  npm không tìm thấy. Bỏ qua frontend build."
        echo "      Build frontend trước: cd dashboard && npm install && npm run build"
    fi
else
    echo "  ✅ Frontend đã build sẵn"
fi

# ── 2. Copy backend source ────────────────────────────────────────────────
echo "[2/5] Copy backend source..."
rm -rf "$DEPLOY_DIR/backend"
mkdir -p "$DEPLOY_DIR/backend"
cp -r "$PROJECT_ROOT/backend" "$DEPLOY_DIR/backend/"

# Copy frontend dist vào backend (cho Nginx serve)
mkdir -p "$DEPLOY_DIR/backend/frontend"
if [[ -d "$PROJECT_ROOT/dashboard/dist" ]]; then
    cp -r "$PROJECT_ROOT/dashboard/dist/"* "$DEPLOY_DIR/backend/frontend/"
fi

# ── 3. Copy src module ──────────────────────────────────────────────────────
echo "[3/5] Copy AI pipeline modules..."
rm -rf "$DEPLOY_DIR/src"
mkdir -p "$DEPLOY_DIR/src"
cp -r "$PROJECT_ROOT/src" "$DEPLOY_DIR/src/"

# ── 4. Copy models (bao gồm yolov8n_plate) ──────────────────────────────────
echo "[4/5] Copy AI models..."
rm -rf "$DEPLOY_DIR/models"
mkdir -p "$DEPLOY_DIR/models"

if [[ -d "$PROJECT_ROOT/models" ]]; then
    cp -rn "$PROJECT_ROOT/models/"* "$DEPLOY_DIR/models/" 2>/dev/null || true
    echo "  ✅ $(ls "$DEPLOY_DIR/models" | wc -l) thư mục models"
else
    echo "  ⚠️  Không tìm thấy thư mục models/"
fi

# ── 5. Copy configs & Dockerfiles ───────────────────────────────────────────
echo "[5/5] Copy configs & Dockerfiles..."
for f in docker-compose.yml Dockerfile.backend Dockerfile.savant-ai-core \
         Dockerfile.ingress-manager requirements.txt module.yml \
         tracker/config_tracker_NvSORT.yml \
         scripts/sql scripts/backup.sh 2>/dev/null; do
    if [[ -e "$PROJECT_ROOT/$f" ]]; then
        mkdir -p "$(dirname "$DEPLOY_DIR/$f")"
        cp -r "$PROJECT_ROOT/$f" "$DEPLOY_DIR/$f"
    fi
done

# Monitoring
rm -rf "$DEPLOY_DIR/monitoring"
mkdir -p "$DEPLOY_DIR/monitoring"
for f in "$PROJECT_ROOT/monitoring/"*; do
    if [[ -e "$f" ]]; then
        cp -rn "$f" "$DEPLOY_DIR/monitoring/"
    fi
done

# ── Kiểm tra models quan trọng ─────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  KIỂM TRA MODELS:"
echo "================================================================"
check_model() {
    if [[ -f "$1" ]]; then
        SIZE=$(du -h "$1" | cut -f1)
        echo "  ✅ $1 ($SIZE)"
    else
        echo "  ❌ Thiếu: $1"
    fi
}

check_model "$DEPLOY_DIR/models/yolov8n_plate/yolov8n_plate.onnx"
check_model "$DEPLOY_DIR/models/yolov8/yolov8s.onnx"
check_model "$DEPLOY_DIR/models/scrfd_10g_bnkps.onnx"
check_model "$DEPLOY_DIR/models/glintr100.onnx"

# ── Đóng gói ──────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  ĐÓNG GÓI:"
echo "================================================================"
echo "  File: $DEPLOY_DIR/$ARCHIVE_NAME"
echo ""

cd "$DEPLOY_DIR"
tar -czvf "$ARCHIVE_NAME" \
    --exclude="*.git" \
    --exclude="node_modules" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".venv" \
    --exclude="venv" \
    --exclude="*.tar.gz" \
    .

echo ""
echo "  ✅ Đóng gói hoàn tất!"
echo ""
echo "================================================================"
echo "  HƯỚNG DẪN TRIỂN KHAI:"
echo "================================================================"
echo ""
echo "  1. Copy file lên server:"
echo "     scp $ARCHIVE_NAME user@server:/tmp/"
echo ""
echo "  2. SSH vào server, giải nén:"
echo "     ssh user@server"
echo "     sudo mkdir -p /opt/svpro"
echo "     sudo tar -xzvf /tmp/$ARCHIVE_NAME -C /opt/svpro"
echo "     cd /opt/svpro"
echo ""
echo "  3. Sửa .env (bảo mật):"
echo "     sudo nano .env"
echo "     # Thay đổi: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD"
echo ""
echo "  4. Cài đặt:"
echo "     chmod +x install.sh scripts/*.sh"
echo "     sudo ./install.sh"
echo ""
echo "  5. Kiểm tra:"
echo "     bash scripts/quickstart.sh"
echo ""
