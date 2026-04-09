# Windows/WSL2 vs Ubuntu — ổn định & khác biệt quan trọng

## Windows + WSL2 + Docker Desktop (dev)

- **Sleep/lock**: không có `nvidia-persistenced` như Ubuntu. Cần đặt Windows Power Options để **không sleep** khi chạy pipeline (theo [`README_WINDOWS_DEV.md`](../../README_WINDOWS_DEV.md)).\n+- **NVENC**: trong WSL2 thường **không hỗ trợ NVENC**; `module/module.yml` đã disable encoder NVENC và dùng `raw-rgb24`.\n+- **GPU visibility**: kiểm tra `wsl -- nvidia-smi`.\n+- **File I/O**: nên đặt workspace trong filesystem WSL (ví dụ `/home/...`) nếu thấy I/O chậm khi ghi `Detect/`/`output/`.\n+
## Ubuntu 22.04 native (staging/prod)

- **NVIDIA Persistence Mode (bắt buộc)**: bật để tránh deadlock khi host lock/suspend (theo [`README.md`](../../README.md)).\n+- **NVIDIA Container Toolkit**: đảm bảo Docker có GPU passthrough.\n+- **NVENC**: có thể bật lại encode NVENC cho output video nếu cần.\n+- **dcgm-exporter**: khuyến nghị thêm để Prometheus scrape GPU metrics (Prometheus config đã có job `svpro_gpu`).\n+
## Checklist “đúng trước khi chạy lâu”

- `docker compose ps` tất cả service đều `healthy` (backend, postgres, redis, prometheus, grafana, ai-core).\n+- Disk còn đủ chỗ cho `./Detect` và `./output`.\n+- Grafana hiển thị các counter `svpro_*` tăng theo thời gian.\n+
