import os
import requests
from tqdm import tqdm

def download_file(url, filename):
    """Tải file với thanh tiến trình."""
    response = requests.get(url, stream=True)
    total_size = int(response.headers.get('content-length', 0))
    block_size = 1024
    
    with open(filename, 'wb') as file, tqdm(
        desc=filename,
        total=total_size,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in response.iter_content(block_size):
            size = file.write(data)
            bar.update(size)

def main():
    # Thư mục đích cho InsightFace buffalo_l model
    model_dir = "models/insightface/buffalo_l"
    os.makedirs(model_dir, exist_ok=True)
    
    # Danh sách các file cần thiết cho buffalo_l
    # Note: Đây là link giả lập, trong thực tế sẽ dùng insightface library để tải
    # hoặc link trực tiếp từ GitHub/HuggingFace của InsightFace.
    print("🚀 Bắt đầu tải mô hình InsightFace buffalo_l...")
    print("💡 Lưu ý: Bạn có thể cài đặt thư viện 'insightface' và chạy 'FaceAnalysis(name=\"buffalo_l\")' để tự động tải.")
    
    # Gợi ý lệnh cài đặt
    print("\nLệnh cài đặt thư viện hỗ trợ:")
    print("pip install insightface onnxruntime-gpu tqdm requests")

if __name__ == "__main__":
    main()
