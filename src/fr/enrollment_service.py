"""
Enrollment HTTP Service — SV-PRO Face Enrollment.

Chạy trong một thread riêng bên trong savant-ai-core container.
Tái dụng ONNX sessions (SCRFD + ArcFace) đã được load và warmup sẵn
bởi FaceRecognizer.on_start() — không load lại model.

Cách hoạt động:
  1. FaceRecognizer khởi động → load SCRFD + ArcFace vào GPU/CPU.
  2. FaceRecognizer.on_start() gọi start_enrollment_server(scrfd, arcface).
  3. EnrollmentServer spin up FastAPI app trên port 8090 (internal).
  4. Backend routers/enroll.py POST ảnh → nhận embedding 512-dim.
  5. Backend lưu embedding vào DB và invalidate Redis cache.

Lợi ích: Cùng model, cùng preprocessing, cùng GPU/CPU provider
  → embedding enroll ≡ embedding realtime → match chính xác 100%.

Port: 8090 (internal only, không expose ra host trong docker-compose).
"""

import io
import json
import logging
import threading
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("enrollment_service")

# ── Trạng thái server ─────────────────────────────────────────────────────────
_server_thread: Optional[threading.Thread] = None
_is_running = False


class EnrollmentServer:
    """
    HTTP server nhỏ dùng Flask (sync, phù hợp cho enrollment không cần realtime).
    Tái sử dụng ONNX sessions từ FaceRecognizer — không load lại model.
    """

    def __init__(
        self,
        scrfd_session,    # ort.InferenceSession đã load
        arcface_session,  # ort.InferenceSession đã load
        host: str = "0.0.0.0",
        port: int = 8090,
    ):
        # Nhận ONNX sessions đã warmup từ FaceRecognizer
        self._scrfd    = scrfd_session
        self._arcface  = arcface_session
        self._host     = host
        self._port     = port

    # ── Face detection (tái dụng logic từ FaceRecognizer) ────────────────────

    def _detect_best_face(self, image_bgr: np.ndarray) -> Optional[np.ndarray]:
        """
        Phát hiện khuôn mặt bằng SCRFD session đã load, trả về ảnh 112×112.
        Lấy khuôn mặt có confidence score cao nhất nếu có nhiều.
        Trả về None nếu không phát hiện được khuôn mặt.
        """
        h, w = image_bgr.shape[:2]
        model_sz = 640
        scale = min(model_sz / h, model_sz / w)
        new_h, new_w = int(h * scale), int(w * scale)
        resized = cv2.resize(image_bgr, (new_w, new_h))

        canvas = np.zeros((model_sz, model_sz, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = resized

        inp = canvas[:, :, ::-1].astype(np.float32)    # BGR→RGB
        inp = inp.transpose(2, 0, 1)[np.newaxis]        # [1,3,640,640]

        input_name = self._scrfd.get_inputs()[0].name
        outputs    = self._scrfd.run(None, {input_name: inp})

        # Parse output: tìm bbox có score cao nhất
        best_box, best_score = None, 0.0
        CONF = 0.50

        if len(outputs) >= 2:
            num_strides = len(outputs) // 2
            for i in range(num_strides):
                sc_raw = outputs[i].squeeze()
                bx_raw = outputs[i + num_strides]
                if sc_raw.ndim == 2:
                    sc_raw = sc_raw[:, 0]
                mask = sc_raw > CONF
                if not mask.any():
                    continue
                sc  = sc_raw[mask]
                bx  = (bx_raw[mask] / scale)
                idx = int(np.argmax(sc))
                if float(sc[idx]) > best_score:
                    best_score = float(sc[idx])
                    best_box   = bx[idx].tolist()

        if best_box is None:
            return None

        x1 = int(np.clip(best_box[0], 0, w))
        y1 = int(np.clip(best_box[1], 0, h))
        x2 = int(np.clip(best_box[2], 0, w))
        y2 = int(np.clip(best_box[3], 0, h))

        face_crop = image_bgr[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None

        return cv2.resize(face_crop, (112, 112), interpolation=cv2.INTER_LINEAR)

    def _extract_embedding(self, face_112: np.ndarray) -> np.ndarray:
        """
        Trích xuất embedding 512-dim từ ảnh 112×112 bằng ArcFace session đã load.
        Cùng preprocessing và normalization với FaceRecognizer.process_frame().
        """
        inp = face_112[:, :, ::-1].astype(np.float32)   # BGR→RGB
        inp = (inp - 127.5) / 128.0                       # Chuẩn hóa ArcFace
        inp = inp.transpose(2, 0, 1)[np.newaxis]          # [1,3,112,112]

        input_name = self._arcface.get_inputs()[0].name
        emb = self._arcface.run(None, {input_name: inp})[0][0]  # [512]

        # Chuẩn hóa L2 — bắt buộc để cosine similarity trong pgvector đúng
        norm = np.linalg.norm(emb)
        return emb / (norm + 1e-6)

    # ── Flask app ──────────────────────────────────────────────────────────────

    def _build_flask_app(self):
        """
        Tạo Flask app với 2 endpoint:
          POST /internal/enroll  — Nhận ảnh → trả embedding
          GET  /internal/health  — Health check
        """
        try:
            from flask import Flask, Response, request
        except ImportError:
            raise RuntimeError(
                "Flask không được cài đặt trong savant-ai-core image. "
                "Thêm 'flask' vào Dockerfile.savant-ai-core."
            )

        app = Flask("enrollment_service")

        # Tắt Flask default logger để không ồn ào trong logs Savant
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.WARNING)

        @app.route("/internal/health", methods=["GET"])
        def health():
            """Kiểm tra enrollment service còn sống không."""
            return Response(
                json.dumps({"status": "ok", "service": "enrollment"}),
                mimetype="application/json",
            )

        @app.route("/internal/enroll", methods=["POST"])
        def enroll():
            """
            Nhận ảnh từ backend → detect khuôn mặt → extract embedding.
            Trả về JSON: {embedding: [512 floats], face_detected: bool}.
            Content-Type: multipart/form-data, field name: 'image'.
            """
            if "image" not in request.files:
                return Response(
                    json.dumps({"error": "Thiếu field 'image' trong form-data"}),
                    status=400, mimetype="application/json",
                )

            file_bytes = request.files["image"].read()
            if len(file_bytes) > 15 * 1024 * 1024:   # Giới hạn 15MB
                return Response(
                    json.dumps({"error": "Ảnh quá lớn (tối đa 15MB)"}),
                    status=413, mimetype="application/json",
                )

            # Decode ảnh → numpy BGR
            arr = np.frombuffer(file_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return Response(
                    json.dumps({"error": "Không decode được ảnh. Dùng JPEG hoặc PNG."}),
                    status=400, mimetype="application/json",
                )

            # Detect face → extract embedding
            try:
                face_112 = self._detect_best_face(img)
                if face_112 is None:
                    return Response(
                        json.dumps({
                            "face_detected": False,
                            "error": "Không phát hiện khuôn mặt. "
                                     "Đảm bảo ảnh có 1 khuôn mặt rõ ràng, nhìn thẳng.",
                        }),
                        status=422, mimetype="application/json",
                    )

                embedding = self._extract_embedding(face_112)
                logger.debug("Enrollment embedding extracted, norm=%.4f", float(np.linalg.norm(embedding)))

                return Response(
                    json.dumps({
                        "face_detected": True,
                        "embedding":     embedding.tolist(),   # list[float] 512 phần tử
                        "dim":           len(embedding),
                    }),
                    status=200, mimetype="application/json",
                )
            except Exception as exc:
                logger.error("Enrollment inference error: %s", exc, exc_info=True)
                return Response(
                    json.dumps({"error": f"Lỗi inference: {exc}"}),
                    status=500, mimetype="application/json",
                )

        return app

    def run(self) -> None:
        """
        Khởi động Flask server (blocking).
        Gọi trong thread riêng từ FaceRecognizer.on_start().
        """
        app = self._build_flask_app()
        logger.info(
            "Enrollment HTTP service starting on %s:%d  →  POST /internal/enroll",
            self._host, self._port,
        )
        # use_reloader=False bắt buộc khi chạy trong thread (không phải main thread)
        app.run(host=self._host, port=self._port, use_reloader=False, threaded=True)


def start_enrollment_server(
    scrfd_session,
    arcface_session,
    host: str = "0.0.0.0",
    port: int = 8090,
) -> None:
    """
    Khởi động EnrollmentServer trong daemon thread.
    Gọi 1 lần trong FaceRecognizer.on_start() sau khi models đã sẵn sàng.
    Daemon thread tự tắt khi Savant process kết thúc.
    """
    global _server_thread, _is_running
    if _is_running:
        logger.info("Enrollment server đang chạy, bỏ qua.")
        return

    server = EnrollmentServer(scrfd_session, arcface_session, host, port)

    _server_thread = threading.Thread(
        target=server.run,
        name="enrollment-http",
        daemon=True,   # Tự tắt khi Savant process exit
    )
    _server_thread.start()
    _is_running = True
    logger.info("Enrollment server thread started (daemon=True, port=%d).", port)
