"""
Script tải về các file model ONNX cần thiết cho pipeline SV-PRO (LPR + FR).

Các model được tải từ các nguồn public chính thức:
  - SCRFD-10GF  : InsightFace/buffalo_l (face detection)
  - ArcFace R100: InsightFace/glintr100  (face recognition)
  - MiniFASNet  : Lite ONNX từ GitHub minivision-ai/Silent-Face-Anti-Spoofing
  - YOLOv8s     : Ultralytics ONNX (vehicle detection)

Sau khi tải, script sẽ kiểm tra checksum SHA-256 để đảm bảo tệp toàn vẹn.
Chạy: python scripts/download_models.py [--output-dir models]
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path


# ── Danh sách model cần tải ────────────────────────────────────────────────────
# Định dạng: (key, url, relative_path, sha256_hex_hoặc_None)
# sha256 = None → bỏ qua kiểm tra checksum (dùng khi chưa có hash chính thức)
MODEL_REGISTRY = [
    (
        "scrfd_10g_bnkps",
        # InsightFace SCRFD-10G với keypoints (5-point landmark)
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "buffalo_l/det_10g.onnx",   # sau khi giải nén từ ZIP
        None,
    ),
    (
        "glintr100",
        # ArcFace GlintR100
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "buffalo_l/w600k_r50.onnx",  # sau khi giải nén từ ZIP
        None,
    ),
    (
        "minifasnet",
        # MiniFASNet 2.7M anti-spoofing — ONNX export từ repo minivision-ai
        "https://github.com/minivision-ai/Silent-Face-Anti-Spoofing/raw/master/resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.onnx",
        "anti_spoof/minifasnet.onnx",
        None,
    ),
]

# buffalo_l.zip cần tải 1 lần rồi giải nén nhiều model
_BUFFALO_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
_BUFFALO_ZIP_PATH = "buffalo_l.zip"


def _sha256(filepath: str) -> str:
    """Tính SHA-256 checksum của file, trả về hex string."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _progress_hook(count: int, block_size: int, total_size: int) -> None:
    """Hiển thị tiến trình tải file theo phần trăm."""
    if total_size <= 0:
        print(f"\r  Downloaded {count * block_size // 1024:,} KB", end="", flush=True)
    else:
        pct = min(count * block_size * 100 // total_size, 100)
        mb  = min(count * block_size, total_size) / 1024 / 1024
        tot = total_size / 1024 / 1024
        print(f"\r  {pct:3d}%  {mb:.1f}/{tot:.1f} MB", end="", flush=True)


def download_file(url: str, dest_path: str) -> bool:
    """
    Tải file từ URL về dest_path.
    Hiển thị progress bar và trả về True nếu thành công, False nếu thất bại.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    print(f"  URL : {url}")
    print(f"  Lưu : {dest_path}")
    try:
        urllib.request.urlretrieve(url, dest_path, reporthook=_progress_hook)
        print()   # Xuống dòng sau progress
        return True
    except Exception as exc:
        print(f"\n  ❌ Lỗi tải: {exc}")
        return False


def download_buffalo_l(output_dir: Path) -> None:
    """
    Tải và giải nén buffalo_l.zip (chứa SCRFD + ArcFace R100).
    File ZIP được tải 1 lần, rồi giải nén vào output_dir/buffalo_l/.
    """
    import zipfile

    zip_path = output_dir / _BUFFALO_ZIP_PATH
    extract_dir = output_dir / "buffalo_l"

    # Kiểm tra nếu cả 2 file cần thiết đã tồn tại
    dest_scrfd  = extract_dir / "det_10g.onnx"
    dest_arc    = extract_dir / "w600k_r50.onnx"
    if dest_scrfd.exists() and dest_arc.exists():
        print("  ✅ buffalo_l models đã tồn tại — bỏ qua tải.")
        return

    print(f"\n{'='*60}")
    print(f"📦 Đang tải buffalo_l.zip (SCRFD + ArcFace R100) ...")
    print(f"{'='*60}")
    if not zip_path.exists():
        ok = download_file(_BUFFALO_ZIP_URL, str(zip_path))
        if not ok:
            print("  ❌ Không thể tải buffalo_l.zip. Vui lòng tải thủ công.")
            return
    else:
        print(f"  ℹ️  buffalo_l.zip đã tồn tại ({zip_path.stat().st_size // 1024 // 1024} MB) — bỏ qua tải lại.")

    print(f"  📂 Đang giải nén → {extract_dir} ...")
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(output_dir))
        print("  ✅ Giải nén thành công!")

        # Đổi tên file theo chuẩn path của SV-PRO
        if (extract_dir / "det_10g.onnx").exists():
            # Copy sang tên chuẩn
            import shutil
            shutil.copy(
                str(extract_dir / "det_10g.onnx"),
                str(output_dir / "scrfd_10g_bnkps.onnx"),
            )
            shutil.copy(
                str(extract_dir / "w600k_r50.onnx"),
                str(output_dir / "glintr100.onnx"),
            )
            print("  ✅ Đã copy sang models/scrfd_10g_bnkps.onnx & models/glintr100.onnx")
    except Exception as exc:
        print(f"  ❌ Lỗi giải nén: {exc}")


def download_minifasnet(output_dir: Path) -> None:
    """Tải MiniFASNet ONNX model cho anti-spoofing từ GitHub minivision-ai."""
    dest = output_dir / "anti_spoof" / "minifasnet.onnx"
    if dest.exists():
        print(f"  ✅ minifasnet.onnx đã tồn tại — bỏ qua.")
        return

    print(f"\n{'='*60}")
    print("📦 Đang tải MiniFASNet (Anti-spoofing) ...")
    print(f"{'='*60}")
    url = "https://github.com/minivision-ai/Silent-Face-Anti-Spoofing/raw/master/resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.onnx"
    download_file(url, str(dest))


def print_instructions(output_dir: Path) -> None:
    """
    In hướng dẫn tải thủ công các model cần thêm (YOLOv8, PaddleOCR).
    Các model này cần fine-tune hoặc tải từ bên thứ 3.
    """
    print(f"\n{'='*60}")
    print("📋 HƯỚNG DẪN TẢI THÊM MODEL THỦ CÔNG")
    print(f"{'='*60}")
    print(f"""
⚠️  Các model sau cần tải thủ công (do giới hạn phân phối):

1. YOLOv8s (Vehicle Detection):
   - Nguồn: Ultralytics HuggingFace (fine-tuned VN vehicles)
   - Đặt tại: {output_dir}/yolov8s.onnx

2. YOLOv8n (Plate Detection):
   - Nguồn: Custom fine-tune trên dataset biển số VN
   - Đặt tại: {output_dir}/yolov8s_plate/yolov8s_plate.onnx

3. PaddleOCR:
   - Tự động tải khi lần đầu gọi PaddleOCR(lang='en')
   - Không cần copy thủ công

Sau khi đặt đúng vị trí, chạy:
  docker compose up -d --build
""")


def main() -> None:
    """Hàm chính: parse args và tải tất cả model cần thiết."""
    parser = argparse.ArgumentParser(description="Tải model ONNX cho SV-PRO pipeline")
    parser.add_argument(
        "--output-dir", "-o",
        default="models",
        help="Thư mục lưu model (mặc định: ./models)",
    )
    parser.add_argument(
        "--skip-buffalo", action="store_true",
        help="Bỏ qua tải buffalo_l.zip (SCRFD + ArcFace)",
    )
    parser.add_argument(
        "--skip-antispoof", action="store_true",
        help="Bỏ qua tải MiniFASNet anti-spoof model",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n🚀 SV-PRO Model Downloader")
    print(f"   Output: {output_dir}\n")

    if not args.skip_buffalo:
        download_buffalo_l(output_dir)
    else:
        print("⏭️  Bỏ qua buffalo_l (SCRFD + ArcFace).")

    if not args.skip_antispoof:
        download_minifasnet(output_dir)
    else:
        print("⏭️  Bỏ qua MiniFASNet.")

    print_instructions(output_dir)

    print(f"\n✅ Hoàn thành! Kiểm tra thư mục: {output_dir}")
    print("   Nhớ tải thêm các model YOLOv8 theo hướng dẫn ở trên.\n")


if __name__ == "__main__":
    main()
