"""
Sprint 4 — Kế hoạch Triển khai Logic Nghiệp vụ & Cảnh báo (Smart Rules)

Mục tiêu:
  Xây dựng lớp nghiệp vụ bên trên kết quả nhận diện từ LPR & FR:
    1. Blacklist/Whitelist Engine  — phân loại đối tượng nguy hiểm/được phép.
    2. Alert Manager               — gửi cảnh báo Telegram + Webhook khi kích hoạt rule.
    3. Object Linker               — ghép biển số xe với khuôn mặt người đi cùng.
    4. Audit Log                   — lưu mọi sự kiện cấp ALERT với ảnh crop + JSON.
    5. Access Control              — gửi tín hiệu mở cửa (HTTP relay) sau khi FR pass.
"""

# ── Danh sách task ──────────────────────────────────────────────────────────────

TASKS = {
    "4.1": {
        "title": "Object Linking — Liên kết biển số xe với khuôn mặt",
        "status": "in_progress",
        "file": "src/business/object_linker.py",
        "notes": [
            "Spatial proximity: xe và người cách nhau < 150px trong cùng frame",
            "Temporal window: 2 giây",
            "Lưu liên kết vào recognition_logs.metadata_json",
        ],
    },
    "4.2": {
        "title": "Blacklist/Whitelist Engine",
        "status": "in_progress",
        "file": "src/business/blacklist_engine.py",
        "notes": [
            "Kiểm tra users.role = 'blacklist' sau mỗi face match",
            "Kiểm tra vehicles.is_blacklisted sau mỗi LPR match",
            "Rule builder: per-camera, per-zone, per-time-range",
        ],
    },
    "4.3": {
        "title": "Alert System — Webhook + Telegram Bot",
        "status": "in_progress",
        "file": "src/business/alert_manager.py",
        "notes": [
            "Trigger khi: blacklist / stranger restricted zone / spoof attempt",
            "Rate limit: 1 alert/entity/5 phút",
            "Template trong config/alerts.yml",
        ],
    },
    "4.4": {
        "title": "Access Control Integration",
        "status": "todo",
        "file": "src/business/access_control.py",
        "notes": [
            "POST /api/door/{door_id}/trigger để mở cửa",
            "Trigger khi: face match + liveness pass + zone allowed",
            "Log vào bảng access_events",
        ],
    },
    "4.5": {
        "title": "Audit Log",
        "status": "in_progress",
        "file": "src/business/audit_logger.py",
        "notes": [
            "Full JSON + ảnh crop cho mọi event ALERT",
            "Retention: 90 ngày normal, 1 năm alert",
        ],
    },
}
