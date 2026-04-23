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
import queue
import threading
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from typing import Optional

import cv2
import numpy as np

from savant.deepstream.meta.frame import NvDsFrameMeta
from savant.deepstream.pyfunc import NvDsPyFuncPlugin
from savant.deepstream.opencv_utils import nvds_to_gpu_mat

from .face_quality import compute_quality_score, _MIN_COMPOSITE
from .face_align import align_face as _shared_align_face
from .stranger_reid import stranger_registry
from .enrollment_service import start_enrollment_server
from src.telemetry import metrics

logger = logging.getLogger(__name__)

# ── Timezone Việt Nam ───────────────────────────────────────────────────────────
_VN_TZ = timezone(timedelta(hours=7))

# ── Ngưỡng nhận diện & theo dõi (tuned for latency + Re-ID accuracy) ────────
# Face detector (YOLOv8-face)
_FACE_DET_CONF_THRESH      = 0.55   # raised từ 0.30 → 0.55 để loại false-positive
                                    # trên non-face (tay, tường, mannequin patterns)
_FACE_DET_NMS_THRESH       = 0.40
_FACE_MIN_PX               = 80     # bbox face phải ≥ 80×80 px (~6400 area)
                                    # → loại face quá xa cam, vỡ, không nhận diện được

_RECOGNITION_THRESHOLD     = 0.42   # cosine sim — match known person
# Cần ≥4 quality frame trước khi commit stranger mới (≈500ms ở 8fps).
# Ít frame → embedding không ổn định ở cam xa → cùng người tạo 2+ stranger ID.
_STRANGER_MIN_FRAMES       = 2     # Giảm 4 → 2 sau khi tích hợp YOLOv8-face +
                                    # min face size 80px + composite 0.50 đã filter
                                    # rất sạch noise. 2 frame quality + motion check
                                    # đủ commit. Latency stranger: 4×125ms → 2×125ms.
_STRANGER_DEDUP_SECS       = 300.0  # Không tạo stranger mới cho cùng track trong 5 phút
_STRANGER_REFRESH_SECS     = 90.0   # Cho phép replace ảnh đẹp hơn trong 90s sau save
_TRACK_MAX_AGE             = 8.0    # Xóa track nếu không thấy trong 8 giây
# Re-ID threshold 0.40: hơi rộng để bắt cùng người ở góc/ánh sáng khác.
# Gallery K=5 bù recall: match nếu giống BẤT KỲ 1 trong 5 exemplar.
_STRANGER_REID_THRESHOLD   = 0.40
_STRANGER_REID_BOOTSTRAP_N = 200
# Throttle: ghi 1 event/stranger/45s
_STRANGER_EVENT_COOLDOWN_S = 45.0
# Gallery: tối đa K=5 embedding/stranger, chỉ insert khi quality ≥ 0.60
_STRANGER_GALLERY_K        = 5
_STRANGER_GALLERY_MIN_QUAL = 0.60

# ── L1 Cache ────────────────────────────────────────────────────────────────────
_L1_CAPACITY = 1000
_L1_TTL_SECS = 60.0

# ── Label nhận diện gắn vào metadata ──────────────────────────────────────────
_ATTR_ELEMENT  = "fr"
_ATTR_PERSON   = "person_id"
_ATTR_NAME     = "person_name"
_ATTR_ROLE     = "person_role"
_ATTR_CONF     = "fr_confidence"
_ATTR_IMG      = "image_path"   # Đường dẫn tương đối ảnh face crop trong /Detect


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

    def clear(self) -> None:
        """Xóa toàn bộ cache — gọi sau khi staff embeddings bị thay đổi."""
        self._cache.clear()


# ── Stranger Track State ────────────────────────────────────────────────────────

