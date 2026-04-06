# Kế hoạch Chi tiết Sprint 2: LPR & Nhận diện Phương tiện (Vehicle Domain)

Trong Sprint 2, chúng ta sẽ tập trung vào luồng xử lý nhận diện biển số xe (License Plate Recognition). Mục tiêu là hoàn thiện `plate_ocr.py` để xử lý đầu vào từ luồng Video và xuất ra JSON đạt chuẩn.

## 1. Mục tiêu (Goals)
- Hoàn thiện PyFunc plugin `PlateOCR` để tương tác với frame do GPU Savant truyền về.
- Tích hợp chuẩn hoá biển số Việt Nam (loại bỏ ký tự thừa, nhận diện biển vuông/biển dài).
- Áp dụng các cải tiến như: Temporal Smoothing (bình ổn kết quả), và Night Mode (CLAHE + độ tương phản cho ban đêm).

## 2. Các Task Kỹ thuật (Implementation Phase)

### 2.1. Cấu trúc Data Contract LPR (Task 2.3.1)
Chúng ta sẽ chuẩn hóa output JSON bao gồm các trường chuẩn như: `plate_number` (đã chuẩn hóa định dạng), `plate_raw` (nhận diện gốc), `vehicle_type`, v.v. Các metadata này sẽ được gán vào `frame_meta.add_obj_meta()`.

### 2.2. Viết Python Logic `src/lpr/plate_ocr.py` (Task 2.2 + 2.4 + 2.5)
- **Tạo class `PlateOCR(NvDsPyFuncPlugin)`:** Hook vào luồng Savant sau khi detect được xe và detect biển số.
- **Xử lý Night Mode:** Tính trung bình độ sáng (mean brightness) của crop ROI. Nếu nhỏ hơn ngưỡng, áp dụng OpenCV `cv2.equalizeHist` hoặc `CLAHE` trước khi đọc chữ OCR.
- **Xử lý Biển Vuông 2 dòng:** Viết hàm phân tách tọa độ bounding box để ghép nối chữ đúng theo dòng (trên xuống dưới).
- **Temporal Smoothing:** Sử dụng từ điển (Dictionary) tracking trên `object_id`. Cập nhật mảng đệm 10 frame gần nhất để bỏ phiếu (Vote) kết quả có tỷ lệ lặp lại cao nhất nhằm giảm nhiễu chớp giật.
- **Lưu lại File Báo Cáo Sự Kiện:** Chuyển xử lý ghi IO (ảnh crop, JSON meta) sang một luồng (thread) hoặc hàng đợi riêng biệt để tránh nghẽn hot path (Task 2.7).

### 2.3. Tiện ích Export Models về TensorRT (Task 2.1)
- Tạo một script mẫu `scripts/convert_trt.py` dùng `trtexec` để hướng dẫn bạn cách biên dịch (compile) model TensorRT FP16 khi triển khai trên board thực tế sau này.

---

## 3. Câu Hỏi Xác Nhận (Socratic Gate)

1. **Phiên bản PaddleOCR:** Bạn xử lý inference bằng `PaddleOCR` gọi trực tiếp qua CPU hay chuyển đổi ONNX để chạy chung với ONNX-Runtime/GPU? (Hiện tại để phát triển tôi sẽ config code sử dụng package `paddleocr` bản chuẩn Python hỗ trợ OpenCV).
2. **Ngôn ngữ chuẩn hoá:** Hiện tại trong `plate_ocr.py`, bạn có muốn ghi log debug chi tiết trong ứng dụng bằng tiếng Anh hay tôi nên duy trì quy tắc "có giải thích tiếng Việt trên đầu hàm" và log debug tiếng Anh?

**Bạn vui lòng phản hồi hoặc nhấn (Y) để tôi triệu hồi các Agents (Python Backend, DevOps) lập tức code các File cho Sprint 2 nhé!**
