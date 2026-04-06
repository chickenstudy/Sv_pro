# Ghi chú cho Môi trường Windows (WSL2 / Docker Desktop)

Đối với hệ thống chạy trên Windows thay vì Linux/Ubuntu native:

## 1. Lưu ý về NVIDIA Persistence Mode (Task 1.3)
- Lệnh `sudo systemctl enable nvidia-persistenced` **KHÔNG** khả dụng và không cần thiết trên Windows.
- Việc quản lý VRAM và sleep mode của GPU sẽ do Windows và driver WDDM (Windows Display Driver Model) qua WSL tích hợp quản lý. 
- Để tránh bị crash pipeline do sleep máy, đảm bảo máy tính không tự động sleep ("Sleep: Never" trong Power Options của Windows).

## 2. Docker Desktop & GPU Passthrough
Đảm bảo bạn đã cài đặt phiên bản Docker Desktop mới nhất và bật tích hợp WSL2.
Kiểm tra xem WSL có nhận GPU không bằng cách mở terminal Windows và gõ:
```bash
wsl -- nvidia-smi
```
Nếu hiện ra bảng thông số GPU là thành công.

## 3. Dummy Ingress Camera
Do chưa tích hợp camera RTSP thực tế trong giai đoạn Sprint 1, hệ thống sẽ sử dụng dummy stream:
- `savant_rs_video_source` (module SMPTE pattern từ Savant) 
- Chạy giả lập 15 FPS, output vào `ipc:///tmp/zmq-sockets/input-video.ipc`
- Mô phỏng một camera tên là `cam_dummy_01`