class _StrangerState:
    """
    Lưu trạng thái theo dõi một người lạ (stranger) chưa được nhận diện.
    Tích lũy embedding từ nhiều frame để tạo profile ổn định hơn.
    """

    __slots__ = (
        "stranger_id", "track_id", "source_id",
        "quality_frames", "embeddings",
        "best_face_crop", "best_quality_score", "best_quality_at",
        "first_seen", "last_seen", "saved", "saved_at",
        "positions",
    )

    def __init__(self, track_id: int, source_id: str, now: float):
        self.stranger_id: str | None = None
        self.track_id    = track_id
        self.source_id   = source_id
        self.quality_frames: int = 0
        self.embeddings: list[np.ndarray] = []
        self.best_face_crop: np.ndarray | None = None
        self.best_quality_score: float = 0.0
        self.best_quality_at: float = 0.0       # monotonic time của frame best hiện tại
        self.first_seen  = now
        self.last_seen   = now
        self.saved       = False
        self.saved_at: float = 0.0              # monotonic time đã save lần cuối
        # Motion liveness: lưu tâm bbox face qua các frame quality-pass → detect
        # vật bất động (mannequin/poster). Giới hạn 32 entries gần nhất.
        self.positions: list[tuple[float, float, float]] = []   # (t, cx, cy)

    def add_frame(
        self,
        embedding: np.ndarray,
        face_crop: np.ndarray,
        quality: float,
        now: float,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> bool:
        """
        Thêm 1 frame chất lượng. Trả True nếu đây là frame BEST mới (ảnh tốt
        hơn lần trước) → caller có thể save replace.
        """
        self.quality_frames += 1
        self.embeddings.append(embedding)
        if bbox is not None:
            cx = (bbox[0] + bbox[2]) * 0.5
            cy = (bbox[1] + bbox[3]) * 0.5
            self.positions.append((now, float(cx), float(cy)))
            if len(self.positions) > 32:
                self.positions = self.positions[-32:]
        if quality > self.best_quality_score:
            self.best_quality_score = quality
            self.best_face_crop = face_crop.copy()
            self.best_quality_at = now
            return True
        return False

    def is_static(self, min_std_px: float = 2.5, min_samples: int = 4) -> bool:
        """
        True nếu bbox face gần như không dịch chuyển qua các frame thu được
        → đặc trưng của mannequin/poster/ảnh in. Yêu cầu ≥ min_samples điểm.
        Đo bằng std độc lập trên trục x và y (đơn vị: pixel trên frame gốc).
        """
        if len(self.positions) < min_samples:
            return False
        xs = [p[1] for p in self.positions]
        ys = [p[2] for p in self.positions]
        n  = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        var_x = sum((x - mx) ** 2 for x in xs) / n
        var_y = sum((y - my) ** 2 for y in ys) / n
        std_x = var_x ** 0.5
        std_y = var_y ** 0.5
        return std_x < min_std_px and std_y < min_std_px

    def temporal_spread_ok(self, min_spread_sec: float) -> bool:
        """
        Kiểm tra temporal voting: các frame tích luỹ phải TRẢI RỘNG trong
        ≥ min_spread_sec giây. Nếu tất cả ở trong 1 burst ngắn (<spread) →
        có thể là noise flash / lóe sáng / motion blur tạm thời → chưa commit.

        Đảm bảo stranger "thật sự xuất hiện" chứ không phải fluke 1 khoảnh khắc.
        """
        if len(self.positions) < 2:
            return False
        t_first = self.positions[0][0]
        t_last  = self.positions[-1][0]
        return (t_last - t_first) >= min_spread_sec

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
      yolov8_face_model_path – Đường dẫn model YOLOv8-face ONNX.
      arcface_model_path     – Đường dẫn model ArcFace R100 ONNX.
      anti_spoof_model_path  – Đường dẫn model MiniFASNet ONNX.
      recognition_threshold – Ngưỡng cosine similarity để nhận diện (mặc định 0.55).
      enable_anti_spoof     – Bật/tắt kiểm tra giả mạo (mặc định True).
      redis_host / redis_port / redis_db – Thông tin kết nối Redis.
      db_dsn                – PostgreSQL DSN string để lưu stranger.
    """

    def __init__(
        self,
        yolov8_face_model_path: str = "/models/yolov8_face/yolov8n-face.onnx",
        arcface_model_path: str = "/models/glintr100.onnx",
        anti_spoof_model_path: str = "/models/anti_spoof/minifasnet.onnx",
        recognition_threshold: float = _RECOGNITION_THRESHOLD,
        enable_anti_spoof: bool = True,
        save_crops: bool = False,
        save_dir: str = "/Detect/faces",
        redis_host: str = "redis",
        redis_port: int = 6379,
        redis_db: int = 0,
        db_dsn: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.yolov8_face_model_path  = yolov8_face_model_path
        self.arcface_model_path      = arcface_model_path
        self.anti_spoof_model_path   = anti_spoof_model_path
        self.recognition_threshold = recognition_threshold
        self.enable_anti_spoof     = enable_anti_spoof
        self.save_crops            = save_crops
        self.save_dir              = save_dir
        self.redis_host            = redis_host
        self.redis_port            = redis_port
        self.redis_db              = redis_db
        self.db_dsn                = db_dsn

        # Models (khởi tạo trong on_start)
        self._yolov8_face = None  # YOLOv8FaceDetector wrapper (face detector)
        self._arcface    = None   # ONNX session cho ArcFace
        self._anti_spoof = None   # AntispoofModel (MiniFASNet)

        # In-process staff embedding cache — matrix (N, 512) + meta list.
        # Được populate bởi _prefetch_staff_embeddings() khi startup + mỗi
        # reload. Match mỗi frame = `matrix @ embedding` vectorized (~100µs
        # cho N=100 vs ~3ms parse JSON N lần + loop).
        self._staff_matrix: np.ndarray | None = None
        self._staff_meta:   list[dict] = []

        # Cache & tracking
        self._l1_cache   = _L1Cache()
        self._redis      = None   # Redis client
        self._disabled    = False  # Graceful degradation flag

        # Stranger tracking: {(source_id, track_id): _StrangerState}
        self._strangers: dict[tuple, _StrangerState] = {}
        # Dedup PER track_id: {(source_id, track_id): last_saved_timestamp}
        self._stranger_saved_ts: dict[tuple, float] = {}
        # Throttle PER stranger_id (toàn cục, đa camera): chống spam khi cùng 1
        # người xuất hiện liên tục → chỉ ghi event tối đa 1 lần / cooldown.
        self._stranger_event_last: dict[str, float] = {}
        # Lock để cập nhật atomic guest_faces UPSERT từ background worker / handler
        self._stranger_db_lock = threading.Lock()

        # Active tracks per source để flush expired
        self._active_tracks: dict[str, dict[int, float]] = {}   # source → {track_id: last_seen}

        # Background save queue
        if self.save_crops:
            self._save_queue: queue.Queue = queue.Queue(maxsize=300)
            self._save_thread = threading.Thread(
                target=self._save_worker, daemon=True, name="face-save"
            )
            self._save_thread.start()
        else:
            self._save_queue = None

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
            # Bootstrap stranger Re-ID từ guest_faces — survive restart container.
            # Nếu DB rỗng hoặc lỗi thì registry chỉ có entry mới sau restart.
            try:
                self._bootstrap_stranger_registry()
            except Exception as exc:
                logger.warning("Stranger registry bootstrap failed: %s", exc)
            # Wire Redis client của FR vào registry để share đa camera
            if stranger_registry is not None and self._redis is not None:
                stranger_registry._redis = self._redis
            # Khởi động Enrollment HTTP server — tái dụng SCRFD + ArcFace đã load
            # Chạy trên port 8090 (internal), không block pipeline (daemon thread)
            # reload_callback: backend gọi POST /internal/reload-embeddings sau
            # khi enroll → invalidate L1 cache + re-prefetch staff embeddings
            # → người vừa enroll match được NGAY (không đợi Redis TTL 5p).
            def _reload_staff():
                try:
                    self._l1_cache.clear() if hasattr(self._l1_cache, "clear") else None
                except Exception:
                    pass
                self._prefetch_staff_embeddings()
                logger.info("[reload] Staff embeddings reloaded after enroll/update")

            start_enrollment_server(
                yolov8_face     = self._yolov8_face,
                arcface_session = self._arcface,
                port            = int(os.environ.get("ENROLLMENT_PORT", "8090")),
                reload_callback = _reload_staff,
            )
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
          1. YOLOv8-face  (phát hiện mặt + 5-point landmarks)
          2. ArcFace R100 (trích xuất embedding)
          3. MiniFASNet   (anti-spoofing, tùy chọn)
        Chạy warmup để tránh spike latency trên frame đầu tiên.
        """
        import onnxruntime as ort
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        # ── Face detector: YOLOv8-face ─────────────────────────────────────
        from .yolov8_face import YOLOv8FaceDetector
        logger.warning("Loading YOLOv8-face: %s", self.yolov8_face_model_path)
        self._yolov8_face = YOLOv8FaceDetector(
            model_path  = self.yolov8_face_model_path,
            providers   = providers,
            conf_thresh = _FACE_DET_CONF_THRESH,   # threshold dùng chung cho mọi face detector
            nms_thresh  = _FACE_DET_NMS_THRESH,
        )

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
        logger.info("Warming up YOLOv8-face ...")
        dummy_bgr = np.zeros((640, 640, 3), dtype=np.uint8)
        self._yolov8_face.detect(dummy_bgr)

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
        Tải embedding của MỌI role active (staff + admin + blacklist + guest)
        vào:
          (1) In-process numpy matrix `_staff_matrix` shape (N, 512) — dùng
              cho `_match_redis` vectorized dot product (nhanh gấp 10-20× so
              với parse JSON N lần + loop).
          (2) Redis hash `svpro:staff:hash` — backup khi AI core restart khác
              process, tương thích với code cũ đọc Redis.

        Gọi khi startup + mỗi lần backend gọi `/internal/reload-embeddings`.
        """
        # Cleanup in-process cache cũ
        self._staff_matrix = None
        self._staff_meta:   list[dict] = []

        if not self.db_dsn:
            logger.info("Skipping staff prefetch (no DB DSN).")
            return
        try:
            import psycopg2
            conn = psycopg2.connect(self.db_dsn)
            cur  = conn.cursor()
            cur.execute(
                "SELECT id, name, role, face_embedding FROM users "
                "WHERE face_embedding IS NOT NULL AND active = TRUE"
            )
            rows = cur.fetchall()
            if not rows:
                logger.info("No active user with embedding — skip prefetch.")
                cur.close(); conn.close()
                return

            # ── Build in-process numpy matrix (fast path) ──────────────────
            embeddings = []
            meta = []
            for uid, name, role, embedding_str in rows:
                try:
                    # pgvector text format '[0.1, 0.2, ...]' — parse 1 lần
                    emb = np.fromstring(embedding_str.strip("[]"), sep=",", dtype=np.float32)
                    if emb.shape[0] != 512:
                        continue
                except Exception:
                    continue
                embeddings.append(emb)
                meta.append({"id": uid, "name": name, "role": role})

            if embeddings:
                self._staff_matrix = np.stack(embeddings, axis=0)   # (N, 512)
                self._staff_meta   = meta
                logger.warning(
                    "Prefetched %d staff embeddings → numpy matrix (%d, 512)",
                    len(embeddings), self._staff_matrix.shape[0],
                )

            # ── Mirror to Redis hash (backup path) ─────────────────────────
            if self._redis is not None:
                try:
                    pipe = self._redis.pipeline(transaction=False)
                    pipe.delete("svpro:staff:hash")
                    for (uid, name, role, _), emb in zip(rows, embeddings):
                        payload = json.dumps({
                            "id": uid, "name": name, "role": role,
                            "embedding": emb.tolist(),
                        })
                        pipe.hset("svpro:staff:hash", uid, payload)
                    pipe.execute()
                except Exception as exc:
                    logger.warning("Redis mirror failed (continuing): %s", exc)

            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning("Staff prefetch failed: %s — continuing without cache.", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Stranger persistence + bootstrap (guest_faces table)
    # ──────────────────────────────────────────────────────────────────────────

    def _bootstrap_stranger_registry(self) -> None:
        """
        Nạp `_STRANGER_REID_BOOTSTRAP_N` stranger gần đây nhất từ guest_faces vào
        local registry để Re-ID hoạt động ngay sau restart container.
        """
        if stranger_registry is None or not self.db_dsn:
            return
        try:
            import psycopg2
            conn = psycopg2.connect(self.db_dsn)
            cur  = conn.cursor()
            cur.execute(
                "SELECT stranger_id, face_embedding::text, source_id "
                "FROM guest_faces "
                "WHERE face_embedding IS NOT NULL "
                "ORDER BY last_seen DESC NULLS LAST LIMIT %s",
                (_STRANGER_REID_BOOTSTRAP_N,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
            loaded = 0
            for sid, emb_str, src in rows:
                try:
                    # pgvector cast ::text → "[0.1,0.2,...]"
                    emb = np.array(
                        json.loads(emb_str.replace(" ", "")),
                        dtype=np.float32,
                    )
                    stranger_registry.register(
                        stranger_id = sid,
                        embedding   = emb,
                        camera_id   = src or "bootstrap",
                    )
                    loaded += 1
                except Exception:
                    continue
            logger.warning("[FR] Bootstrap stranger registry: loaded %d entries", loaded)
        except Exception as exc:
            logger.warning("Bootstrap stranger registry skipped: %s", exc)

    def _upsert_guest_face(
        self,
        stranger_id: str,
        centroid: np.ndarray,
        source_id: str,
        quality_frames: int,
        is_new: bool,
    ) -> None:
        """
        UPSERT 1 row vào guest_faces.
        - is_new=True → INSERT mới với embedding
        - is_new=False → UPDATE last_seen, append camera vào metadata.cameras_seen,
          tăng metadata.appearances. Embedding không cập nhật mỗi frame để rẻ.
        Thread-safe (background save thread cũng có thể gọi).
        """
        if not self.db_dsn:
            return
        emb_str = "[" + ",".join(f"{v:.6f}" for v in centroid.tolist()) + "]"
        with self._stranger_db_lock:
            try:
                import psycopg2
                conn = psycopg2.connect(self.db_dsn)
                cur  = conn.cursor()
                if is_new:
                    cur.execute(
                        """
                        INSERT INTO guest_faces
                          (stranger_id, source_id, first_seen, last_seen,
                           quality_frames, face_embedding, metadata_json)
                        VALUES
                          (%s, %s, NOW(), NOW(), %s, %s::vector,
                           jsonb_build_object('cameras_seen', jsonb_build_array(%s),
                                              'appearances', 1))
                        ON CONFLICT (stranger_id) DO UPDATE SET
                          last_seen      = NOW(),
                          quality_frames = guest_faces.quality_frames + EXCLUDED.quality_frames,
                          metadata_json  = COALESCE(guest_faces.metadata_json, '{}'::jsonb) ||
                                           jsonb_build_object(
                                             'appearances',
                                               COALESCE((guest_faces.metadata_json->>'appearances')::int, 0) + 1
                                           )
                        """,
                        (stranger_id, source_id, quality_frames, emb_str, source_id),
                    )
                else:
                    # Re-ID match: append camera nếu chưa có; tăng appearances.
                    # Dùng jsonb @> (contains) thay vì ? để khỏi confict syntax với
                    # psycopg2 placeholder parser.
                    cur.execute(
                        """
                        UPDATE guest_faces SET
                          last_seen      = NOW(),
                          quality_frames = quality_frames + %s,
                          metadata_json  = jsonb_set(
                            jsonb_set(
                              COALESCE(metadata_json, '{}'::jsonb),
                              '{appearances}',
                              to_jsonb(COALESCE((metadata_json->>'appearances')::int, 0) + 1)
                            ),
                            '{cameras_seen}',
                            CASE WHEN COALESCE(metadata_json->'cameras_seen', '[]'::jsonb) @> jsonb_build_array(%s::text)
                                 THEN COALESCE(metadata_json->'cameras_seen', '[]'::jsonb)
                                 ELSE COALESCE(metadata_json->'cameras_seen', '[]'::jsonb) || to_jsonb(%s::text)
                            END
                          )
                        WHERE stranger_id = %s
                        """,
                        (quality_frames, source_id, source_id, stranger_id),
                    )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as exc:
                logger.error("guest_faces UPSERT failed: %s", exc)

    def _update_guest_face_last_image(self, stranger_id: str, image_path: str) -> None:
        """Cập nhật last_image_path để FE hiển thị thumbnail mới nhất."""
        if not self.db_dsn:
            return
        with self._stranger_db_lock:
            try:
                import psycopg2
                conn = psycopg2.connect(self.db_dsn)
                cur  = conn.cursor()
                cur.execute(
                    "UPDATE guest_faces SET metadata_json = "
                    "  jsonb_set(COALESCE(metadata_json, '{}'::jsonb), "
                    "            '{last_image_path}', to_jsonb(%s::text)) "
                    "WHERE stranger_id = %s",
                    (image_path, stranger_id),
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as exc:
                logger.debug("update last_image_path failed: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Multi-embedding gallery (Re-ID precision)
    # ──────────────────────────────────────────────────────────────────────────

    def _gallery_lookup(self, embedding: np.ndarray) -> tuple[str | None, float]:
        """
        Query stranger_embeddings — lấy stranger_id có embedding gần nhất.
        Trả (stranger_id, similarity). similarity = 1 - cosine_distance.
        Nếu similarity < threshold → trả (None, best_sim).
        """
        if not self.db_dsn:
            return None, 0.0
        emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding.tolist()) + "]"
        try:
            import psycopg2
            conn = psycopg2.connect(self.db_dsn)
            cur  = conn.cursor()
            # Lấy top 5 nearest, group by stranger_id, mỗi stranger lấy MIN distance
            # → trả về stranger có embedding nào gần nhất (regardless of which exemplar)
            cur.execute(
                """SELECT stranger_id, MIN(embedding <=> %s::vector) AS dist
                   FROM stranger_embeddings
                   GROUP BY stranger_id
                   ORDER BY dist
                   LIMIT 1""",
                (emb_str,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return None, 0.0
            sid, dist = row[0], float(row[1])
            sim = 1.0 - dist
            if sim >= _STRANGER_REID_THRESHOLD:
                return sid, sim
            return None, sim
        except Exception as exc:
            logger.debug("gallery_lookup failed: %s", exc)
            return None, 0.0

    def _gallery_insert(
        self,
        stranger_id: str,
        embedding: np.ndarray,
        quality: float,
        source_id: str,
    ) -> None:
        """
        INSERT 1 embedding vào gallery cho stranger này.
        Nếu gallery đã đầy K embedding → DELETE lowest-quality trước.
        Chỉ insert nếu quality >= _STRANGER_GALLERY_MIN_QUAL.
        """
        if not self.db_dsn or quality < _STRANGER_GALLERY_MIN_QUAL:
            return
        emb_str = "[" + ",".join(f"{v:.6f}" for v in embedding.tolist()) + "]"
        with self._stranger_db_lock:
            try:
                import psycopg2
                conn = psycopg2.connect(self.db_dsn)
                cur  = conn.cursor()
                # Insert
                cur.execute(
                    """INSERT INTO stranger_embeddings
                          (stranger_id, embedding, quality, source_id)
                       VALUES (%s, %s::vector, %s, %s)""",
                    (stranger_id, emb_str, quality, source_id),
                )
                # Evict — giữ tối đa K rows quality cao nhất
                cur.execute(
                    """DELETE FROM stranger_embeddings
                       WHERE id IN (
                         SELECT id FROM stranger_embeddings
                         WHERE stranger_id = %s
                         ORDER BY quality DESC, created_at DESC
                         OFFSET %s
                       )""",
                    (stranger_id, _STRANGER_GALLERY_K),
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception as exc:
                logger.debug("gallery_insert failed: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Xử lý frame
    # ──────────────────────────────────────────────────────────────────────────

    # Per-source frame counter (heartbeat — log mỗi 1000 frame ≈ 100s @10fps)
    _per_source_frame_cnt: dict[str, int] = {}

    def process_frame(self, buffer, frame_meta: NvDsFrameMeta) -> None:
        """
        Hàm chính xử lý mỗi khung hình từ DeepStream pipeline:
          1. Lấy ảnh từ GPU memory sang numpy.
          2. Phát hiện person crop từ YOLOv8 → chạy SCRFD trên crop (không full frame).
          3. Lọc chất lượng (Face Quality Filter).
          4. Kiểm tra giả mạo (Anti-spoofing, nếu bật).
          5. Trích xuất embedding (ArcFace).
          6. Tra cứu L1 → Redis → DB (nhận diện hoặc stranger tracking).
          7. Ghi kết quả vào object metadata để egress xuất JSON.
        """
        source_id = str(frame_meta.source_id)
        cnt = self._per_source_frame_cnt.get(source_id, 0) + 1
        self._per_source_frame_cnt[source_id] = cnt
        if cnt % 1000 == 0:
            logger.info("[FR] src=%s heartbeat %d frames", source_id, cnt)
        # `now` dùng cho dedup TTL/expire — giữ time.monotonic() (không bị chỉnh giờ).
        # `wall` dùng cho timestamp thật khi save/log.
        now       = time.monotonic()
        wall      = time.time()

        if getattr(self, "_disabled", False):
            return

        # Telemetry: frames processed in AI core (by source_id)
        try:
            metrics.frames_processed_total.labels(source_id=source_id).inc()
        except Exception:
            pass

        # Lấy ảnh frame từ GPU buffer. nvjpeg/cuda có thể fail thoáng qua
        # (decode error #6 khi batch corrupt) — chỉ skip frame, KHÔNG raise.
        try:
            with nvds_to_gpu_mat(buffer, frame_meta.frame_meta) as gpu_mat:
                frame_rgba = gpu_mat.download()
            if frame_rgba is None or frame_rgba.size == 0:
                return
            if frame_rgba.ndim == 3 and frame_rgba.shape[2] == 4:
                frame_bgr = cv2.cvtColor(frame_rgba, cv2.COLOR_RGBA2BGR)
            else:
                frame_bgr = np.ascontiguousarray(frame_rgba)
        except (cv2.error, Exception) as exc:
            dl_cnt = getattr(self, "_dl_fail_cnt", 0) + 1
            self._dl_fail_cnt = dl_cnt
            if dl_cnt % 500 == 0:
                logger.warning("[FR src=%s] Download fail ×%d: %s", source_id, dl_cnt, exc)
            return

        # ── Detection strategy: SCRFD trên CROP của person bbox ─────────────
        # Lý do: cam giám sát xa, mặt người chỉ chiếm ~30px trong frame
        # 2592×1944. Nếu chạy SCRFD trên full-frame letterbox 640², mặt còn
        # ~10px → SCRFD không detect. Chạy SCRFD trên person crop (~100×250
        # px) sau khi letterbox về 640² thì mặt còn ~80px → detect tốt.
        #
        # Lấy person bbox từ YOLOv8 (qua frame_meta.objects). Với mỗi person:
        #   1. Crop từ frame_bgr (thêm margin 10% để landmark có context)
        #   2. SCRFD trên crop → detect mặt
        #   3. Convert toạ độ mặt từ crop space → frame space (cộng offset)
        # Fallback: nếu không có person nào → SCRFD full-frame (cảnh không
        # có YOLOv8 person, ví dụ camera selfie sát).
        person_objs = []
        for obj in frame_meta.objects:
            try:
                if obj.label == "person":
                    bb = obj.bbox
                    px1 = max(0, int(bb.left))
                    py1 = max(0, int(bb.top))
                    px2 = min(frame_bgr.shape[1], int(bb.left + bb.width))
                    py2 = min(frame_bgr.shape[0], int(bb.top  + bb.height))
                    person_objs.append((px1, py1, px2, py2))
            except Exception:
                continue

        detections: list = []
        try:
            with metrics.aicore_inference_ms.labels(camera_id=source_id, model="yolov8_face").time():
                if person_objs:
                    detections = self._detect_faces_on_persons(frame_bgr, person_objs)
                # Fallback: nếu person-crop không cho ra detection nào (ví dụ person
                # nhỏ < 50px), thử full-frame detection. Tốn hơn nhưng bắt
                # được face khi không có YOLOv8 person bbox.
                if not detections:
                    detections = self._detect_full_frame(frame_bgr)
        except Exception:
            detections = self._detect_full_frame(frame_bgr)

        if not detections:
            cnt = getattr(self, "_no_face_cnt", 0) + 1
            self._no_face_cnt = cnt
            if cnt % 500 == 0:
                logger.warning(
                    "[FR src=%s] Face det no faces ×%d (persons=%d, frame=%dx%d)",
                    source_id, cnt, len(person_objs),
                    frame_bgr.shape[1], frame_bgr.shape[0],
                )
            self._flush_expired_tracks(source_id, now)
            return
        self._no_face_cnt = 0
        logger.warning(
            "[FR src=%s] Face det %d face(s) from %d person crop(s): scores=%s",
            source_id, len(detections), len(person_objs),
            [round(d[1], 3) for d in detections[:5]],
        )

        # Cập nhật danh sách track đang active
        active = self._active_tracks.setdefault(source_id, {})

        # Publish face bboxes + metadata cho FE overlay (qua BlacklistEngine → Redis).
        # Savant NvDsFrameMeta.set_tag chỉ nhận (str,str) nên serialize JSON.
        # Chỉ publish metadata L1-cached (match sẵn) — stranger flow commit sau.
        overlay_faces = []
        for bbox_o, score_o, _kp in detections:
            tid_o = self._find_track_id(frame_meta, bbox_o)
            cached_o = self._l1_cache.get(tid_o)
            entry = {
                "bbox":     [int(bbox_o[0]), int(bbox_o[1]), int(bbox_o[2]), int(bbox_o[3])],
                "track_id": tid_o,
                "score":    round(float(score_o), 3),
            }
            if cached_o:
                for k in ("person_id", "person_name", "person_role", "fr_confidence", "is_stranger"):
                    if k in cached_o:
                        entry[k] = cached_o[k]
            overlay_faces.append(entry)
        if overlay_faces:
            try:
                import json as _json
                frame_meta.set_tag("fr_face_bboxes", _json.dumps(overlay_faces))
            except Exception:
                pass

        for bbox, score, landmarks in detections:
            x1, y1, x2, y2 = bbox
            # Mở rộng 30% để có context (tóc/cằm) cho cả crop save lẫn alignment.
            # Margin 60% — crop rộng hơn để bao tóc/cằm/cổ/vai (ảnh save đẹp
            # hơn + FE thumbnail nhìn rõ mặt). 30% cũ quá chặt, mặt bị cắt cằm.
            ex1, ey1, ex2, ey2 = self._expand_face_bbox(
                bbox, frame_bgr.shape[1], frame_bgr.shape[0], margin=0.60,
            )
            face_crop = frame_bgr[ey1:ey2, ex1:ex2]   # crop có context cho save
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
            # Alignment chuẩn insightface: warp trực tiếp trên frame đầy đủ với
            # landmarks toạ độ ảnh — không crop trước (tránh lệch origin).
            face_aligned = self._align_face(frame_bgr, landmarks)
            # Cho QC dùng landmarks đã chuyển sang toạ độ tương đối crop
            # (chỉ dùng để tính yaw/pitch — không quan trọng absolute origin).
            lm_local = (
                landmarks.astype(np.float32) - np.array([x1, y1], dtype=np.float32)
                if landmarks is not None else None
            )
            quality, q_details = compute_quality_score(face_aligned, lm_local)
            if quality < _MIN_COMPOSITE:
                qc_cnt = getattr(self, "_qc_fail_cnt", 0) + 1
                self._qc_fail_cnt = qc_cnt
                if qc_cnt % 500 == 0:
                    logger.warning(
                        "[FR src=%s] Face QC fail × %d composite=%.2f %s",
                        source_id, qc_cnt, quality, q_details,
                    )
                try:
                    metrics.fr_recognition_total.labels(camera_id=source_id, result="low_quality").inc()
                except Exception:
                    pass
                continue
            self._qc_fail_cnt = 0

            # ── Anti-spoofing check ─────────────────────────────────────────────
            if self.enable_anti_spoof and self._anti_spoof is not None:
                if not self._check_anti_spoof(face_aligned):
                    sp_cnt = getattr(self, "_spoof_cnt", 0) + 1
                    self._spoof_cnt = sp_cnt
                    if sp_cnt % 500 == 0:
                        logger.warning("[FR src=%s] Spoof × %d (track=%s)",
                                       source_id, sp_cnt, track_id)
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
                
                # Save crop — dùng face_crop CÓ CONTEXT (pad 30%) thay vì
                # 112×112 squashed → ảnh đẹp hơn, FE thumbnail rõ hơn.
                if self.save_crops and quality >= _MIN_COMPOSITE:
                    save_img = self._square_resize(face_crop, 256)
                    rel = self._queue_save(
                        source_id=source_id,
                        person_id=match["id"],
                        person_name=match["name"],
                        role=match["role"],
                        confidence=round(match["score"], 4),
                        face_crop=save_img,
                        now=wall,
                        is_stranger=False,
                        is_new=False,
                        log_to_db=True,
                    )
                    if rel:
                        result["image_path"] = rel
                        self._l1_cache.put(track_id, result)
                        self._write_attr(frame_meta, track_id, result)
            else:
                # Người lạ → Stranger tracking
                # Truyền face_crop có context (cho save) — _handle_stranger sẽ
                # chọn best-of-N rồi mới queue_save.
                self._handle_stranger(
                    source_id, track_id, embedding, face_crop, quality, now, wall,
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
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

    def _detect_faces_on_persons(
        self,
        frame_bgr: np.ndarray,
        person_bboxes: list[tuple[int, int, int, int]],
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray | None]]:
        """
        Chạy SCRFD lần lượt trên CROP của mỗi person bbox + 10% margin trên/dưới.
        Trả về detections với toạ độ ĐÃ convert về frame space (không phải crop space).

        Optimization: skip person nhỏ < 80px chiều cao (chắc chắn không có mặt).
        Dedup: face crop từ 2 person bbox chồng nhau → giữ score cao hơn.
        """
        all_dets: list = []
        H, W = frame_bgr.shape[:2]
        skipped_small = 0
        for px1, py1, px2, py2 in person_bboxes:
            ph = py2 - py1
            pw = px2 - px1
            if ph < 50 or pw < 30:    # person quá nhỏ — không có mặt detect được
                skipped_small += 1
                continue
            # Margin 10% mỗi chiều để có context cho landmark
            mx = int((px2 - px1) * 0.10)
            my = int(ph * 0.05)
            cx1 = max(0, px1 - mx)
            cy1 = max(0, py1 - my)
            cx2 = min(W, px2 + mx)
            cy2 = min(H, py2 + my)
            crop = frame_bgr[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue

            try:
                local_dets = self._yolov8_face.detect(crop)
            except Exception as exc:
                logger.debug("YOLOv8-face on person crop failed: %s", exc)
                continue

            # Convert toạ độ từ crop space → frame space
            for (lx1, ly1, lx2, ly2), score, kp in local_dets:
                gx1 = lx1 + cx1
                gy1 = ly1 + cy1
                gx2 = lx2 + cx1
                gy2 = ly2 + cy1
                gkp = None
                if kp is not None:
                    gkp = kp + np.array([cx1, cy1], dtype=np.float32)
                all_dets.append(((gx1, gy1, gx2, gy2), score, gkp))

        # ── Filter: face bbox phải ≥ _FACE_MIN_PX trên cả 2 chiều ───────────
        # Face nhỏ hơn → ảnh quá vỡ để embedding ổn định → loại tránh tạo
        # stranger ảo (cùng người ra nhiều ID do embedding noise).
        before_size = len(all_dets)
        all_dets = [
            d for d in all_dets
            if (d[0][2] - d[0][0]) >= _FACE_MIN_PX
            and (d[0][3] - d[0][1]) >= _FACE_MIN_PX
        ]
        small_dropped = before_size - len(all_dets)
        if small_dropped > 0:
            sm_cnt = getattr(self, "_small_face_drop_cnt", 0) + small_dropped
            self._small_face_drop_cnt = sm_cnt
            if sm_cnt % 100 < small_dropped:
                logger.info("[FR] Dropped %d small face(s) (<%dpx) — total %d",
                            small_dropped, _FACE_MIN_PX, sm_cnt)

        # Dedup: nếu 2 detection có IoU > 0.5 → giữ cái score cao hơn.
        # Implementation đơn giản: sort theo score desc, keep nếu chưa overlap > 0.5
        all_dets.sort(key=lambda d: d[1], reverse=True)
        kept: list = []
        for det in all_dets:
            (x1, y1, x2, y2), _, _ = det
            overlap = False
            for (kx1, ky1, kx2, ky2), _, _ in kept:
                ix1 = max(x1, kx1); iy1 = max(y1, ky1)
                ix2 = min(x2, kx2); iy2 = min(y2, ky2)
                iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
                inter = iw * ih
                ua = (x2 - x1) * (y2 - y1) + (kx2 - kx1) * (ky2 - ky1) - inter
                if ua > 0 and inter / ua > 0.5:
                    overlap = True
                    break
            if not overlap:
                kept.append(det)
        return kept

    def _detect_full_frame(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray | None]]:
        """Detect khuôn mặt trên toàn frame bằng YOLOv8-face."""
        return self._yolov8_face.detect(frame_bgr)

    # ──────────────────────────────────────────────────────────────────────────
    # Căn chỉnh khuôn mặt (Face Alignment) — helper trong src/fr/face_align.py
    # CRITICAL: enrollment_service phải dùng cùng module này, nếu không sẽ
    # tạo embedding khác nhau cho cùng người (enroll vs runtime mismatch).
    # ──────────────────────────────────────────────────────────────────────────

    def _align_face(
        self,
        face_crop: np.ndarray,
        landmarks_5pt: np.ndarray | None,
    ) -> np.ndarray:
        """Wrapper — sử dụng helper module chung với EnrollmentServer."""
        return _shared_align_face(face_crop, landmarks_5pt)

    @staticmethod
    def _square_resize(img: np.ndarray, size: int) -> np.ndarray:
        """
        Pad ảnh thành square (side = max(h, w)) với mean color → resize về
        TARGET = min(size, side). CHỈ DOWNSCALE, không upscale.

        Lý do bỏ upscale:
          Face xa cam (vd bbox 80×100 px) + margin 60% = ~130×160 → upscale
          về 256×256 chỉ làm MỜ (không tạo được detail gốc không có).
          Giữ nguyên size gốc → ảnh sắc nét, FE tự responsive thumbnail.

        Output là square để FE grid hiển thị đều (không bị méo rectangle).
        """
        h, w = img.shape[:2]
        side = max(h, w)
        if side == 0:
            return np.zeros((size, size, 3), dtype=np.uint8)
        # Pad mean color để giảm viền cứng
        if img.size > 0:
            mean = img.reshape(-1, img.shape[-1]).mean(axis=0).astype(np.uint8)
        else:
            mean = np.array([114, 114, 114], dtype=np.uint8)
        canvas = np.full((side, side, img.shape[-1]), mean, dtype=np.uint8)
        ox = (side - w) // 2
        oy = (side - h) // 2
        canvas[oy:oy + h, ox:ox + w] = img

        # Chỉ downscale khi side > size. Nếu gốc nhỏ hơn → giữ nguyên (tránh
        # upscale mờ). Có thể file < 256×256 nhưng sắc hơn rõ rệt.
        if side > size:
            return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)
        return canvas

    def _expand_face_bbox(
        self,
        bbox: tuple[int, int, int, int],
        frame_w: int,
        frame_h: int,
        margin: float = 0.30,
    ) -> tuple[int, int, int, int]:
        """
        Mở rộng face bbox thêm `margin` (%) mỗi chiều — chuẩn insightface.
        Lấy thêm tóc/cằm/vai để align + quality score chính xác hơn.
        """
        x1, y1, x2, y2 = bbox
        w = x2 - x1
        h = y2 - y1
        dx = int(w * margin)
        dy = int(h * margin)
        return (
            max(0, x1 - dx),
            max(0, y1 - dy),
            min(frame_w, x2 + dx),
            min(frame_h, y2 + dy),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Anti-spoofing (MiniFASNet)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_anti_spoof(self, face_112: np.ndarray) -> bool:
        """
        Kiểm tra mặt thật vs giả mạo bằng MiniFASNet (Silent-Face-Anti-Spoofing).

        Schema model:
          - Input : [1, 3, 80, 80] BGR pixel raw (chuẩn repo gốc — không normalize ImageNet)
          - Output: [1, 3] = softmax(real, fake_2D, fake_3D)

        ⚠️ MiniFASNet pretrained chỉ hoạt động tốt trên ảnh enrollment-quality
        (mặt nhìn thẳng, đủ sáng, ≥80px). RTSP far-cam thường bị tag fake_2D
        do model nhầm với ảnh in. Để giảm false-positive:
          - Dùng REAL-vs-3D-FAKE thay vì REAL-vs-MỌI fake (chỉ block fake_3D rõ rệt)
          - Để pass-through fake_2D với ngưỡng cao (vì hầu hết là false-positive)

        Decision logic:
          - Block CHẮC CHẮN nếu fake_3D > 0.70 (mask/3D mock — model rất chính xác việc này)
          - Block nếu fake_2D > 0.85 VÀ real < 0.10 (in/màn hình rõ rệt)
          - Còn lại → pass (fail-open cho mặt thật xa cam)
        """
        try:
            inp = cv2.resize(face_112, (80, 80))   # giữ BGR uint8
            inp = inp.astype(np.float32)
            inp = inp.transpose(2, 0, 1)[np.newaxis]   # [1, 3, 80, 80]

            input_name = self._anti_spoof.get_inputs()[0].name
            output = self._anti_spoof.run(None, {input_name: inp})[0]

            # Softmax (ONNX export thường chưa apply softmax)
            raw = output[0]
            exp = np.exp(raw - raw.max())
            probs = exp / exp.sum()

            real_prob    = float(probs[0])
            fake_2d_prob = float(probs[1]) if len(probs) > 1 else 0.0
            fake_3d_prob = float(probs[2]) if len(probs) > 2 else 0.0

            # Policy: nới sau khi YOLOv8-face đã filter rất sạch non-face / mannequin
            # → không cần spoof siết quá nữa (giảm false-reject mặt thật xa cam đêm).
            # Chỉ block khi model RẤT chắc là fake — mặt nghi ngờ vẫn pass qua filter
            # downstream (motion liveness + min face size + composite quality).
            if fake_3d_prob > 0.65:
                is_real = False
                reason  = f"fake_3D={fake_3d_prob:.2f}"
            elif fake_2d_prob > 0.75 and real_prob < 0.20:
                is_real = False
                reason  = f"fake_2D={fake_2d_prob:.2f} real={real_prob:.2f}"
            else:
                is_real = True
                reason  = ""

            if not is_real:
                cnt = getattr(self, "_spoof_log_cnt", 0) + 1
                self._spoof_log_cnt = cnt
                if cnt % 200 == 0:
                    logger.warning(
                        "[FR] Spoof ×%d %s (real=%.2f f2D=%.2f f3D=%.2f)",
                        cnt, reason, real_prob, fake_2d_prob, fake_3d_prob,
                    )
            return is_real
        except Exception as exc:
            logger.debug("Anti-spoof error: %s — pass-through", exc)
            return True   # Fail-open: nếu lỗi → coi là thật, không block luồng

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
        Tầng 1: vectorized cosine similarity trên in-process matrix.

        Hiện thực: 1 matmul `(N, 512) @ (512,) → (N,)` + argmax. Cả 2
        embedding đã L2-normalized nên dot product = cosine sim.

        Perf: ~100µs cho N=100 staff (NumPy SIMD). So với version cũ HGETALL
        Redis + JSON parse N lần + Python loop → ~3ms.

        Fallback: nếu matrix chưa được prefetch (vd reload pending) → dùng
        Redis hash như cũ để không miss.
        """
        # Fast path: in-process matrix
        if self._staff_matrix is not None and len(self._staff_meta) > 0:
            try:
                sims = self._staff_matrix @ embedding   # (N,)
                best_idx = int(np.argmax(sims))
                best_score = float(sims[best_idx])
                if best_score >= self.recognition_threshold:
                    meta = self._staff_meta[best_idx]
                    return {
                        "id":    meta["id"],
                        "name":  meta["name"],
                        "role":  meta["role"],
                        "score": best_score,
                    }
                return None
            except Exception as exc:
                logger.debug("Matrix match error: %s", exc)
                # fall through to Redis fallback

        # Fallback: Redis hash (tương thích với container restart khác process)
        if self._redis is None:
            return None
        try:
            raw_map = self._redis.hgetall("svpro:staff:hash")
            if not raw_map:
                return None

            best_score  = 0.0
            best_person = None

            for key, raw in raw_map.items():
                person = json.loads(raw)
                ref_emb = np.array(person["embedding"], dtype=np.float32)
                sim = float(np.dot(embedding, ref_emb))
                if sim > best_score:
                    best_score  = sim
                    best_person = person

            if best_person and best_score >= self.recognition_threshold:
                best_person["score"] = best_score
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
        """Cache kết quả pgvector vào Redis Hash (key=uid) để lần sau HGETALL."""
        if self._redis is None or not person.get("id"):
            return
        try:
            uid     = person["id"]
            payload = json.dumps(person)
            self._redis.hset("svpro:staff:hash", uid, payload)
            logger.debug("pgvector result cached to Redis: uid=%s", uid)
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
        wall: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        """
        Theo dõi người lạ với Re-ID đa camera + throttle.

        Workflow:
          1. Tích lũy quality frame cho (source_id, track_id) → state.
          2. Khi đủ N frame chất lượng:
             a. lookup() trong registry (local + Redis) bằng centroid embedding.
             b. Nếu match → reuse stranger_id (CÙNG NGƯỜI). Cập nhật last_seen +
                cameras_seen. Ghi event chỉ khi vượt cooldown.
             c. Nếu không match → SHA256-generate ID mới, register, INSERT DB.
          3. Trong cả 2 nhánh: ghi attr lên obj_meta để BlacklistEngine downstream
             biết là stranger và sinh recognition_logs (đã được throttle ở b).
        """
        key = (source_id, track_id)

        # Dedup PER TRACK: track này đã được xử lý gần đây thì skip.
        # Throttle PER STRANGER_ID xử lý ở dưới — sau khi biết được stranger_id.
        last_saved = self._stranger_saved_ts.get(key, 0.0)
        if now - last_saved < _STRANGER_DEDUP_SECS:
            return

        state = self._strangers.get(key)
        if state is None:
            state = _StrangerState(track_id, source_id, now)
            self._strangers[key] = state

        is_new_best = state.add_frame(embedding, face_crop, quality, now, bbox=bbox)
        state.last_seen = now

        # ── Best-of-N: sau khi đã saved, nếu trong _STRANGER_REFRESH_SECS có
        # frame quality cao hơn (is_new_best) → cho phép REPLACE ảnh đã save.
        # Điều này đảm bảo "1 người = 1 ảnh đẹp nhất" thay vì 4-5 ảnh trung bình.
        if state.saved and is_new_best:
            if (now - state.saved_at) < _STRANGER_REFRESH_SECS:
                logger.info(
                    "[FR] BEST UPDATE stranger=%s qual=%.2f→%.2f, replace ảnh",
                    state.stranger_id, state.best_quality_score, quality,
                )
                if self.save_crops and state.best_face_crop is not None:
                    save_img = self._square_resize(state.best_face_crop, 256)
                    self._queue_save(
                        source_id   = source_id,
                        person_id   = state.stranger_id or "",
                        person_name = "Stranger",
                        role        = "unknown",
                        confidence  = round(state.best_quality_score, 4),
                        face_crop   = save_img,
                        now         = wall if wall is not None else time.time(),
                        is_stranger = True,
                        is_new      = False,
                        log_to_db   = False,  # BEST UPDATE không log event mới
                    )
                    state.saved_at = now
            return   # đã handle, không re-create stranger

        if state.quality_frames < _STRANGER_MIN_FRAMES or state.saved:
            return

        # ── Temporal voting: chỉ check nếu có ≥ 4 frame (đủ mẫu để có nghĩa).
        # Với MIN_FRAMES=2 và fps=8, 2 frame cách ~125ms → burst ngắn là bình thường,
        # không phải noise. Dropping temporal check khi chỉ 2 frame để người đi
        # lướt qua cam (2-3s) vẫn được commit nhanh.
        if state.quality_frames >= 4 and not state.temporal_spread_ok(min_spread_sec=0.4):
            tv_cnt = getattr(self, "_temporal_reject_cnt", 0) + 1
            self._temporal_reject_cnt = tv_cnt
            if tv_cnt % 50 == 1:
                spread = (state.positions[-1][0] - state.positions[0][0]) if state.positions else 0
                logger.info(
                    "[FR src=%s] TEMPORAL skip × %d track=%s frames=%d spread=%.2fs",
                    source_id, tv_cnt, track_id, state.quality_frames, spread,
                )
            return

        # ── Motion liveness: CHỈ check khi có ≥ 4 samples (đủ để judge static).
        # Với MIN_FRAMES=2, std 2 điểm không đủ tin cậy — người đứng nhẹ có thể
        # bị flag nhầm. Chờ 4+ samples và nới std threshold lên 15px.
        if state.is_static(min_std_px=15.0, min_samples=4):
            st_cnt = getattr(self, "_static_reject_cnt", 0) + 1
            self._static_reject_cnt = st_cnt
            if st_cnt % 20 == 1:
                logger.warning(
                    "[FR src=%s] STATIC reject × %d track=%s frames=%d "
                    "(mannequin/poster khả năng cao — bbox không di chuyển)",
                    source_id, st_cnt, track_id, state.quality_frames,
                )
            try:
                metrics.fr_recognition_total.labels(camera_id=source_id, result="static").inc()
            except Exception:
                pass
            # Giữ state nhưng xóa position history: nếu vật thật sự di chuyển
            # trong _TRACK_MAX_AGE thì sẽ tích lũy lại và vượt qua check.
            state.positions.clear()
            return

        # ── Re-ID: cố gắng map vào stranger đã biết ──────────────────────────
        # Strategy ƯU TIÊN gallery (multi-embedding pgvector) → in-memory registry → fallback
        # Multi-embedding → match nếu giống BẤT KỲ exemplar nào → recall cao hơn nhiều
        # so với 1 centroid duy nhất.
        centroid = state.mean_embedding()
        matched_id: str | None = None
        match_sim:  float = 0.0
        try:
            matched_id, match_sim = self._gallery_lookup(centroid)
        except Exception as exc:
            logger.debug("gallery_lookup failed: %s", exc)
        # Fallback: in-memory registry (cũ) nếu DB miss hoặc gallery rỗng
        if matched_id is None and stranger_registry is not None:
            try:
                matched_id = stranger_registry.lookup(centroid, source_id)
            except Exception as exc:
                logger.debug("registry lookup failed: %s", exc)

        if matched_id is None:
            # Người lạ MỚI → generate id, register
            stranger_id = state.generate_id()
            is_new      = True
            logger.warning(
                "[FR] NEW stranger=%s src=%s track=%s frames=%d qual=%.2f gallery_best_sim=%.2f",
                stranger_id, source_id, track_id,
                state.quality_frames, state.best_quality_score, match_sim,
            )
        else:
            # Re-ID match → CÙNG người. Có thể ở camera khác / sau khi đi ra rồi vào lại.
            stranger_id = matched_id
            is_new      = False
            logger.warning(
                "[FR] RE-ID stranger=%s src=%s track=%s sim=%.2f (re-appeared / cross-cam)",
                stranger_id, source_id, track_id, match_sim,
            )

        state.stranger_id = stranger_id
        state.saved       = True
        self._stranger_saved_ts[key] = now

        # Đăng ký / cập nhật registry (centroid moving average + camera list).
        if stranger_registry is not None:
            try:
                stranger_registry.register(
                    stranger_id = stranger_id,
                    embedding   = centroid,
                    camera_id   = source_id,
                    extra       = {"quality_frames": state.quality_frames},
                )
            except Exception as exc:
                logger.debug("stranger register failed: %s", exc)

        # Persist xuống guest_faces (UPSERT) — survive restart, query API.
        try:
            self._upsert_guest_face(
                stranger_id   = stranger_id,
                centroid      = centroid,
                source_id     = source_id,
                quality_frames= state.quality_frames,
                is_new        = is_new,
            )
        except Exception as exc:
            logger.debug("upsert guest_face failed: %s", exc)

        # Multi-embedding gallery: thêm centroid vào gallery (chỉ khi quality cao).
        # Mục đích: lần sau cùng người ở góc khác → match nhờ exemplar đa dạng.
        try:
            self._gallery_insert(stranger_id, centroid, state.best_quality_score, source_id)
        except Exception as exc:
            logger.debug("gallery_insert failed: %s", exc)

        # ── Throttle theo stranger_id (đa camera) ───────────────────────────
        # Nếu vừa ghi event cho stranger này → ngưng ghi tiếp trong cooldown.
        # KHÔNG bao giờ ngừng việc gắn attr xuống obj_meta (cho live overlay),
        # chỉ ngưng việc tạo recognition_logs row mới (chống spam DB).
        last_event = self._stranger_event_last.get(stranger_id, 0.0)
        within_cooldown = (now - last_event) < _STRANGER_EVENT_COOLDOWN_S
        # Lần đầu thấy (is_new) hoặc đã hết cooldown → cho phép ghi event.
        emit_event = is_new or not within_cooldown
        if emit_event:
            self._stranger_event_last[stranger_id] = now

        # L1 cache: lưu để các frame sau gắn attr nhanh + biết có nên emit nữa không
        cached_data = {
            "person_id":     stranger_id,
            "person_name":   "Stranger",
            "person_role":   "unknown",
            "fr_confidence": round(state.best_quality_score, 4),
            "is_stranger":   True,
            # _suppress_event=True → BlacklistEngine không ghi recognition_logs cho
            # frame này (xem patch trong blacklist_engine.py để đọc cờ).
            "_suppress_event": not emit_event,
        }
        self._l1_cache.put(track_id, cached_data)

        # Save crop ảnh (chỉ khi emit_event để giảm spam disk).
        # Dùng best_face_crop (đã tích lũy quality cao nhất trong N frame),
        # square_resize 256 → ảnh đẹp đều, FE thumbnail rõ.
        if self.save_crops and emit_event and state.best_face_crop is not None:
            save_img = self._square_resize(state.best_face_crop, 256)
            rel = self._queue_save(
                source_id  = source_id,
                person_id  = stranger_id,
                person_name= "Stranger",
                role       = "unknown",
                confidence = round(state.best_quality_score, 4),
                face_crop  = save_img,
                now        = wall if wall is not None else time.time(),
                is_stranger= True,
                is_new     = is_new,
                log_to_db  = True,
            )
            if rel:
                cached_data["image_path"] = rel
                self._l1_cache.put(track_id, cached_data)
                state.saved_at = now
                # Update last_image_path trong DB để FE hiển thị thumbnail mới nhất
                try:
                    self._update_guest_face_last_image(stranger_id, rel)
                except Exception:
                    pass
        elif emit_event:
            # Không có ảnh để save nhưng vẫn cần ghi recognition_log
            _ts = (wall if wall is not None else time.time())
            try:
                self._write_recognition_log({
                    "source_id":   source_id,
                    "person_id":   stranger_id,
                    "person_name": "Stranger",
                    "confidence":  round(state.best_quality_score, 4),
                    "is_stranger": True,
                    "is_new":      is_new,
                    "timestamp":   datetime.fromtimestamp(_ts, _VN_TZ).isoformat(),
                })
            except Exception:
                pass

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
    # Background Save Worker
    # ──────────────────────────────────────────────────────────────────────────

    def _queue_save(
        self,
        source_id: str,
        person_id: str,
        person_name: str,
        role: str,
        confidence: float,
        face_crop: np.ndarray,
        now: float,
        is_stranger: bool = False,
        is_new: bool = False,
        log_to_db: bool = True,
    ) -> str | None:
        """
        Đẩy task lưu ảnh và metadata vào queue.
        Trả về đường dẫn TƯƠNG ĐỐI của file JPG (so với /Detect) để caller có thể
        gắn vào DB metadata. Trả None nếu không enqueue được.
        """
        if self._save_queue is None or face_crop is None:
            return None

        try:
            dt = datetime.fromtimestamp(now, _VN_TZ)
            date_str = dt.strftime("%Y-%m-%d")
            ts = dt.strftime("%H%M%S_%f")[:10]  # HHMMSSmmm

            save_dir = os.path.join(self.save_dir, source_id, date_str, role)
            safe_name = "".join([c if c.isalnum() else "_" for c in person_name])
            prefix = f"{ts}_{person_id[:8]}_{safe_name}"
            img_filename = f"{prefix}_face.jpg"
            full_img_path = os.path.join(save_dir, img_filename)

            # Đường dẫn tương đối so với /Detect/ — ngắn gọn để serve qua HTTP
            # và để FE build URL dạng /api/detect-images/{rel_path}
            try:
                rel_path = os.path.relpath(full_img_path, "/Detect")
            except ValueError:
                rel_path = full_img_path

            event = {
                "timestamp": dt.isoformat(),
                "source_id": source_id,
                "person_id": person_id,
                "person_name": person_name,
                "role": role,
                "confidence": confidence,
                "files": {"face": img_filename},
                "image_path": rel_path,
                "is_stranger": is_stranger,
                "is_new": is_new,
                "log_to_db": log_to_db,
            }

            self._save_queue.put_nowait((save_dir, prefix, face_crop.copy(), event))
            return rel_path
        except queue.Full:
            logger.warning("Face save queue full, dropped frame processing.")
            return None
        except Exception as exc:
            logger.warning("Failed to enqueue face save: %s", exc)
            return None

    def _write_recognition_log(self, event: dict) -> None:
        """Ghi recognition_logs vào DB — gọi từ _save_worker sau khi lưu file."""
        if not self.db_dsn:
            return
        try:
            import psycopg2, json as _json
            conn = psycopg2.connect(self.db_dsn, connect_timeout=3)
            try:
                cur = conn.cursor()
                meta = {}
                if event.get("person_name"):
                    meta["person_name"] = event["person_name"]
                if event.get("image_path"):
                    meta["image_path"] = event["image_path"]
                if event.get("is_new") is not None:
                    meta["is_new"] = event["is_new"]
                cur.execute(
                    """INSERT INTO recognition_logs
                       (source_id, camera_id, label, person_id, match_score,
                        is_stranger, created_at, metadata_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        event.get("source_id"),
                        event.get("source_id"),
                        "person",
                        event.get("person_id"),
                        event.get("confidence", 0.0),
                        bool(event.get("is_stranger", False)),
                        event.get("timestamp"),
                        _json.dumps(meta) if meta else None,
                    ),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("recognition_logs write error: %s", exc)

    def _save_worker(self) -> None:
        """Luồng background lưu file ra đĩa (I/O) và ghi recognition_logs."""
        while True:
            try:
                task = self._save_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                save_dir, prefix, crop, event = task
                os.makedirs(save_dir, exist_ok=True)

                img_path = os.path.join(save_dir, f"{prefix}_face.jpg")
                json_path = os.path.join(save_dir, f"{prefix}.json")

                # JPEG quality 95 giữ chi tiết face tốt. Default cv2.imwrite
                # là 95 nhưng set explicit để không phụ thuộc version.
                cv2.imwrite(img_path, crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(event, f, ensure_ascii=False, indent=2)

                # Ghi recognition_logs nếu được yêu cầu
                if event.get("log_to_db"):
                    self._write_recognition_log(event)
            except Exception as exc:
                logger.error("Face _save_worker write error: %s", exc)
            finally:
                self._save_queue.task_done()

    # ──────────────────────────────────────────────────────────────────────────
    # Ghi kết quả vào NvDs metadata
    # ──────────────────────────────────────────────────────────────────────────

    def _write_attr(self, frame_meta: NvDsFrameMeta, track_id: int, data: dict) -> None:
        """
        Gắn kết quả nhận diện vào object metadata của DeepStream.
        BlacklistEngine + JSON egress sẽ đọc các attribute này downstream.
        Cờ `_suppress_event` (bool) → BlacklistEngine không ghi recognition_logs
        (đang trong cooldown của stranger_id này).
        """
        for obj_meta in frame_meta.objects:
            if getattr(obj_meta, "track_id", None) == track_id:
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_PERSON, data.get("person_id", ""),    1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_NAME,   data.get("person_name", ""),  1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_ROLE,   data.get("person_role", ""),  1.0)
                if data.get("image_path"):
                    obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_IMG, data["image_path"], 1.0)
                if data.get("_suppress_event"):
                    obj_meta.add_attr_meta(_ATTR_ELEMENT, "suppress_event", "1", 1.0)
                obj_meta.add_attr_meta(_ATTR_ELEMENT, _ATTR_CONF,   data.get("fr_confidence", 0), 1.0)
                break

    def _find_track_id(self, frame_meta: NvDsFrameMeta, bbox: tuple) -> int:
        """
        Tìm track_id của DeepStream person object chứa face bbox này.
        Face thường nằm trong bbox của person (YOLOv8 → nvtracker), nên ta
        ưu tiên containment thay vì center-distance.
        Fallback: hash spatial bucket nếu không match — coarse hơn raw bbox
        để cùng face qua nhiều frame vẫn ra cùng track_id.
        """
        fx1, fy1, fx2, fy2 = bbox
        fcx = (fx1 + fx2) / 2
        fcy = (fy1 + fy2) / 2

        best_id     = None
        best_area   = float("inf")  # nhỏ nhất = sát face nhất

        for obj_meta in frame_meta.objects:
            bb = obj_meta.bbox
            l, t = bb.left, bb.top
            r, b = l + bb.width, t + bb.height
            # Face center phải nằm trong bbox của person
            if l <= fcx <= r and t <= fcy <= b:
                area = bb.width * bb.height
                if area < best_area:
                    best_area = area
                    tid = getattr(obj_meta, "track_id", None)
                    if tid:
                        best_id = int(tid)

        if best_id is not None:
            return best_id

        # Fallback: bucket theo lưới 160px (tăng từ 80 → 160 sau quan sát: primary
        # YOLOv8s đôi khi miss person → fallback path được dùng liên tục, người
        # dịch chuyển 80-150px qua vài frame sẽ bị nhảy bucket → track mới → không
        # tích luỹ được MIN_FRAMES. 160px bucket = 1 người đi lướt qua vẫn cùng track.
        bucket = (int(fcx // 160), int(fcy // 160))
        return abs(hash(bucket)) % (10 ** 8)
