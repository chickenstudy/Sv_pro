"""
Module nhận diện khuôn mặt (Face Recognition) cho pipeline SV-PRO.

Quy trình 2-stage:
  Stage 1 – Phát hiện mặt:  SCRFD-10GF (ONNX Runtime) → bounding box + 5-point landmark.
  Stage 2 – Nhận diện mặt:  ArcFace R100 (ONNX Runtime) → embedding 512-dim → cosine search.

Bộ nhớ đệm 2 tầng:
  Tầng 1 (L1): LRU cache process-local theo track_id  (capacity=1000, TTL=60s)
  Tầng 2 (L2): Redis shared  (prefetch staff khi startup, TTL=5 phút)

Stranger Tracking:
  Nếu không match ai → gán ID tạm (SHA-256[:8] của embedding) → insert DB sau ≥ 3 frame chất lượng.
"""

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Optional

import cv2
import numpy as np

from savant.deepstream.meta.frame import NvDsFrameMeta
from savant.deepstream.pyfunc import NvDsPyFuncPlugin
from savant.deepstream.opencv_utils import nvds_to_gpu_mat

from .face_quality import compute_quality_score, _MIN_COMPOSITE
from .stranger_reid import stranger_registry
from src.telemetry import metrics

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))

# ── Ngưỡng nhận diện & theo dõi ───────────────────────────────────────────────
_RECOGNITION_THRESHOLD = 0.55   # cosine similarity tối thiểu để coi là "match"
_STRANGER_MIN_FRAMES   = 3      # Số frame chất lượng tối thiểu trước khi lưu stranger
_STRANGER_DEDUP_SECS   = 300.0  # Không tạo stranger mới cho cùng track trong 5 phút
_TRACK_MAX_AGE         = 5.0    # Xóa track nếu không thấy trong 5 giây

# ── L1 Cache ────────────────────────────────────────────────────────────────────
_L1_CAPACITY = 1000
_L1_TTL_SECS = 60.0

# ── Label nhận diện gắn vào metadata ──────────────────────────────────────────
_ATTR_ELEMENT  = "fr"
_ATTR_PERSON   = "person_id"
_ATTR_NAME     = "person_name"
_ATTR_ROLE     = "person_role"
_ATTR_CONF     = "fr_confidence"


# ── L1 LRU Cache (dùng OrderedDict) ────────────────────────────────────────────

class _L1Cache:
    """
    LRU cache bộ nhớ nội tại (process-local) lưu kết quả nhận diện theo track_id.
    Capacity và TTL cố định để tránh tràn bộ nhớ RAM.
    """

    def __init__(self, capacity: int = _L1_CAPACITY, ttl: float = _L1_TTL_SECS):
        # Lưu cặp track_id → (person_data, timestamp)
        self._cache: OrderedDict[int, tuple[dict, float]] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl

    def get(self, track_id: int) -> Optional[dict]:
        """Trả về kết quả nhận diện nếu còn hợp lệ (chưa hết TTL), ngược lại None."""
        item = self._cache.get(track_id)
        if item is None:
            return None
        data, ts = item
        if time.monotonic() - ts > self._ttl:
            del self._cache[track_id]
            return None
        # LRU: đưa item lên cuối vì mới vừa truy xuất
        self._cache.move_to_end(track_id)
        return data

    def put(self, track_id: int, data: dict) -> None:
        """Lưu kết quả nhận diện cho track_id vào cache."""
        if track_id in self._cache:
            self._cache.move_to_end(track_id)
        self._cache[track_id] = (data, time.monotonic())
        # Giới hạn dung lượng: xóa phần tử cũ nhất (FIFO trong LRU)
        while len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    def invalidate(self, track_id: int) -> None:
        """Xóa cache của một track cụ thể."""
        self._cache.pop(track_id, None)


# ── Stranger Track State ────────────────────────────────────────────────────────

