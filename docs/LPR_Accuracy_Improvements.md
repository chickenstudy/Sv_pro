## LPR Accuracy Improvements (SV-PRO)

Tài liệu này mô tả cách **kế thừa toàn bộ LPR accuracy** từ `vms-savant` và chuẩn hóa thành blueprint cho `sv-pro`, tập trung vào:

1. `OCR smoothing` (temporal smoothing / vote theo track)
2. `OCR normalization` (chuẩn hóa biển số Việt Nam + regex phân loại theo category)
3. `ROI` (lọc theo vùng + ROI Autopilot)

---

## 1. Mục tiêu & nguyên tắc kế thừa

Từ `vms-savant`, SV-PRO nên đạt được các thuộc tính sau:

- **Giảm nhiễu OCR theo thời gian**: không chỉ lấy “đọc tốt nhất tại 1 frame”, mà gom nhiều lần đọc quanh cùng một `track_id`.
- **Chuẩn hóa đúng chuẩn biển số VN**: thống nhất ký tự hợp lệ, xử lý nhầm lẫn OCR (D/O, I/1, v.v.) và áp regex theo thứ tự ưu tiên.
- **Chỉ xử lý phần khung có khả năng đọc được**: `roi_zones` giảm tải và giảm false-positive.

`vms-savant` đã có sẵn các cơ chế cốt lõi (đặc biệt trong `src/lpr/plate_ocr.py`):

- lọc sharpness (Laplacian variance)
- night mode preprocessing (gamma/brightness + CLAHE)
- split-line OCR fallback (biển 2 dòng)
- tracking/buffer candidate và save 1 lần khi track expire
- dedup (tránh lưu trùng cùng plate trong thời gian ngắn)

SV-PRO chỉ cần port và “đóng gói” lại theo contract cấu hình tại `module/module.yml`.

---

## 2. Temporal Smoothing OCR (OCR smoothing)

### 2.1 Vấn đề cần giải

OCR trên biển số VN thường dao động theo:

- nhấp nháy đèn / thay đổi ánh sáng
- góc camera / độ nghiêng
- chuyển động của xe làm motion blur
- trường hợp biển 2 dòng: dòng trên thường mất ký tự

Do đó, lấy kết quả “best confidence” của frame đơn thường không ổn định.

### 2.2 Cơ chế kế thừa khuyến nghị

Trong `vms-savant`, pipeline kết hợp:

- theo `track_id` (DeepStream NvTracker) hoặc fallback center-distance
- buffer nhiều lần đọc OCR (candidate list) theo track
- khi track expire (không thấy trong `_TRACK_MAX_AGE`), chọn plate “tốt nhất/đại diện” để save
- dedup theo `(source_id, plate_text)` với cửa sổ `_PLATE_DEDUP_SECS`

Để đúng với mô tả SV-PRO (“vote majority trên 10 frame”), bạn nên thực thi một trong 2 variant:

Variant A (khớp với `vms-savant` hiện có, ưu tiên port nhanh):

- buffer tối đa `N` candidates theo track
- chọn candidate có `ocr_conf` cao nhất (và/hoặc det_conf)
- khi track expire, save đúng 1 lần

Variant B (chuẩn hóa vote majority):

- lấy `N` candidates quanh tracker window (đề xuất `N=10`)
- áp “character-level majority vote” (nếu bạn có hàm `_vote_plate()` theo kiểu position vote)
- chọn plate sau normalize có dạng hợp lệ theo regex category
- dùng `ocr_conf` trung bình của nhóm winning-length để làm confidence cuối

Khuyến nghị thực chiến: triển khai Variant A trước để port tương thích, sau đó nâng cấp sang Variant B để khớp spec “10-frame vote”.

---

## 3. OCR Normalization & Plate Categorization

### 3.1 Normalize: ký tự hợp lệ và xử lý nhầm OCR

`vms-savant` định nghĩa:

- tập ký tự hợp lệ `_PLATE_ALLOWED` (gồm digits, A-Z không chứa một số ký tự không dùng trong VN plates, dấu `-`)
- hàm `_normalize_plate()` tạo nhiều “variant” và thử phân loại regex

Các lỗi OCR thường gặp:

- OCR nhầm `O` (chữ) với `0` (số) và ngược lại ở segment khác nhau
- OCR nhầm digit/letter tại “series” (ví dụ `1→T`, `6→G`, `7→T`, `8→B`)
- thỉnh thoảng OCR đọc extra leading digit khi plate có format đặc thù

### 3.2 Regex phân loại category (order matters)

`vms-savant` có danh sách category & regex:

- `XE_MAY_DAN_SU`
- `O_TO_DAN_SU`
- `BIEN_CA_NHAN`
- `XE_QUAN_DOI`
- `KHONG_XAC_DINH` (unknown)

Điểm quan trọng:

- regex được thử theo thứ tự (cái “specific” cần đứng trước cái “general” để tránh match sai)
- sau khi normalize, chỉ những biến thể thỏa regex mới được coi là valid

### 3.3 Confidence gating

Để giảm false-positive:

- plate detection: `plate_conf_threshold`
- OCR character/line: `ocr_conf_threshold`
- sharpness gate: nếu ảnh plate quá mờ (Laplacian variance thấp) thì skip

