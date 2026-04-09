#!/bin/bash
# =============================================================================
# SV-PRO Installer — Cài đặt một-click cho Linux server
# =============================================================================
# Hướng dẫn: chmod +x install.sh && ./install.sh
# Yêu cầu: Ubuntu 20.04+ / Debian 11+, NVIDIA GPU, Docker + NVIDIA Docker runtime
# =============================================================================

set -e

# ── Màu sắc terminal ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ── Thư mục cài đặt mặc định ────────────────────────────────────────────────
DEFAULT_INSTALL_DIR="/opt/svpro"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Hàm in ────────���──────────────────────────────────────────────────────────
print_step() { echo -e "${CYAN}[1/6]${NC} $1"; }
print_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
print_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_fail()  { echo -e "${RED}[FAIL]${NC} $1"; }
print_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ask()        { echo -ne "${YELLOW}[HỎI]${NC} $1"; }

# ── Banner ───────────────────────────────────────────────────────────────────
banner() {
    cat << 'EOF'
    ╔═══════════════════════════════════════════════════════════╗
    ║           SV-PRO — Hệ thống AI Surveillance            ║
    ║            Cài đặt tự động cho Linux Server            ║
    ╚═══════════════════════════════════════════════════════════╝
EOF
    echo
}

# ── Kiểm tra quyền root ────────────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_fail "Cần chạy với quyền root! Dùng: sudo $0"
        exit 1
    fi
}

# ── Kiểm tra kết nối internet ────────────────────────────────────────────────────
check_internet() {
    print_step "Kiểm tra kết nối internet..."
    if curl -s --max-time 5 https://registry.hub.docker.com > /dev/null 2>&1; then
        print_ok "Kết nối internet OK"
    else
        print_warn "Không có internet — có thể cần cài offline"
    fi
}

# ── Kiểm tra hệ điều hành ──────────────────────────────────────────────────────
check_os() {
    print_step "Kiểm tra hệ điều hành..."
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS="$ID"
        VER="$VERSION_ID"
        print_ok "OS: $PRETTY_NAME"
        if [[ "$OS" == "ubuntu" ]] || [[ "$OS" == "debian" ]]; then
            print_ok "Hệ điều hành được hỗ trợ"
        else
            print_warn "Chỉ kiểm tra trên Ubuntu/Debian"
        fi
    else
        print_fail "Không xác định được hệ điều hành"
        exit 1
    fi
}

# ── Kiểm tra Docker ─────────────────────────────────────────────────────────────
check_docker() {
    print_step "Kiểm tra Docker..."
    if command -v docker &> /dev/null; then
        DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
        print_ok "Docker $DOCKER_VER đã cài"
    else
        print_fail "Docker chưa được cài đặt!"
        echo
        echo "👉 Cài đặt Docker:"
        echo "   curl -fsSL https://get.docker.com | sudo sh"
        echo "   sudo systemctl enable docker"
        echo "   sudo usermod -aG docker \$USER"
        exit 1
    fi
}

# ── Kiểm tra NVIDIA GPU ─────────────────────────────────────────────────────────
check_gpu() {
    print_step "Kiểm tra NVIDIA GPU..."

    if ! command -v nvidia-smi &> /dev/null; then
        print_fail "nvidia-smi không tìm thấy!"
        echo
        echo "👉 Cài đặt NVIDIA Driver:"
        echo "   # Ubuntu"
        echo "   sudo add-apt-repository ppa:graphics-drivers/ppa"
        echo "   sudo apt update"
        echo "   sudo apt install -y nvidia-driver-535 nvidia-dkms-535"
        echo "   sudo reboot"
        echo
        echo "   # Kiểm tra sau reboot: nvidia-smi"
        exit 1
    fi

    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1 | awk '{print $1}')
    CUDA_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
    print_ok "GPU: $GPU_NAME (${GPU_MEM} MiB, Driver $CUDA_VER)"

    if [[ $GPU_MEM -lt 4000 ]]; then
        print_warn "GPU VRAM < 4GB — có thể chạy chậm, nên dùng FP16"
    fi
}

