# Kế hoạch Triển khai Sprint 3: Face Recognition (Nhận diện Khuôn mặt)

> **Mục tiêu Sprint:** Xây dựng phần mềm nhận diện khuôn mặt (FR) hoàn chỉnh trên Savant pipeline. Đường truyền (pipeline) sẽ chụp khuôn mặt, trích xuất đặc trưng (embedding), tra cứu trên SQL Database thông qua bộ nhớ đệm Redis, và theo dõi những người lạ xuất hiện.

---

## 1. Kiến trúc luồng xử lý FR (Face Recognition)

Tương tự như cơ chế LPR, FR sẽ được viết dưới dạng **Savant NvDsPyFuncPlugin**. Đường dẫn dự kiến: `src/fr/face_recognizer.py`.

**Pipeline logic:**
1. **Detection:** Dùng thuật toán SCRFD-10GF (ONNX) để phát hiện khuôn mặt và bộ 5 điểm (5-point landmarks) trên khung hình camera.
2. **Alignment:** Thực hiện AffineTransform để cắt và căn chỉnh khuôn mặt đúng hình vuông (112x112 px).
3. **Quality & Anti-Spoofing (Cải tiến):**
   - Đánh giá độ nét (Laplacian) và góc nghiêng của khuôn mặt.
   - Gọi model MiniFASNet để xem có phải hình ảnh chụp lại bằng điện thoại/giấy in hay không. Kém chất lượng -> *Drop frame*.
4. **Embedding:** Cho ảnh 112x112 vào thuật toán ArcFace R100 để thu được dải số đặc trưng (Vector 512 chiều).
5. **Matching & Caching (Truy xuất CSDL):**
   - Bộ nhớ đệm tầng 1 (LRU RAM): Kiểm tra có đang tracking ID không.
   - Bộ nhớ đệm tầng 2 (Redis Shared): Kiểm tra Redis xem Vector có trùng ai là nhân viên (Staff) không.
   - CSDL tầng 3 (pgvector): Truy vấn PostgreSQL dùng chỉ mục HNSW để tìm Vector gần giống nhất, nếu chưa trùng ở Redis.
6. **Stranger Tracking:** Nếu không trùng với ai, gán ID tạm thời (SHA-256 hash) cho người lạ để tracking xem họ đi qua những camera nào.

## 2. Danh sách Task Kỹ thuật

### 2.1 Chuẩn bị Models
- Bắt buộc phải có các file `.onnx` sau đây trong thư mục `/models/`:
  - `scrfd_10g_bnkps.onnx` (Cho phát hiện mặt)
  - `glintr100.onnx` (ArcFace R100)
  - `minifasnet.onnx` (Anti Speefing)
- Các module này sẽ chạy trực tiếp bằng `onnxruntime` trên máy hoặc TensorRT biên dịch ngược. 

### 2.2 Quy trình Caching với Redis
Đội ngũ sẽ không chạy gối SQL liên tục vì dễ gây thắt cổ chai. Hệ thống sẽ tích hợp mô hình phân tầng:
```python
# Pseudo-code logic truy vấn FR:
matches = L1_Local_Cache(track_id)
if not matches:
    matches = Redis.VectorSearch(limit=5) # Tìm trong NV Công ty
    if not matches:
        matches = Postgres.PgVector(limit=1) # Fallback cuối
        if not matches:
            register_stranger()
```

### 2.3 Phân chia công việc (Giai đoạn Implement)

1. **Khởi tạo Code gốc:** Tạo class `FaceRecognizer` kế thừa `NvDsPyFuncPlugin`
2. **Setup SCRFD + ArcFace SDK:** Tái sử dụng logic toán học ma trận (AffineTransform) để tự động crop & xoay mặt thẳng đứng.
3. **Cấu hình Redis Connection:** Cập nhật biến môi trường `.env` kết nối Redis và khởi tạo Pool tại `on_start()`.
4. **Stranger Re-ID Sync:** Sẽ có tiến trình phụ xử lý đồng nhất 2 gương mặt người lạ xuất hiện ở 2 luồng camera khác nhau nếu độ giống > 60%.

---

## 3. Rủi ro & Cách phòng tránh

- **Face quá mờ/chuyển động nhanh:** Thuật toán Quality Filter (Lọc chất lượng) phải được bật sớm nhất để không đưa ảnh mờ vào ArcFace. Phạt điểm cho các đối tượng che mặt hoặc chụp nghiêng (Yaw > 30 độ).
- **Latency tắc nghẽn (Block Stream):** Khâu gọi CSDL PgVector trên Python nếu để quá lâu sẽ dồn HWM Queue. Yêu cầu timeout khắt khe (VD: > 50ms từ DB chưa phản hồi -> Xử lý coi là người lạ Tạm thời).
