-- Migration 004: Thêm bảng lưu ảnh snapshot từ AI events
-- Dùng cho lưu frame khi phát hiện blacklist person/vehicle hoặc stranger

CREATE TABLE IF NOT EXISTS snapshot_images (
    id              BIGSERIAL PRIMARY KEY,
    camera_id       VARCHAR(50) NOT NULL,
    event_id        VARCHAR(100),
    entity_id       VARCHAR(100),
    entity_type     VARCHAR(20),                      -- 'person' | 'vehicle' | 'stranger'
    image_path      TEXT NOT NULL,                    -- Đường dẫn file ảnh đầy đủ (local hoặc URL)
    thumbnail_path  TEXT,                             -- Đường dẫn thumbnail nhỏ
    storage_type    VARCHAR(20) DEFAULT 'local',       -- 'local' | 's3' | 'url'
    width           INT,
    height          INT,
    file_size_bytes BIGINT,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index cho truy vấn nhanh theo camera + thời gian (dùng nhiều nhất)
CREATE INDEX IF NOT EXISTS idx_snapshots_camera
    ON snapshot_images (camera_id, detected_at DESC);

-- Index cho truy vấn theo event
CREATE INDEX IF NOT EXISTS idx_snapshots_event
    ON snapshot_images (event_id)
    WHERE event_id IS NOT NULL;

-- Index cho entity lookup
CREATE INDEX IF NOT EXISTS idx_snapshots_entity
    ON snapshot_images (entity_id, detected_at DESC)
    WHERE entity_id IS NOT NULL;

-- Index cho detected_at (dùng cho cleanup/retention)
CREATE INDEX IF NOT EXISTS idx_snapshots_detected
    ON snapshot_images (detected_at DESC);