# ── Kiểm tra NVIDIA Docker runtime ─────────────────────────────────────────────
check_nvidia_docker() {
    print_step "Kiểm tra NVIDIA Docker runtime..."
    if docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 \
        nvidia-smi --query-gpu=name --format=csv,noheader &> /dev/null; then
        print_ok "NVIDIA Docker runtime OK"
    else
        print_fail "NVIDIA Docker runtime chưa được cài đặt!"
        echo
        echo "👉 Cài đặt NVIDIA Container Toolkit:"
        echo "   distribution=\$(. /etc/os-release;echo \$ID\$VERSION_ID)"
        echo "   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
        echo "   curl -s -A \"\$distribution\" https://nvidia.github.io/libnvidia-container/container.deb.list | sudo sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list"
        echo "   sudo apt-get update"
        echo "   sudo apt-get install -y nvidia-container-toolkit"
        echo "   sudo systemctl restart docker"
        echo "   sudo nvidia-ctk runtime configure --runtime=docker"
        echo "   sudo systemctl restart docker"
        exit 1
    fi
}

# ── Kiểm tra RAM ────────────────────────────────────────────────────────────────
check_ram() {
    print_step "Kiểm tra RAM..."
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
    print_info "RAM: ${RAM_GB} GB"
    if [[ $RAM_GB -lt 8 ]]; then
        print_warn "RAM < 8GB — có thể không đủ cho AI pipeline"
    fi
}

# ── Kiểm tra ổ đĩa ──────────────────────────────────────────────────────────────
check_disk() {
    print_step "Kiểm tra dung lượng đĩa..."
    AVAILABLE_KB=$(df "$DEFAULT_INSTALL_DIR" 2>/dev/null | tail -1 | awk '{print $4}')
    AVAILABLE_GB=$((AVAILABLE_KB / 1024 / 1024))
    if [[ $AVAILABLE_GB -lt 20 ]]; then
        print_warn "Dung lượng trống < 20GB — cần ít nhất 20GB cho models + data"
    else
        print_ok "Dung lượng trống: ${AVAILABLE_GB} GB"
    fi
}

# ── Chọn thư mục cài đặt ────────────────────────────────────────────────────────
choose_install_dir() {
    ask "Thư mục cài đặt [mặc định: $DEFAULT_INSTALL_DIR]: "
    read -r USER_DIR
    if [[ -n "$USER_DIR" ]]; then
        INSTALL_DIR="$USER_DIR"
    else
        INSTALL_DIR="$DEFAULT_INSTALL_DIR"
    fi

    if [[ -d "$INSTALL_DIR" ]]; then
        ask "Thư mục đã tồn tại. Ghi đè? (y/n): "
        read -r OVERWRITE
        if [[ "$OVERWRITE" != "y" ]]; then
            print_info "Thoát. Chọn thư mục khác hoặc xóa thư mục hiện tại."
            exit 0
        fi
    fi
}

# ── Tạo thư mục ───────────────────────────────────────────────────────────────────
create_dirs() {
    print_step "Tạo thư mục cài đặt..."
    mkdir -p "$INSTALL_DIR"/{models,models/yolov8n_plate,Detect/plates,Detect/faces,Detect/audit,scripts,module}
    print_ok "Thư mục: $INSTALL_DIR"
}

# ── Copy file ────────────────────────────────────────────────────────────────────
copy_files() {
    print_step "Copy file vào thư mục cài đặt..."

    # Các file cần copy
    local files=(
        "docker-compose.yml"
        "Dockerfile.backend"
        "Dockerfile.savant-ai-core"
        "Dockerfile.ingress-manager"
        "module/module.yml"
        "backend/"
        "src/"
        "scripts/sql/"
        "scripts/backup.sh"
        "monitoring/prometheus.yml"
        "monitoring/grafana/provisioning/dashboards/dashboard.yml"
        "tracker/config_tracker_NvSORT.yml"
        "requirements.txt"
    )

    for item in "${files[@]}"; do
        if [[ -e "$SCRIPT_DIR/$item" ]]; then
            cp -r "$SCRIPT_DIR/$item" "$INSTALL_DIR/$item" 2>/dev/null || true
        fi
    done

    # Copy toàn bộ models (nếu có)
    if [[ -d "$SCRIPT_DIR/models" ]]; then
        print_info "Copy models..."
        cp -rn "$SCRIPT_DIR/models/"* "$INSTALL_DIR/models/" 2>/dev/null || true
        print_ok "Models đã copy (bao gồm yolov8n_plate fine-tuned VN)"
    fi

    print_ok "Đã copy tất cả file"
}

