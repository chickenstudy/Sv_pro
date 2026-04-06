#!/usr/bin/env python3
import os
import argparse
import subprocess

# Giải thích: Hàm chuyển đổi model chuẩn hoá file ảnh hoặc mạng AI ONNX sang dạng TensorRT để chạy trên thiết bị nHWS.
def convert_to_tensorrt(onnx_path, engine_path, fp16=True):
    """
    Sử dụng lệnh hệ thống gọi `trtexec` (có sẵn trong container TensorRT/DeepStream)
    để biên dịch mô hình ONNX thành Engine TensorRT theo card GPU hiện tại.
    """
    if not os.path.exists(onnx_path):
        print(f"File không tồn tại: {onnx_path}")
        return

    # Lệnh biên dịch chuẩn từ NVIDIA TensorRT toolkit
    command = [
        "trtexec",
        f"--onnx={onnx_path}",
        f"--saveEngine={engine_path}",
        "--workspace=4096", # 4GB RAM 
    ]
    
    if fp16:
        command.append("--fp16") # Tối ưu hóa điểm chuẩn Float16 

    print(f"Đang thực thi: {' '.join(command)}")
    
    try:
        subprocess.run(command, check=True)
        print(f"\n[OK] Chuyển đổi thành công: {engine_path}")
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Biên dịch thất bại: {e}")
        print("Lưu ý: Script này cần chạy bên trong môi trường có sẵn CUDA và trtexec.")

# Giải thích: Hàm gọi chính (Entrypoint) cho chương trình từ CLI.
if __name__ == "__main__":
    """Main point cho script - Phân tích tham số CLI và xử lý lệnh convert."""
    parser = argparse.ArgumentParser(description="Chuyển đổi ONNX model thành TensorRT Engine.")
    parser.add_argument("--model", type=str, required=True, help="Đường dẫn tới file ONNX")
    parser.add_argument("--output", type=str, required=True, help="Đường dẫn lưu file .engine")
    parser.add_argument("--fp16", action="store_true", help="Bật tối ưu Float16")
    
    args = parser.parse_args()
    
    # Tạo thư mục đầu ra nếu chưa có
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    
    convert_to_tensorrt(args.model, args.output, args.fp16)
