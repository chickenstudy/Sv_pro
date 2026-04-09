#!/bin/bash
# =============================================================================
# Download AI Models cho SV-PRO
# =============================================================================
# Models nặng không đi kèm source code. Script này giúp tải hoặc
# hướng dẫn đặt các file model vào đúng vị trí.
# =============================================================================

set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/svpro}"
MODELS_DIR="$INSTALL_DIR/models"

echo "================================================================"
echo "  SV-PRO — Tải Models AI"
echo "================================================================"
echo

# ── Tạo thư mục ──────────────────────────────────────────────────────────
mkdir -p "$MODELS_DIR"
mkdir -p "$MODELS_DIR/yolov8n_plate"
mkdir -p "$MODELS_DIR/yolov8"
mkdir -p "$MODELS_DIR/anti_spoof"
mkdir -p "$MODELS_DIR/buffalo_l"

# ── Hàm kiểm tra file đã tồn tại ─────────────────────────────────────────
check_file() {
    if [[ -f "$1" ]]; then
        SIZE=$(du -h "$1" | cut -f1)
        echo "  ✅ $1 ($SIZE)"
        return 0
    else
        echo "  ❌ Thiếu: $1"
        return 1
    fi
}

# ── Kiểm tra các model hiện có ────────────────────────────────────────────
echo "📦 Kiểm tra models hiện có..."
echo

MISSING=0

check_file "$MODELS_DIR/scrfd_10g_bnkps.onnx" || MISSING=1
check_file "$MODELS_DIR/glintr100.onnx" || MISSING=1
check_file "$MODELS_DIR/anti_spoof/minifasnet.onnx" || MISSING=1
check_file "$MODELS_DIR/yolov8/yolov8s.onnx" || MISSING=1
check_file "$MODELS_DIR/yolov8/yolov8s_config_savant.txt" || MISSING=1
check_file "$MODELS_DIR/yolov8n_plate/yolov8n_plate.onnx" || MISSING=1

echo
if [[ $MISSING -eq 0 ]]; then
    echo "✅ Tất cả models đã có sẵn!"
    exit 0
fi

# ── Hướng dẫn tải từng model ─────────────────────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "  HƯỚNG DẪN TẢI MODELS"
echo "────────────────────────────────────────────────────────────────"
echo
echo "⚠️  Các model lớn (>10MB) KHÔNG đi kèm source code."
echo "   Bạn cần tải thủ công hoặc copy từ máy dev."
echo
echo "📂 Thư mục đích: $MODELS_DIR"
echo
echo "────────────────────────────────────────────────────────────────"
echo

# YOLOv8s (phát hiện phương tiện)
echo "1️⃣  YOLOv8s — Phát hiện xe (Vehicle Detection)"
echo "   File: models/yolov8/yolov8s.onnx"
echo "   Kích thước: ~50 MB"
echo
echo "   Cách 1: Tải bằng Python (ultralytics)"
echo "     pip install ultralytics"
echo "     python -c \"from ultralytics import YOLO; m = YOLO('yolov8s.pt'); m.export(format='onnx')\""
echo
echo "   Cách 2: Copy từ máy dev"
echo "     # Trên máy dev: ls models/yolov8/"
echo "     scp -r user@dev:~/sv-pro/models/yolov8/ $MODELS_DIR/yolov8/"
echo

# YOLOv8n Plate (phát hiện biển số VN)
echo "2️⃣  YOLOv8n Plate — Phát hiện biển số (Fine-tuned cho Việt Nam)"
echo "   File: models/yolov8n_plate/yolov8n_plate.onnx"
echo "   Kích thước: ~12 MB"
echo
echo "   ✅ ĐÃ CÓ SẴN trong bộ cài đặt — KHÔNG CẦN TẢI THÊM!"
echo "   Nếu bạn có model mới hơn:"
echo "     cp /đường/dẫn/yolov8n_plate.onnx $MODELS_DIR/yolov8n_plate/"
echo

# SCRFD
echo "3️⃣  SCRFD-10GF — Nhận diện khuôn mặt (Face Detection)"
echo "   File: models/scrfd_10g_bnkps.onnx"
echo "   Kích thước: ~170 MB"
echo
echo "   Tải từ:"
echo "   https://github.com/onnx/models/tree/main/vision/body_analysis/arcface"
echo "   (tìm file scrfd_10g_bnkps.onnx)"
echo
echo "   Hoặc tìm trên Google: scrfd_10g_bnkps.onnx download"
echo

# ArcFace
echo "4️⃣  ArcFace R100 — Embedding khuôn mặt (Face Recognition)"
echo "   File: models/glintr100.onnx"
echo "   Kích thước: ~90 MB"
echo
echo "   Tải từ:"
echo "   https://github.com/onnx/models/tree/main/vision/body_analysis/arcface"
echo "   (tìm file w600k_r50.onnx hoặc glintr100.onnx)"
echo

# AntiSpoof
echo "5️⃣  MiniFASNet — Chống giả mạo khuôn mặt (Anti-Spoofing)"
echo "   File: models/anti_spoof/minifasnet.onnx"
echo "   Kích thước: ~1-5 MB"
echo
echo "   Tìm trong repository insight-platform:"
echo "   https://github.com/insight-platform/savant"
echo

echo "────────────────────────────────────────────────────────────────"
echo
echo "📋 CHECKLIST trước khi chạy hệ thống:"
echo
echo "   [ ] models/yolov8/yolov8s.onnx"
echo "   [ ] models/yolov8/yolov8s_config_savant.txt"
echo "   [ ] models/yolov8n_plate/yolov8n_plate.onnx"
echo "   [ ] models/scrfd_10g_bnkps.onnx"
echo "   [ ] models/glintr100.onnx"
echo "   [ ] models/anti_spoof/minifasnet.onnx"
echo
echo "   Kiểm tra: ls $MODELS_DIR/**/*.onnx"
echo