# ── Tạo .env production ────────────────────────────────────────────────────���────
create_env() {
    print_step "Tạo file cấu hình .env..."

    cat > "$INSTALL_DIR/.env" << 'ENVCFG'
# ── PostgreSQL Config ────────────────────────────────────────────────────────
POSTGRES_USER=svpro_user
POSTGRES_PASSWORD=svpro_pass
POSTGRES_DB=svpro_db
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DSN=postgresql://svpro_user:svpro_pass@postgres:5432/svpro_db

# ── Redis Config ──────────────────────────────────────────────────────────────
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# ── Auth / Security ──────────────────────────────────────────────────────────
# ⚠️  THAY ĐỔI CÁC GIÁ TRỊ NÀY TRONG PRODUCTION!
JWT_SECRET=change-me-in-production-use-long-random-string
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440
API_KEY_AI_CORE=svpro-ai-core-api-key-change-in-prod
ADMIN_PASSWORD=svpro2024

# ── CORS ────────────────────────────────────────────────────────────────��──
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# ── Telegram Alerts ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
ALERT_WEBHOOK_URL=

# ── Face Recognition Thresholds ─────────────────────────────────────────────
FR_RECOGNITION_THRESHOLD=0.55
FR_ENABLE_ANTI_SPOOF=true
FR_SPOOF_THRESHOLD=0.85

# ── Model Paths (container paths) ───────────────────────────────────────────
SCRFD_MODEL_PATH=/models/scrfd_10g_bnkps.onnx
ARCFACE_MODEL_PATH=/models/glintr100.onnx
ANTI_SPOOF_MODEL_PATH=/models/anti_spoof/minifasnet.onnx
PLATE_MODEL_PATH=/models/yolov8n_plate/yolov8n_plate.onnx

# ── Monitoring ─────────────────────────────────────────────────────────────
PROMETHEUS_URL=http://prometheus:9090
GF_ADMIN_USER=admin
GF_ADMIN_PASS=svpro2024

# ── Watchdog ────────────────────────────────────────────────────────────────
WATCHDOG_ENABLED=true
WATCHDOG_CHECK_INTERVAL_SECS=30
WATCHDOG_STUCK_THRESHOLD_SECS=120
WATCHDOG_MAX_RESTARTS_PER_WINDOW=3
ENVCFG

    print_ok ".env đã tạo tại $INSTALL_DIR/.env"
    print_warn "⚠️  NHỚ THAY ĐỔI: JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD!"
}

# ── Tải models ────────────────────────────────────────────────────────────────
download_models() {
    print_step "Hướng dẫn tải Models AI..."
    echo
    echo "──────────────────────────────────────────────────────────────"
    echo "  CÁC MODEL CẦN THIẾT:"
    echo "──────────────────────────────────────────────────────────────"
    echo
    echo "  1. YOLOv8s (phát hiện phương tiện):"
    echo "     → Tải từ Ultralytics: yolov8s.onnx"
    echo "     → Đặt vào: $INSTALL_DIR/models/yolov8/"
    echo
    echo "  2. YOLOv8n Plate (phát hiện biển số):"
    echo "     → Sử dụng model đã fine-tune cho Việt Nam"
    echo "     → Hoặc tải yolov8n từ Ultralytics rồi fine-tune"
    echo "     → Đặt vào: $INSTALL_DIR/models/yolov8n_plate/yolov8n_plate.onnx"
    echo
    echo "  3. SCRFD-10GF (nhận diện khuôn mặt):"
    echo "     → Tải: https://github.com/onnx/models/tree/main/vision/body_analysis/arcface"
    echo "     → Đặt vào: $INSTALL_DIR/models/scrfd_10g_bnkps.onnx"
    echo
    echo "  4. ArcFace R100 (embedding khuôn mặt):"
    echo "     → Tải: https://github.com/onnx/models/tree/main/vision/body_analysis/arcface"
    echo "     → Đặt vào: $INSTALL_DIR/models/glintr100.onnx"
    echo
    echo "  5. MiniFASNet (chống giả mạo):"
    echo "     → Tải: https://github.com/onnx/models/tree/main/vision/body_analysis/arcface"
    echo "     → Đặt vào: $INSTALL_DIR/models/anti_spoof/minifasnet.onnx"
    echo
    echo "─────────────────────────────────────────────────────────��────"
    ask "Đã tải tất cả models? (y/n): "
    read -r CONFIRM
    if [[ "$CONFIRM" != "y" ]]; then
        print_info "Có thể tải sau bằng: bash $SCRIPT_DIR/scripts/download-models.sh"
    fi
}

