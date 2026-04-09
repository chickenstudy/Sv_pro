#!/bin/bash
# =============================================================================
# SV-PRO Quickstart — Kiểm tra nhanh hệ thống
# =============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

OK="${GREEN}✅${NC}"
FAIL="${RED}❌${NC}"
WARN="${YELLOW}⚠️${NC}"
INFO="${CYAN}ℹ️${NC}"

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║       SV-PRO — Kiểm tra nhanh hệ thống          ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── 1. GPU ─────────────────────────────────────────────────────────────────
echo -n "🖥️  NVIDIA GPU:     "
if command -v nvidia-smi &>/dev/null; then
    GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -c 40)
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1 | awk '{print $1}')
    echo -e "$OK $GPU (${VRAM} MiB)"
else
    echo -e "$FAIL Không tìm thấy GPU"
fi

# ── 2. Docker ───────────────────────────────────────────────────────────────
echo -n "🐳 Docker:         "
if docker ps &>/dev/null; then
    VER=$(docker --version | awk '{print $3}' | tr -d ',')
    echo -e "$OK $VER"
else
    echo -e "$FAIL Docker không chạy"
fi

# ── 3. Containers ──────────────────────────────────────────────────────────
echo ""
echo "📦 Containers đang chạy:"
echo ""

SERVICES=(
    "svpro_backend:savant-ai-core"
    "svpro_backend"
    "svpro_postgres"
    "svpro_redis"
    "svpro_prometheus"
    "svpro_grafana"
    "svpro_redis_exporter"
    "svpro_postgres_exporter"
    "svpro-ingress-manager"
    "sv_pro-savant-ai-core"
)

ALL_RUNNING=0
for name in "${SERVICES[@]}"; do
    IFS=':' read -r container svc <<< "$name"
    STATUS=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null)
    HEALTH=$(docker inspect --format='{{.State.Health}}' "$container" 2>/dev/null)

    if [[ "$STATUS" == "running" ]]; then
        if [[ "$HEALTH" == "healthy" ]]; then
            echo -e "  $OK ${container}"
        else
            echo -e "  $WARN ${container} (running, health: ${HEALTH:-none})"
        fi
    else
        echo -e "  $FAIL ${container} (${STATUS:-not found})"
        ALL_RUNNING=1
    fi
done

# ── 4. HTTP Endpoints ───────────────────────────────────────────────────────
echo ""
echo "🌐 HTTP Endpoints:"
echo ""

check_http() {
    local url=$1
    local name=$2
    local timeout=${3:-5}
    if curl -sf --max-time "$timeout" "$url" > /dev/null 2>&1; then
        echo -e "  $OK $name: $url"
        return 0
    else
        echo -e "  $FAIL $name: $url"
        return 1
    fi
}

check_http "http://localhost:8000/health" "Backend" || true
check_http "http://localhost:8080/health" "AI Core" 10 || true
check_http "http://localhost:9090/-/healthy" "Prometheus" || true
check_http "http://localhost:6379" "Redis (raw)" 2 || true

# ── 5. Database ─────────────────────────────────────────────────────────────
echo ""
echo -n "🗄️  PostgreSQL:      "
if docker exec svpro_postgres pg_isready -U svpro_user -d svpro_db &>/dev/null; then
    echo -e "$OK Connected"
else
    echo -e "$FAIL Không kết nối được"
fi

# ── 6. GPU Usage ────────────────────────────────────────────────────────────
echo ""
echo -n "🔥 GPU:            "
if command -v nvidia-smi &>/dev/null; then
    UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader | head -1 | awk '{print $1}')
    echo -e "$OK GPU Usage: ${UTIL}%"
else
    echo -e "$WARN Không có nvidia-smi"
fi

# ── 7. Xem logs ────────────────────────────────────────────────────────────
echo ""
echo "───────────────────────────────────────────────"
echo "📄 Xem logs nhanh:"
echo "   AI Core:     docker compose logs -f savant-ai-core"
echo "   Backend:     docker compose logs -f backend"
echo "   Ingress:     docker compose logs -f ingress-manager"
echo "   Tất cả:     docker compose logs -f"
echo ""
echo "🔄 Restart:"
echo "   docker compose restart"
echo "   docker compose restart savant-ai-core  # chỉ AI core"
echo ""
echo "⏹️  Dừng:"
echo "   docker compose down"
echo "   docker compose down -v  # xóa data"
echo "───────────────────────────────────────────────"
echo ""