class _StrangerState:
    """
    Lưu trạng thái theo dõi một người lạ (stranger) chưa được nhận diện.
    Tích lũy embedding từ nhiều frame để tạo profile ổn định hơn.
    """

    __slots__ = (
        "stranger_id", "track_id", "source_id",
        "quality_frames", "embeddings",
        "best_face_crop", "best_quality_score",
        "first_seen", "last_seen", "saved",
    )

    def __init__(self, track_id: int, source_id: str, now: float):
        # Tạo ID ngẫu nhiên tạm thời (sẽ được gán lại sau khi lưu DB)
        self.stranger_id: str | None = None
        self.track_id    = track_id
        self.source_id   = source_id
        self.quality_frames: int = 0
        self.embeddings: list[np.ndarray] = []
        self.best_face_crop: np.ndarray | None = None
        self.best_quality_score: float = 0.0
        self.first_seen  = now
        self.last_seen   = now
        self.saved       = False

    def add_frame(self, embedding: np.ndarray, face_crop: np.ndarray, quality: float) -> None:
        """Thêm một frame chất lượng mới vào buffer của stranger."""
        self.quality_frames += 1
        self.embeddings.append(embedding)
        if quality > self.best_quality_score:
            self.best_quality_score = quality
            self.best_face_crop = face_crop.copy()

    def mean_embedding(self) -> np.ndarray:
        """Tính embedding trung bình từ tất cả các frame đã tích lũy."""
        if not self.embeddings:
            return np.zeros(512, dtype=np.float32)
        emb = np.stack(self.embeddings, axis=0).mean(axis=0)
        norm = np.linalg.norm(emb)
        return emb / (norm + 1e-6)

    def generate_id(self) -> str:
        """Tạo ID ngắn từ embedding trung bình bằng SHA-256[:8]."""
        emb_bytes = self.mean_embedding().tobytes()
        return hashlib.sha256(emb_bytes).hexdigest()[:8].upper()


# ── Main Plugin ─────────────────────────────────────────────────────────────────