# ── Build Docker images ───────────────────────────────────────────────────────────
build_images() {
    print_step "Build Docker images (có thể mất 10-20 phút)..."

    cd "$INSTALL_DIR"

    # Build tất cả images song song
    print_info "Build backend..."
    docker compose build backend &
    PID_BACKEND=$!

    print_info "Build savant-ai-core..."
    docker compose build savant-ai-core &
    PID_AI=$!

    print_info "Build ingress-manager..."
    docker compose build ingress-manager &
    PID_INGRESS=$!

    # Đợi tất cả
    wait $PID_BACKEND
    wait $PID_AI
    wait $PID_INGRESS

    print_ok "Build hoàn tất!"
}

# ── Khởi động services ─────────────────────────────────────────────────────────
start_services() {
    print_step "Khởi động services..."

    cd "$INSTALL_DIR"

    # Tạo network trước
    docker network create svpro_net 2>/dev/null || true

    # Khởi động infrastructure trước
    docker compose up -d postgres redis

    # Đợi postgres healthy
    print_info "Đợi PostgreSQL khởi động (10s)..."
    sleep 10

    # Khởi động toàn bộ
    docker compose up -d

    print_ok "Đã khởi động toàn bộ services!"
}

# ── Kiểm tra sức khỏe ──────────────────────────────────────────────────────────
health_check() {
    print_step "Kiểm tra sức khỏe hệ thống..."
    echo

    local all_ok=true

    # Backend
    sleep 5
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        print_ok "Backend:     http://localhost:8000 ✅"
    else
        print_fail "Backend:     http://localhost:8000 ❌"
        all_ok=false
    fi

    # AI Core
    if curl -sf --max-time 5 http://localhost:8080/health > /dev/null 2>&1; then
        print_ok "AI Core:     http://localhost:8080 ✅"
    else
        print_warn "AI Core:     http://localhost:8080 (đang khởi động...)"
    fi

    # PostgreSQL
    if docker exec svpro_postgres pg_isready -U svpro_user -d svpro_db &>/dev/null; then
        print_ok "PostgreSQL:  port 5432 ✅"
    else
        print_fail "PostgreSQL:  port 5432 ❌"
        all_ok=false
    fi

    # Redis
    if docker exec svpro_redis redis-cli ping 2>/dev/null | grep -q PONG; then
        print_ok "Redis:       port 6379 ✅"
    else
        print_fail "Redis:       port 6379 ❌"
        all_ok=false
    fi

    # Prometheus
    if curl -sf http://localhost:9090/-/healthy > /dev/null 2>&1; then
        print_ok "Prometheus:  http://localhost:9090 ✅"
    else
        print_warn "Prometheus:  http://localhost:9090 (đang khởi động...)"
    fi

    echo
    echo "──────────────────────────────────────────────────────────────"
    echo "  TRUY CẬP HỆ THỐNG:"
    echo "──────────────────────────────────────────────────────────────"
    echo "  🌐 Backend API:    http://localhost:8000"
    echo "  🌐 AI Core:        http://localhost:8080"
    echo "  🌐 Prometheus:      http://localhost:9090"
    echo "  🌐 Grafana:         http://localhost:3001"
    echo "  👤 Tài khoản:      admin / svpro2024"
    echo
    echo "  📁 Thư mục cài đặt: $INSTALL_DIR"
    echo "  📄 Logs:            docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
    echo "──────────────────────────────────────────────────────────────"
}

# ── MAIN ──────────────────────────────────────────────────────────────────────
main() {
    banner

    echo -e "${BOLD}CÀI ĐẶT TỰ ĐỘNG SV-PRO${NC}"
    echo "Yêu cầu: Ubuntu 20.04+ / Debian 11+, NVIDIA GPU (RTX 3060 trở lên)"
    echo "──────────────────────────────────────────────────────────────"
    echo

    check_root
    check_internet
    check_os
    check_docker
    check_gpu
    check_nvidia_docker
    check_ram
    check_disk
    choose_install_dir
    create_dirs
    copy_files
    create_env
    download_models

    echo
    echo "──────────────────────────────────────────────────────────────"
    ask "Bắt đầu build và khởi động? (y/n): "
    read -r START
    if [[ "$START" == "y" ]]; then
        build_images
        start_services
        health_check
    else
        print_info "Đã bỏ qua build. Khởi động sau: cd $INSTALL_DIR && docker compose up -d"
    fi

    echo
    print_ok "Cài đặt hoàn tất!"
    echo "📖 Đọc README: cat $INSTALL_DIR/README_VI.md"
}

main "$@"
