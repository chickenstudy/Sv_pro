#!/usr/bin/env python3
"""
Script 1-click Setup & Deploy cho SV-PRO.

Bước chạy:
  python scripts/setup.py

Wizard sẽ hỏi từng bước:
  1. Kiểm tra prerequisites (Docker, nvidia-smi).
  2. Nhập cấu hình (RTSP URLs, DB password, Telegram token, API key).
  3. Tạo file .env từ .env.example.
  4. Thêm bảng cameras vào DB schema nếu chưa có.
  5. Tải model (gọi download_models.py).
  6. Khởi động docker compose.
  7. Kiểm tra health check tự động.

Chạy không cần Docker (dev mode):
  python scripts/setup.py --no-docker
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # gốc dự án sv-pro/
ENV_FILE     = ROOT / ".env"
ENV_EXAMPLE  = ROOT / ".env.example"


# ── Màu sắc terminal ────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"


def ok(msg):   print(f"{C.GREEN}  ✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}  ⚠️  {msg}{C.RESET}")
def err(msg):  print(f"{C.RED}  ❌ {msg}{C.RESET}")
def info(msg): print(f"{C.CYAN}  ℹ️  {msg}{C.RESET}")
def header(msg): print(f"\n{C.BOLD}{C.CYAN}{'='*60}\n{msg}\n{'='*60}{C.RESET}")


def ask(prompt: str, default: str = "") -> str:
    """Hỏi người dùng với giá trị mặc định hiện trong ngoặc."""
    full_prompt = f"  {prompt}"
    if default:
        full_prompt += f" [{default}]"
    full_prompt += ": "
    ans = input(full_prompt).strip()
    return ans if ans else default


def ask_yn(prompt: str, default: bool = True) -> bool:
    """Hỏi Yes/No, trả về bool."""
    yn = "Y/n" if default else "y/N"
    ans = input(f"  {prompt} ({yn}): ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


# ── Bước 1: Kiểm tra prerequisites ────────────────────────────────────────────

def check_prerequisites(no_docker: bool) -> bool:
    """
    Kiểm tra các công cụ bắt buộc đã được cài đặt chưa:
      - Python 3.10+
      - Docker & docker compose (nếu không phải no_docker mode)
      - nvidia-smi (cảnh báo nếu không có)
    """
    header("Bước 1: Kiểm tra Prerequisites")
    all_ok = True

    # Python version
    pv = sys.version_info
    if pv >= (3, 10):
        ok(f"Python {pv.major}.{pv.minor}.{pv.micro}")
    else:
        err(f"Python {pv.major}.{pv.minor} — Cần Python 3.10+")
        all_ok = False

    if not no_docker:
        # Docker
        if shutil.which("docker"):
            try:
                out = subprocess.check_output(["docker", "--version"], text=True).strip()
                ok(f"Docker: {out}")
            except Exception:
                err("Docker có nhưng không chạy được")
                all_ok = False
        else:
            err("Docker chưa cài đặt. Tải tại: https://docs.docker.com/get-docker/")
            all_ok = False

        # docker compose
        try:
            subprocess.check_output(["docker", "compose", "version"], text=True)
            ok("Docker Compose: OK")
        except Exception:
            err("docker compose không khả dụng")
            all_ok = False

    # nvidia-smi (không bắt buộc nhưng cảnh báo)
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], text=True).strip()
            ok(f"NVIDIA GPU: {out}")
        except Exception:
            warn("nvidia-smi có nhưng không đọc được thông tin GPU")
    else:
        warn("nvidia-smi không tìm thấy — hệ thống sẽ chạy CPU-only (ảnh hưởng hiệu năng)")

    return all_ok


# ── Bước 2: Thu thập cấu hình ────────────────────────────────────────────────

def collect_config() -> dict:
    """
    Hỏi người dùng các thông số cấu hình cần thiết để tạo file .env.
    Giá trị mặc định được đọc từ .env cũ (nếu có) để tránh nhập lại.
    """
    header("Bước 2: Cấu hình hệ thống")

    # Đọc giá trị cũ nếu .env đã tồn tại
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
        info(f"Đọc cấu hình cũ từ {ENV_FILE}")

    cfg: dict[str, str] = {}

    print()
    info("Database (PostgreSQL)")
    cfg["POSTGRES_USER"]     = ask("DB Username", existing.get("POSTGRES_USER", "svpro_user"))
    cfg["POSTGRES_PASSWORD"] = ask("DB Password", existing.get("POSTGRES_PASSWORD", "svpro_pass"))
    cfg["POSTGRES_DB"]       = ask("DB Name",     existing.get("POSTGRES_DB", "svpro_db"))
    cfg["POSTGRES_DSN"] = (
        f"postgresql://{cfg['POSTGRES_USER']}:{cfg['POSTGRES_PASSWORD']}"
        f"@postgres:5432/{cfg['POSTGRES_DB']}"
    )

    print()
    info("Redis")
    cfg["REDIS_HOST"] = ask("Redis Host", existing.get("REDIS_HOST", "redis"))
    cfg["REDIS_PORT"] = ask("Redis Port", existing.get("REDIS_PORT", "6379"))
    cfg["REDIS_DB"]   = ask("Redis DB",   existing.get("REDIS_DB",   "0"))

    print()
    info("Telegram Alert (bỏ trống nếu chưa có)")
    cfg["TELEGRAM_BOT_TOKEN"] = ask("Telegram Bot Token", existing.get("TELEGRAM_BOT_TOKEN", ""))
    cfg["TELEGRAM_CHAT_ID"]   = ask("Telegram Chat ID",   existing.get("TELEGRAM_CHAT_ID", ""))

    print()
    info("Auth & Security")
    import secrets
    cfg["JWT_SECRET"]        = ask("JWT Secret (auto-gen OK)", existing.get("JWT_SECRET", secrets.token_hex(32)))
    cfg["INTERNAL_API_KEY"]  = ask("Internal API Key",         existing.get("INTERNAL_API_KEY", secrets.token_hex(16)))
    cfg["ADMIN_USERNAME"]    = ask("Admin Username",            existing.get("ADMIN_USERNAME", "admin"))
    cfg["ADMIN_PASSWORD"]    = ask("Admin Password",            existing.get("ADMIN_PASSWORD", "svpro2024"))

    print()
    info("Face Recognition")
    cfg["FR_RECOGNITION_THRESHOLD"] = ask("FR Threshold (0.0-1.0)", existing.get("FR_RECOGNITION_THRESHOLD", "0.55"))
    cfg["FR_ENABLE_ANTI_SPOOF"]     = ask("Anti-spoof (true/false)", existing.get("FR_ENABLE_ANTI_SPOOF", "true"))

    print()
    info("Model Paths (bên trong container)")
    cfg["SCRFD_MODEL_PATH"]      = ask("SCRFD model path",     existing.get("SCRFD_MODEL_PATH", "/models/scrfd_10g_bnkps.onnx"))
    cfg["ARCFACE_MODEL_PATH"]    = ask("ArcFace model path",   existing.get("ARCFACE_MODEL_PATH", "/models/glintr100.onnx"))
    cfg["ANTI_SPOOF_MODEL_PATH"] = ask("MiniFASNet model path",existing.get("ANTI_SPOOF_MODEL_PATH", "/models/anti_spoof/minifasnet.onnx"))
    cfg["PLATE_MODEL_PATH"]      = ask("Plate model path",     existing.get("PLATE_MODEL_PATH", "/models/yolov8s_plate/yolov8s_plate.onnx"))
    cfg["CORS_ORIGINS"]          = ask("CORS origins",         existing.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"))

    info("Monitoring (Grafana) & Frontend")
    cfg["GF_ADMIN_USER"]     = ask("Grafana Admin User", existing.get("GF_ADMIN_USER", "admin"))
    cfg["GF_ADMIN_PASS"]     = ask("Grafana Admin Pass", existing.get("GF_ADMIN_PASS", secrets.token_urlsafe(12)))
    cfg["VITE_API_URL"]      = ask("Frontend API URL",   existing.get("VITE_API_URL", "http://localhost:8000"))

    return cfg


# ── Bước 3: Ghi .env ─────────────────────────────────────────────────────────

def write_env(cfg: dict) -> None:
    """Ghi cấu hình vào file .env. Tạo backup .env.bak nếu file cũ đã tồn tại."""
    header("Bước 3: Ghi file .env")
    if ENV_FILE.exists():
        bak = ENV_FILE.with_suffix(".bak")
        shutil.copy(ENV_FILE, bak)
        info(f"Backup file cũ → {bak}")

    lines = ["# SV-PRO Environment Configuration (auto-generated by setup.py)", ""]
    for k, v in cfg.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ok(f"Đã ghi {ENV_FILE}")

# ── Bước 3.5: Tạo các thư mục lưu trữ ────────────────────────────────────────

def create_directories() -> None:
    """Tạo các thư mục bắt buộc như logs, output, models."""
    header("Bước 3.5: Khởi tạo thư mục")
    dirs = [
        "output/faces", 
        "output/plates", 
        "logs", 
        "models", 
        "monitoring/grafana/provisioning",
    ]
    for d in dirs:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    
    # Phân quyền cho Grafana ghi file nếu cần (Docker container Grafana thường chạy với UID 472)
    if sys.platform != "win32":
        try:
            subprocess.run("chmod -R 777 output logs", shell=True, cwd=ROOT, check=False)
        except Exception:
            pass
    ok("Đã tạo các thư mục thành công.")



# ── Bước 4: Tải models ────────────────────────────────────────────────────────

def download_models() -> None:
    """Gọi scripts/download_models.py để tải model ONNX nếu chưa có."""
    header("Bước 4: Tải Models")
    script = ROOT / "scripts" / "download_models.py"
    if not script.exists():
        warn("download_models.py không tìm thấy — bỏ qua")
        return

    if not ask_yn("Tải model ONNX ngay bây giờ?", default=True):
        warn("Bỏ qua — nhớ tải model trước khi chạy pipeline!")
        return

    result = subprocess.run([sys.executable, str(script)], cwd=ROOT)
    if result.returncode == 0:
        ok("Tải model hoàn thành!")
    else:
        warn("Tải model gặp lỗi — kiểm tra kết nối internet")


# ── Bước 5: Docker Compose ────────────────────────────────────────────────────

def start_docker() -> None:
    """Chạy docker compose up --build -d và theo dõi log health check."""
    header("Bước 5: Khởi động Docker Compose")

    compose_file = ROOT / "docker-compose.yml"
    if not compose_file.exists():
        err("docker-compose.yml không tìm thấy!")
        return

    print("  Đang build và khởi động containers...")
    result = subprocess.run(
        ["docker", "compose", "up", "--build", "-d"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        err("docker compose up thất bại!")
        return
    ok("Containers đã khởi động!")

    # Health check
    info("Chờ 10 giây để services ổn định...")
    time.sleep(10)
    _health_check()


def _health_check() -> None:
    """Gọi /health endpoint để xác nhận backend đã sẵn sàng."""
    url = "http://localhost:8000/health"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    ok(f"Backend API sẵn sàng: {url}")
                    info("Swagger UI: http://localhost:8000/docs")
                    return
        except Exception:
            pass
        time.sleep(3)
        print(f"  Đang chờ... (thử lần {attempt + 2}/5)")
    warn("Backend chưa phản hồi sau 5 lần thử. Kiểm tra: docker compose logs backend")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Hàm chính: chạy toàn bộ wizard 1-click setup."""
    import argparse
    parser = argparse.ArgumentParser(description="SV-PRO 1-click Setup Wizard")
    parser.add_argument("--no-docker", action="store_true", help="Bỏ qua bước Docker")
    parser.add_argument("--skip-models", action="store_true", help="Bỏ qua tải model")
    parser.add_argument("--skip-config", action="store_true", help="Bỏ qua nhập cấu hình (dùng .env cũ)")
    args = parser.parse_args()

    print(f"\n{C.BOLD}{C.CYAN}")
    print("  ╔══════════════════════════════════════╗")
    print("  ║    SV-PRO 1-click Setup Wizard       ║")
    print("  ║    Sprint 5 — v1.0                   ║")
    print("  ╚══════════════════════════════════════╝")
    print(f"{C.RESET}")

    # Bước 1: Prerequisites
    if not check_prerequisites(args.no_docker):
        err("Một số prerequisites chưa thỏa mãn. Vui lòng khắc phục rồi chạy lại.")
        sys.exit(1)

    # Bước 2 + 3: Cấu hình
    if not args.skip_config:
        cfg = collect_config()
        write_env(cfg)
    else:
        if not ENV_FILE.exists():
            err(".env không tồn tại. Hãy bỏ --skip-config.")
            sys.exit(1)
        info("Dùng .env hiện có.")

    # Bước 3.5: Tạo folder
    create_directories()

    # Bước 4: Models
    if not args.skip_models:
        download_models()

    # Bước 5: Docker
    if not args.no_docker:
        start_docker()
    else:
        info("Chế độ --no-docker: bỏ qua bước Docker.")
        info("Chạy thủ công: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload")

    print(f"\n{C.GREEN}{C.BOLD}🎉 SV-PRO Setup hoàn thành!{C.RESET}")
    print(f"  Dashboard:   http://localhost:3000")
    print(f"  API Swagger: http://localhost:8000/docs")
    print(f"  Grafana:     http://localhost:3001\n")


if __name__ == "__main__":
    main()