class FaceRecognizer(NvDsPyFuncPlugin):
    """
    Savant NvDsPyFuncPlugin xử lý nhận diện khuôn mặt (FR) trên pipeline SV-PRO.

    Thông số cấu hình (truyền từ module.yml):
      scrfd_model_path      – Đường dẫn model SCRFD ONNX.
      arcface_model_path    – Đường dẫn model ArcFace R100 ONNX.
      anti_spoof_model_path – Đường dẫn model MiniFASNet ONNX.
      recognition_threshold – Ngưỡng cosine similarity để nhận diện (mặc định 0.55).
      enable_anti_spoof     – Bật/tắt kiểm tra giả mạo (mặc định True).
      redis_host / redis_port / redis_db – Thông tin kết nối Redis.
      db_dsn                – PostgreSQL DSN string để lưu stranger.
    """

    def __init__(
        self,
        scrfd_model_path: str = "/models/scrfd_10g_bnkps.onnx",
        arcface_model_path: str = "/models/glintr100.onnx",
        anti_spoof_model_path: str = "/models/anti_spoof/minifasnet.onnx",
        recognition_threshold: float = _RECOGNITION_THRESHOLD,
        enable_anti_spoof: bool = True,
        redis_host: str = "redis",
        redis_port: int = 6379,
        redis_db: int = 0,
        db_dsn: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.scrfd_model_path      = scrfd_model_path
        self.arcface_model_path    = arcface_model_path
        self.anti_spoof_model_path = anti_spoof_model_path
        self.recognition_threshold = recognition_threshold
        self.enable_anti_spoof     = enable_anti_spoof
        self.redis_host            = redis_host
        self.redis_port            = redis_port
        self.redis_db              = redis_db
        self.db_dsn                = db_dsn

        # Models (khởi tạo trong on_start)
        self._scrfd      = None   # ONNX session cho SCRFD
        self._arcface    = None   # ONNX session cho ArcFace
        self._anti_spoof = None   # AntispoofModel (MiniFASNet)

        # Cache & tracking
        self._l1_cache   = _L1Cache()
        self._redis      = None   # Redis client
        self._disabled    = False  # Graceful degradation flag

        # Stranger tracking: {(source_id, track_id): _StrangerState}
        self._strangers: dict[tuple, _StrangerState] = {}
        # Dedup: {(source_id, track_id): last_saved_timestamp}
        self._stranger_saved_ts: dict[tuple, float] = {}

        # Active tracks per source để flush expired
        self._active_tracks: dict[str, dict[int, float]] = {}   # source → {track_id: last_seen}

    # ──────────────────────────────────────────────────────────────────────────
    # Savant lifecycle hooks
    # ──────────────────────────────────────────────────────────────────────────

    def on_start(self) -> bool:
        """Tải models và khởi tạo kết nối Redis/DB khi pipeline bắt đầu."""
        if not super().on_start():
            return False
        self._disabled = False
        try:
            self._init_models()
            self._init_redis()
            self._prefetch_staff_embeddings()
        except Exception as exc:
            # Degrade gracefully: keep pipeline alive even if FR deps/models
            # are not available in the current environment.
            logger.error("FaceRecognizer disabled (on_start failed): %s", exc, exc_info=True)
            self._disabled = True
            return True
        return True

    def on_stop(self) -> None:
        """Dọn dẹp tài nguyên khi pipeline dừng."""
        logger.info("FaceRecognizer stopping — flushing %d active strangers", len(self._strangers))
        self._flush_all_strangers()
        super().on_stop()

    # ──────────────────────────────────────────────────────────────────────────
    # Khởi tạo models & kết nối
    # ──────────────────────────────────────────────────────────────────────────

    def _init_models(self) -> None:
        """
        Nạp các model ONNX vào RAM/GPU:
          1. SCRFD-10GF  (phát hiện mặt)
          2. ArcFace R100 (trích xuất embedding)
          3. MiniFASNet   (anti-spoofing, tùy chọn)
        Chạy warmup để tránh spike latency trên frame đầu tiên.
        """
        import onnxruntime as ort
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        logger.info("Loading SCRFD model: %s", self.scrfd_model_path)
        self._scrfd = ort.InferenceSession(self.scrfd_model_path, providers=providers)
        logger.info("SCRFD provider: %s", self._scrfd.get_providers()[0])

        logger.info("Loading ArcFace model: %s", self.arcface_model_path)
        self._arcface = ort.InferenceSession(self.arcface_model_path, providers=providers)
        logger.info("ArcFace provider: %s", self._arcface.get_providers()[0])

        if self.enable_anti_spoof and os.path.exists(self.anti_spoof_model_path):
            logger.info("Loading MiniFASNet model: %s", self.anti_spoof_model_path)
            self._anti_spoof = ort.InferenceSession(self.anti_spoof_model_path, providers=providers)
            logger.info("MiniFASNet loaded.")
        else:
            logger.info("Anti-spoof disabled or model not found — skipping.")

        # Warmup để tránh CUDA compilation spike trên frame thực đầu tiên
        self._warmup_models()
        logger.info("FaceRecognizer models ready.")

    def _warmup_models(self) -> None:
        """Chạy thử inference với tensor dummy để khởi động CUDA kernels."""
        logger.info("Warming up SCRFD ...")
        dummy_frame = np.zeros((1, 3, 640, 640), dtype=np.float32)
        self._scrfd.run(None, {self._scrfd.get_inputs()[0].name: dummy_frame})

        logger.info("Warming up ArcFace ...")
        dummy_face = np.zeros((1, 3, 112, 112), dtype=np.float32)
        self._arcface.run(None, {self._arcface.get_inputs()[0].name: dummy_face})

        if self._anti_spoof:
            logger.info("Warming up MiniFASNet ...")
            dummy_spoof = np.zeros((1, 3, 80, 80), dtype=np.float32)
            self._anti_spoof.run(None, {self._anti_spoof.get_inputs()[0].name: dummy_spoof})

        logger.info("All FR models warmed up.")

    def _init_redis(self) -> None:
        """
        Khởi tạo kết nối Redis để lưu và truy xuất embedding nhân viên (Staff).
        Nếu Redis không khả dụng, log cảnh báo và tiếp tục (graceful degrade).
        """
        try:
            import redis
            self._redis = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                db=self.redis_db,
                socket_connect_timeout=2,
                decode_responses=False,
            )
            self._redis.ping()
            logger.info("Redis connected: %s:%d/db%d", self.redis_host, self.redis_port, self.redis_db)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — FR will run without L2 cache!", exc)
            self._redis = None

    def _prefetch_staff_embeddings(self) -> None:
        """
        Tải toàn bộ embedding của nhân viên (role='staff') từ DB vào Redis khi startup.
        Giúp tăng tốc độ tra cứu: tra Redis thay vì gọi DB tốn kém.
        Nếu DB chưa kết nối được, bỏ qua (graceful degrade).
        """
        if not self.db_dsn or not self._redis:
            logger.info("Skipping staff prefetch (no DB DSN or Redis).")
            return
        try:
            import psycopg2
            conn = psycopg2.connect(self.db_dsn)
            cur  = conn.cursor()
            cur.execute(
                "SELECT id, name, role, face_embedding FROM users "
                "WHERE face_embedding IS NOT NULL AND active = TRUE AND role = 'staff'"
            )
            rows = cur.fetchall()
            pipe = self._redis.pipeline(transaction=False)
            count = 0
            for uid, name, role, embedding_str in rows:
                emb = np.array(json.loads(embedding_str), dtype=np.float32)
                key = f"svpro:staff:{uid}"
                payload = json.dumps({
                    "id": uid, "name": name, "role": role,
                    "embedding": emb.tolist(),
                })
                pipe.setex(key, 300, payload)   # TTL = 5 phút
                count += 1
            pipe.execute()
            cur.close()
            conn.close()
            logger.info("Prefetched %d staff embeddings → Redis.", count)
        except Exception as exc:
            logger.warning("Staff prefetch failed: %s — continuing without cache.", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Xử lý frame
    # ──────────────────────────────────────────────────────────────────────────

    def process_frame(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        """
        Hàm chính xử lý mỗi khung hình từ DeepStream pipeline:
          1. Lấy ảnh từ GPU memory sang numpy.
          2. Phát hiện khuôn mặt (SCRFD).
          3. Lọc chất lượng (Face Quality Filter).
          4. Kiểm tra giả mạo (Anti-spoofing, nếu bật).
          5. Trích xuất embedding (ArcFace).
          6. Tra cứu L1 → Redis → DB (nhận diện hoặc stranger tracking).
          7. Ghi kết quả vào object metadata để egress xuất JSON.
        """
        source_id = str(frame_meta.source_id)
        now       = time.monotonic()

        if getattr(self, "_disabled", False):
            return

        # Telemetry: frames processed in AI core (by source_id)
        try:
            metrics.frames_processed_total.labels(source_id=source_id).inc()
        except Exception:
            pass

        # Lấy ảnh frame từ GPU buffer
        try:
            with nvds_to_gpu_mat(buffer, frame_meta.frame_meta) as gpu_mat:
                frame_bgr = gpu_mat.download()
        except Exception as exc:
            logger.debug("Cannot download frame for source %s: %s", source_id, exc)
            return

        # Phát hiện khuôn mặt trên frame đầy đủ
        try:
            with metrics.aicore_inference_ms.labels(camera_id=source_id, model="scrfd").time():
                detections = self._detect_faces(frame_bgr)
        except Exception:
            detections = self._detect_faces(frame_bgr)
        if not detections:
            self._flush_expired_tracks(source_id, now)
            return

        # Cập nhật danh sách track đang active
        active = self._active_tracks.setdefault(source_id, {})

        for bbox, score, landmarks in detections:
            x1, y1, x2, y2 = bbox
            face_crop = frame_bgr[y1:y2, x1:x2]
            if face_crop.size == 0:
                continue

            # Tìm track_id tương ứng từ DeepStream object_meta (nếu có)
            track_id = self._find_track_id(frame_meta, bbox)
            active[track_id] = now

            # ── L1 Cache check ─────────────────────────────────────────────────
            cached = self._l1_cache.get(track_id)
            if cached:
                self._write_attr(frame_meta, track_id, cached)
                continue

            # ── Quality Filter ─────────────────────────────────────────────────
            # Resize face_crop về 112x112 trước khi đánh giá
            face_aligned = self._align_face(face_crop, landmarks)
            quality, q_details = compute_quality_score(face_aligned, landmarks)
            if quality < _MIN_COMPOSITE:
                logger.debug(
                    "Face QC fail track=%s src=%s composite=%.2f %s",
                    track_id, source_id, quality, q_details,
                )
                try:
                    metrics.fr_recognition_total.labels(camera_id=source_id, result="low_quality").inc()
                except Exception:
                    pass
                continue

            # ── Anti-spoofing check ─────────────────────────────────────────────
            if self.enable_anti_spoof and self._anti_spoof is not None:
                if not self._check_anti_spoof(face_aligned):
                    logger.warning("Spoof detected! source=%s track=%s", source_id, track_id)
                    try:
                        metrics.fr_recognition_total.labels(camera_id=source_id, result="spoof").inc()
                    except Exception:
                        pass
                    continue

            # ── ArcFace embedding ───────────────────────────────────────────────
            try:
                with metrics.aicore_inference_ms.labels(camera_id=source_id, model="arcface").time():
                    embedding = self._extract_embedding(face_aligned)
            except Exception:
                embedding = self._extract_embedding(face_aligned)

            # ── Matching: L1 → Redis → Stranger ────────────────────────────────
            match = self._match_embedding(embedding)

            if match:
                # Nhận diện được người quen
                result = {
                    "person_id":   match["id"],
                    "person_name": match["name"],
                    "person_role": match["role"],
                    "fr_confidence": round(match["score"], 4),
                    "is_stranger": False,
                }
                self._l1_cache.put(track_id, result)
                self._write_attr(frame_meta, track_id, result)
                try:
                    metrics.fr_recognition_total.labels(camera_id=source_id, result="known").inc()
                    metrics.fr_events_total.labels(camera_id=source_id).inc()
                except Exception:
                    pass
            else:
                # Người lạ → Stranger tracking
                self._handle_stranger(
                    source_id, track_id, embedding, face_aligned, quality, now
                )
                try:
                    metrics.fr_recognition_total.labels(camera_id=source_id, result="stranger").inc()
                    metrics.fr_events_total.labels(camera_id=source_id).inc()
                except Exception:
                    pass

        self._flush_expired_tracks(source_id, now)

    # ──────────────────────────────────────────────────────────────────────────
    # Phát hiện khuôn mặt (SCRFD)
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_faces(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray | None]]:
        """
        Phát hiện khuôn mặt trong frame bằng SCRFD-10GF ONNX.
        Trả về danh sách (bbox_xyxy, score, landmarks_5pt).
        Nếu không phát hiện hoặc lỗi → trả về danh sách rỗng.
        """
        try:
            h, w = frame_bgr.shape[:2]
            model_h, model_w = 640, 640

            # Letterbox resize để giữ tỉ lệ
            scale  = min(model_h / h, model_w / w)
            new_h  = int(h * scale)
            new_w  = int(w * scale)
            resized = cv2.resize(frame_bgr, (new_w, new_h))

            # Tạo canvas trống và đặt ảnh vào trên cùng bên trái
            canvas = np.zeros((model_h, model_w, 3), dtype=np.uint8)
            canvas[:new_h, :new_w] = resized

            inp = canvas[:, :, ::-1].astype(np.float32)  # BGR→RGB
            inp = inp.transpose(2, 0, 1)[np.newaxis]      # [1, 3, H, W]

            input_name = self._scrfd.get_inputs()[0].name
            outputs = self._scrfd.run(None, {input_name: inp})

            # SCRFD output: [scores, bboxes, kps]  (shapes tuỳ theo model export)
            # Dạng đơn giản: parse output[0] là score, output[1] là bbox
            results = self._parse_scrfd_output(outputs, scale, w, h)
            return results
        except Exception as exc:
            logger.debug("SCRFD detection error: %s", exc)
            return []

    def _parse_scrfd_output(
        self,
        outputs: list,
        scale: float,
        orig_w: int,
        orig_h: int,
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray | None]]:
        """
        Phân tích kết quả đầu ra của SCRFD model (multi-stride format).
        Lọc theo confidence threshold 0.50 và áp dụng NMS đơn giản.
        Chuyển đổi tọa độ về không gian ảnh gốc.
        """
        CONF_THRESH = 0.50
        NMS_THRESH  = 0.40

        all_boxes  = []
        all_scores = []
        all_kps    = []

        # SCRFD thường có 3 stride (8, 16, 32) → 6 output tensors
        # [cls_8, cls_16, cls_32, bbox_8, bbox_16, bbox_32] + [kps_8, kps_16, kps_32]
        # Ở đây xử lý output generic: nếu output[i] có shape phù hợp thì parse
        try:
            if len(outputs) >= 6:
                num_strides = len(outputs) // 2
            else:
                num_strides = 0

            for i in range(num_strides):
                scores_raw = outputs[i].squeeze()       # [N] hoặc [N, 1]
                bboxes_raw = outputs[i + num_strides]   # [N, 4]

                if scores_raw.ndim == 2:
                    scores_raw = scores_raw[:, 0]

                mask = scores_raw > CONF_THRESH
                if not mask.any():
                    continue

                scores = scores_raw[mask]
                bboxes = bboxes_raw[mask]

                # Tọa độ letterbox → ảnh gốc
                bboxes_orig = bboxes / scale

                for j, (box, sc) in enumerate(zip(bboxes_orig, scores)):
                    x1 = int(np.clip(box[0], 0, orig_w))
                    y1 = int(np.clip(box[1], 0, orig_h))
                    x2 = int(np.clip(box[2], 0, orig_w))
                    y2 = int(np.clip(box[3], 0, orig_h))
                    all_boxes.append([x1, y1, x2, y2])
                    all_scores.append(float(sc))
                    all_kps.append(None)   # landmark parse cần thêm bước

        except Exception as exc:
            logger.debug("SCRFD output parse error: %s", exc)

        if not all_boxes:
            return []

        # NMS
        boxes_arr  = np.array(all_boxes, dtype=np.float32)
        scores_arr = np.array(all_scores, dtype=np.float32)
        keep = self._nms(boxes_arr, scores_arr, NMS_THRESH)

        return [
            (
                (all_boxes[k][0], all_boxes[k][1], all_boxes[k][2], all_boxes[k][3]),
                all_scores[k],
                all_kps[k],
            )
            for k in keep
        ]

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
        """NMS đơn giản theo diện tích giao nhau / hội (IoU)."""
        if len(boxes) == 0:
            return []
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        keep  = []
        while len(order):
            i = order[0]
            keep.append(int(i))
            if len(order) == 1:
                break
            iou = (
                np.maximum(0, np.minimum(x2[i], x2[order[1:]]) - np.maximum(x1[i], x1[order[1:]])) *
                np.maximum(0, np.minimum(y2[i], y2[order[1:]]) - np.maximum(y1[i], y1[order[1:]]))
            ) / np.maximum(1e-6, areas[i] + areas[order[1:]])
            order = order[1:][iou < iou_thresh]
        return keep

    # ──────────────────────────────────────────────────────────────────────────
    # Căn chỉnh khuôn mặt (Face Alignment)
    # ──────────────────────────────────────────────────────────────────────────

    def _align_face(
        self,
        face_crop: np.ndarray,
        landmarks_5pt: np.ndarray | None,
    ) -> np.ndarray:
        """
        Căn chỉnh và chuẩn hóa khuôn mặt về ảnh 112×112 px chuẩn ArcFace.
        Nếu có 5-point landmark → dùng AffineTransform chính xác.
        Nếu không có landmark → chỉ resize đơn giản.
        """
        TARGET_SIZE = (112, 112)

        if landmarks_5pt is not None and len(landmarks_5pt) == 5:
            # Template điểm chuẩn ArcFace 112x112
            dst_pts = np.array([
                [38.2946, 51.6963],
                [73.5318, 51.5014],
                [56.0252, 71.7366],
                [41.5493, 92.3655],
                [70.7299, 92.2041],
            ], dtype=np.float32)
            M, _ = cv2.estimateAffinePartial2D(landmarks_5pt.astype(np.float32), dst_pts)
            if M is not None:
                aligned = cv2.warpAffine(face_crop, M, TARGET_SIZE, flags=cv2.INTER_LINEAR)
                return aligned

        return cv2.resize(face_crop, TARGET_SIZE, interpolation=cv2.INTER_LINEAR)

    # ──────────────────────────────────────────────────────────────────────────
    # Anti-spoofing (MiniFASNet)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_anti_spoof(self, face_112: np.ndarray) -> bool:
        """
        Kiểm tra xem khuôn mặt có phải là thật hay bị giả mạo (spoofed).
        Dùng MiniFASNet (ONNX) nhận input 80×80.
        Trả về True nếu là khuôn mặt thật, False nếu phát hiện giả mạo.
        """
        try:
            inp = cv2.resize(face_112, (80, 80))
            inp = inp[:, :, ::-1].astype(np.float32)   # BGR→RGB
            inp = inp.transpose(2, 0, 1)[np.newaxis]   # [1, 3, 80, 80]
            # Chuẩn hóa ImageNet
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
            std  = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
            inp  = (inp / 255.0 - mean) / std

            input_name = self._anti_spoof.get_inputs()[0].name
            output = self._anti_spoof.run(None, {input_name: inp.astype(np.float32)})[0]

            # Output [1, 2]: [fake_prob, real_prob]
            probs = output[0]
            real_prob = probs[1] if len(probs) >= 2 else probs[0]
            is_real = float(real_prob) > 0.60
            if not is_real:
                logger.debug("Anti-spoof: real_prob=%.3f → SPOOF", real_prob)
            return is_real
        except Exception as exc:
            logger.debug("Anti-spoof error: %s — pass-through", exc)
            return True   # Fail-open: nếu lỗi thì coi là thật để không block luồng

    # ──────────────────────────────────────────────────────────────────────────
    # Trích xuất embedding (ArcFace)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_embedding(self, face_112: np.ndarray) -> np.ndarray:
        """
        Trích xuất vector đặc trưng 512 chiều từ ảnh khuôn mặt 112×112 bằng ArcFace R100.
        Vector được chuẩn hóa L2 trước khi trả về để tính cosine similarity đơn giản.
        """
        inp = face_112[:, :, ::-1].astype(np.float32)    # BGR→RGB
        inp = (inp - 127.5) / 128.0                       # Chuẩn hóa [-1, 1]
        inp = inp.transpose(2, 0, 1)[np.newaxis]          # [1, 3, 112, 112]

        input_name = self._arcface.get_inputs()[0].name
        emb = self._arcface.run(None, {input_name: inp})[0][0]   # [512]

        # Chuẩn hóa L2
        norm = np.linalg.norm(emb)
        return emb / (norm + 1e-6)

    # ──────────────────────────────────────────────────────────────────────────
    # Matching
    # ──────────────────────────────────────────────────────────────────────────

    def _match_embedding(self, embedding: np.ndarray) -> Optional[dict]:
        """
        Tra cứu embedding theo thứ tự ưu tiên (3 tầng):
          1. Redis L2 cache  — tìm staff đã prefetch (nhanh, in-memory).
          2. pgvector ANN    — query PostgreSQL với toán tử <=> cosine distance
                               (chỉ gọi khi Redis miss, kết quả cache lại Redis).
          3. Trả về None     — stranger tracking xử lý tiếp.

        Trả về dict {'id', 'name', 'role', 'score'} hoặc None.
        """
        # ── Tầng 1: Redis L2 cache ────────────────────────────────────────────
        redis_result = self._match_redis(embedding)
        if redis_result is not None:
            return redis_result

        # ── Tầng 2: pgvector ANN (fallback) ──────────────────────────────────
        pgvector_result = self._match_pgvector(embedding)
        if pgvector_result is not None:
            # Cache kết quả trở lại Redis để lần sau nhanh hơn
            self._cache_pgvector_result(pgvector_result)
            return pgvector_result

        return None

    def _match_redis(self, embedding: np.ndarray) -> Optional[dict]:
        """
        Tầng 1: so sánh cosine similarity với tất cả staff đã prefetch trong Redis.
        Brute-force trên tập nhỏ (<2000 nhân viên) — đủ nhanh cho production.
        Trả về dict {id, name, role, score} hoặc None nếu không match.
        """
        if self._redis is None:
            return None
        try:
            keys = self._redis.keys("svpro:staff:*")
            if not keys:
                return None

            best_score  = 0.0
            best_person = None

            for key in keys:
                # Redis client có thể trả về bytes hoặc string tùy phiên bản
                lookup_key = key.decode() if isinstance(key, bytes) else key
                raw = self._redis.get(lookup_key)
                if not raw:
                    continue
                person  = json.loads(raw)
                ref_emb = np.array(person["embedding"], dtype=np.float32)
                # Cosine similarity — cả 2 vector đã L2-normalized nên dot = cosine
                sim = float(np.dot(embedding, ref_emb))
                if sim > best_score:
                    best_score  = sim
                    best_person = person

            if best_person and best_score >= self.recognition_threshold:
                best_person["score"] = best_score
                logger.debug("Redis match: id=%s sim=%.3f", best_person.get("id"), best_score)
                return best_person
        except Exception as exc:
            logger.debug("Redis match error: %s", exc)
        return None

    def _match_pgvector(self, embedding: np.ndarray) -> Optional[dict]:
        """
        Tầng 2: query pgvector với toán tử <=> (cosine distance ANN).
        Dùng psycopg2 đồng bộ (gọi từ background thread của pipeline Savant).

        SQL:
            SELECT id, name, role,
                   1 - (face_embedding <=> $1::vector) AS cosine_sim
            FROM users
            WHERE face_embedding IS NOT NULL AND active = TRUE
            ORDER BY face_embedding <=> $1::vector
            LIMIT 1;

        Chỉ chạy nếu có self.db_dsn được cấu hình trong module YAML.
        Trả về dict hoặc None.
        """
        if not getattr(self, "db_dsn", None):
            return None
        try:
            import psycopg2
            # Chuyển embedding numpy → string pgvector format: '[0.1, 0.2, ...]'
            vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding.tolist()) + "]"

            conn = psycopg2.connect(self.db_dsn, connect_timeout=2)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, role,
                       1 - (face_embedding <=> %s::vector) AS cosine_sim
                FROM users
                WHERE face_embedding IS NOT NULL AND active = TRUE
                ORDER BY face_embedding <=> %s::vector
                LIMIT 1
                """,
                (vec_str, vec_str),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                uid, name, role, cosine_sim = row
                sim = float(cosine_sim)
                if sim >= self.recognition_threshold:
                    logger.debug(
                        "pgvector match: id=%s name=%s sim=%.3f", uid, name, sim
                    )
                    return {"id": uid, "name": name, "role": role, "score": sim}
        except Exception as exc:
            logger.warning("pgvector query failed: %s", exc)
        return None

    def _cache_pgvector_result(self, person: dict) -> None:
        """
        Cache kết quả pgvector trở lại Redis để lần sau tra cứu nhanh hơn.
        TTL = 5 phút (giống staff prefetch).
        """
        if self._redis is None or not person.get("id"):
            return
        try:
            key     = f"svpro:staff:{person['id']}"
            payload = json.dumps(person)
            self._redis.setex(key, 300, payload)
            logger.debug("pgvector result cached to Redis: key=%s", key)
        except Exception as exc:
            logger.debug("Redis cache write failed: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Stranger Tracking
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_stranger(
        self,
        source_id: str,
        track_id: int,
        embedding: np.ndarray,
        face_crop: np.ndarray,
        quality: float,
        now: float,
    ) -> None:
        """
        Theo dõi người lạ theo track_id. Sau khi tích lũy đủ _STRANGER_MIN_FRAMES
        frame chất lượng, tạo ID và lưu vào DB (nếu chưa dedup).
        """
        key = (source_id, track_id)

        # Kiểm tra dedup: đã lưu stranger này gần đây chưa
        last_saved = self._stranger_saved_ts.get(key, 0.0)
        if now - last_saved < _STRANGER_DEDUP_SECS:
            return

        state = self._strangers.get(key)
        if state is None:
            state = _StrangerState(track_id, source_id, now)
            self._strangers[key] = state

        state.add_frame(embedding, face_crop, quality)
        state.last_seen = now

        if state.quality_frames >= _STRANGER_MIN_FRAMES and not state.saved:
            stranger_id = state.generate_id()
            state.stranger_id = stranger_id
            state.saved = True
            self._stranger_saved_ts[key] = now
            logger.info(
                "New stranger: id=%s source=%s track=%s frames=%d quality=%.2f",
                stranger_id, source_id, track_id, state.quality_frames, state.best_quality_score,
            )
            # Đăng ký stranger vào Re-ID registry để match đa camera
            if stranger_registry is not None:
                stranger_registry.register(
                    stranger_id=stranger_id,
                    embedding=state.centroid,
                    camera_id=source_id,
                    extra={"quality_frames": state.quality_frames},
                )
            # Ghi vào L1 cache để không xử lý lại
            self._l1_cache.put(track_id, {
                "person_id": stranger_id,
                "person_name": "Stranger",
                "person_role": "unknown",
                "fr_confidence": round(state.best_quality_score, 4),
                "is_stranger": True,
            })

    def _flush_expired_tracks(self, source_id: str, now: float) -> None:
        """
        Dọn dẹp các track không còn hoạt động (không thấy trong _TRACK_MAX_AGE giây).
        Giải phóng bộ nhớ và đảm bảo stranger state không tích lũy vô hạn.
        """
        active = self._active_tracks.get(source_id, {})
        expired = [tid for tid, ts in active.items() if now - ts > _TRACK_MAX_AGE]
        for tid in expired:
            del active[tid]
            self._strangers.pop((source_id, tid), None)
            self._l1_cache.invalidate(tid)

    def _flush_all_strangers(self) -> None:
        """Dọn sạch tất cả trạng thái stranger khi pipeline dừng."""
        self._strangers.clear()
        self._active_tracks.clear()

    # ──────────────────────────────────────────────────────────────────────────
    # Ghi kết quả vào NvDs metadata
    # ──────────────────────────────────────────────────────────────────────────

    def _write_attr(self, frame_meta: NvDsFrameMeta, track_id: int, data: dict) -> None:
        """
        Gắn kết quả nhận diện vào object metadata của DeepStream.
        JSON egress sẽ đọc các attribute này để xuất ra downstream.
        """
        for obj_meta in frame_meta.objects:
            if getattr(obj_meta, "track_id", None) == track_id:
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_PERSON, data.get("person_id", ""),    1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_NAME,   data.get("person_name", ""),  1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_ROLE,   data.get("person_role", ""),  1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_CONF,   data.get("fr_confidence", 0), 1.0)
                break

    def _find_track_id(self, frame_meta: NvDsFrameMeta, bbox: tuple) -> int:
        """
        Tìm track_id của DeepStream object gần nhất với bbox phát hiện được từ SCRFD.
        Dùng center-distance để khớp nếu không có ID tracker trực tiếp.
        Trả về track_id (int) dương nếu tìm được, hoặc hash của bbox nếu không tìm thấy.
        """
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        best_id   = None
        best_dist = float("inf")

        for obj_meta in frame_meta.objects:
            bb  = obj_meta.bbox
            ocx = bb.left + bb.width  / 2
            ocy = bb.top  + bb.height / 2
            d   = ((cx - ocx) ** 2 + (cy - ocy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_id   = getattr(obj_meta, "track_id", None)

        # Nếu không tìm được tracker hoặc khoảng cách quá xa → dùng hash bbox
        if best_id is None or best_dist > 100:
            best_id = abs(hash((x1, y1, x2, y2))) % (10 ** 8)

        return best_id