Ngoài ra có thể thêm “điều kiện shape” (tối thiểu độ dài chuỗi sau normalize) để loại đọc thiếu ký tự.

---

## 4. ROI (Region of Interest) và ROI Autopilot

### 4.1 ROI filter runtime (trong PlateOCR)

SV-PRO cần filter theo `roi_zones` để:

- bỏ qua xe quá xa (plate quá nhỏ)
- bỏ qua vùng góc khuất/độ nghiêng lớn
- giảm số object phải chạy OCR

Trong `vms-savant`:

- tính center `(cx, cy)` của bbox xe
- chỉ process nếu center nằm trong `[x1, y1, x2, y2]`

### 4.2 ROI Eval (heatmap 50×50)

`vms-savant/src/lpr/roi_eval.py`:

- đọc JSON detect trong `last N days`
- gom theo lưới `GRID_SIZE = 50`
- tính success-rate mỗi cell:
  - `success` = count plate_category != `KHONG_XAC_DINH`
  - `total` = tổng count
- chọn cells thỏa:
  - `MIN_CELL_HITS`
  - `MIN_SUCCESS_RATE`
- suy ra `suggested_roi` theo bounding box cells tốt

### 4.3 Auto-apply ROI update YAML (an toàn)

SV-PRO cần thêm “atomic update” và “validate camera key mapping” khi ghi vào `module/module.yml`:

- validate camera tồn tại trong `roi_zones`
- nếu key camera không tồn tại hoặc format sai → không ghi/không restart
- ghi vào file tạm rồi rename (tránh module.yml bị cắt giữa chừng)

Định danh camera trong `sv-pro` nên thống nhất với `source_id` của ingress.

---

## 5. Event schema (data contract)

Bạn nên thống nhất output JSON (để `json-egress` và dashboard truy xuất ổn định). Dựa theo ví dụ trong `vms-savant` và README `sv-pro`, đề xuất schema tối thiểu cho LPR:

- `timestamp`
- `event_id` (nếu bạn có trace_id thì dùng để liên kết)
- `source_id`
- `label` (vehicle type)
- `plate_number` (đã normalize)
- `plate_raw` (raw OCR trước normalize, nếu có)
- `plate_category`
- `ocr_confidence`
- `plate_det_confidence`
- `vehicle_bbox` (x1,y1,x2,y2)
- `plate_bbox_in_vehicle` (x1,y1,x2,y2)
- `files`: `vehicle` + `plate`

Nếu plate detection thất bại thì có thể output record loại `NOT_DETECTED` để phục vụ annotation offline.

---

## 6. Porting checklist (SV-PRO cần làm gì trong code)

Để “kế thừa toàn bộ LPR accuracy”, `sv-pro/src/lpr/plate_ocr.py` (hoặc tương đương) nên bao gồm:

- [ ] init/warmup: ONNX Runtime session + PaddleOCR (giảm first-inference latency spike)
- [ ] preprocessing:
  - [ ] CLAHE
  - [ ] night mode brightness threshold (gamma/HEQ)
  - [ ] sharpening (unsharp mask / kernel)
- [ ] plate detection chạy trong `_detect_plates`
- [ ] OCR:
  - [ ] line detection với `_LINE_DETECT_CONF`
  - [ ] split-line fallback cho biển 2 dòng khi kết quả chỉ digits
- [ ] normalize:
  - [ ] `_PLATE_ALLOWED`
  - [ ] `_normalize_plate` + `_classify_plate` theo regex order
- [ ] tracking + temporal smoothing:
  - [ ] track state buffer (max candidates)
  - [ ] save-on-track-expire
  - [ ] dedup theo `(source_id, plate_text)`
- [ ] disk I/O off the hot path:
  - [ ] background save queue/thread (tránh block pipeline)
- [ ] ROI filter theo `roi_zones`

---

## 7. Gợi ý cấu hình module/module.yml (knobs)

Trong `module/module.yml`, bạn nên expose các knob:

- `plate_conf_threshold`
- `ocr_conf_threshold`
- `nms_iou_threshold`
- `roi_zones`
- `plate_skip_factor` (nếu có cơ chế skip theo N frame / theo điều kiện)
- `min_sharpness`
- `night_brightness_thresh`
- `temporal_window_frames` hoặc `max_candidates`
- `plate_dedup_seconds`

Các giá trị mặc định có thể map theo `vms-savant`:

- plate conf: ~0.35–0.50
- ocr conf: ~0.50–0.60
- sharpness gate: `Laplacian variance` khoảng `>= 25`
- night brightness threshold: `80`

---

## 8. Test plan (để xác nhận “accuracy kế thừa”)

Tối thiểu cần:

- bộ ảnh test ban ngày/ban đêm theo từng camera/source_id
- test case cho:
  - xe 1 dòng
  - xe 2 dòng (motorcycle)
  - góc nghiêng cao (ROI)
  - motion blur (sharpness gate)
- metric:
  - plate read rate (OCR valid)
  - category accuracy
  - stability: đo % sự kiện giữ kết quả không đổi trong window tracking

