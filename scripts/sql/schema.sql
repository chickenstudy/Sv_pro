-- Khởi tạo pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Bảng người dùng nhân viên/đăng ký
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    person_id       VARCHAR(50) UNIQUE NOT NULL,
    name            VARCHAR(100) NOT NULL,
    role            VARCHAR(20) DEFAULT 'staff',     -- staff | blacklist | guest | admin
    active          BOOLEAN DEFAULT TRUE,
    face_embedding  vector(512),
    embedding_version INT DEFAULT 1,
    blacklist_reason  TEXT,
    access_zones    TEXT[],                          -- Danh sách zone được phép: ['office', 'warehouse']
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- HNSW Vector Index cho Face matching (cosine similarity)
CREATE INDEX IF NOT EXISTS idx_face_hnsw
    ON users USING hnsw (face_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index theo role để blacklist lookup nhanh
CREATE INDEX IF NOT EXISTS idx_users_role   ON users (role) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_active ON users (active);

-- ── Bảng phương tiện ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vehicles (
    id               SERIAL PRIMARY KEY,
    plate_number     VARCHAR(20) UNIQUE NOT NULL,
    plate_category   VARCHAR(30),
    owner_id         INT REFERENCES users(id) ON DELETE SET NULL,
    is_blacklisted   BOOLEAN DEFAULT FALSE,
    blacklist_reason TEXT,
    registered_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vehicles_plate      ON vehicles (plate_number);
CREATE INDEX IF NOT EXISTS idx_vehicles_blacklist  ON vehicles (is_blacklisted) WHERE is_blacklisted = TRUE;

-- ── Bảng log nhận diện (Vehicle / Person) ──────────────────────────────────────
-- Phân vùng theo tháng để hiệu năng truy vấn tốt hơn với dữ liệu lớn
CREATE TABLE IF NOT EXISTS recognition_logs (
    event_id       UUID DEFAULT gen_random_uuid(),
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    source_id      VARCHAR(50) NOT NULL,
    camera_id      VARCHAR(50),
    label          VARCHAR(20) NOT NULL,
    person_id      VARCHAR(50),
    match_score    FLOAT,
    is_stranger    BOOLEAN DEFAULT FALSE,
    plate_number   VARCHAR(20),
    plate_category VARCHAR(30),
    ocr_confidence FLOAT,
    metadata_json  JSONB,
    PRIMARY KEY (event_id, created_at)
) PARTITION BY RANGE (created_at);

-- Unique index trên event_id (để query nhanh theo event_id)
CREATE UNIQUE INDEX IF NOT EXISTS idx_reclogs_event_id ON recognition_logs (event_id);

-- Tạo partition tháng hiện tại và 2 tháng tiếp theo
DO $$
DECLARE
    m DATE;
BEGIN
    FOR i IN 0..2 LOOP
        m := DATE_TRUNC('month', CURRENT_DATE + (i || ' months')::INTERVAL);
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS recognition_logs_%s PARTITION OF recognition_logs FOR VALUES FROM (%L) TO (%L)',
            TO_CHAR(m, 'YYYY_MM'),
            m,
            m + INTERVAL '1 month'
        );
    END LOOP;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_reclogs_source   ON recognition_logs (source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reclogs_plate    ON recognition_logs (plate_number) WHERE plate_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reclogs_person   ON recognition_logs (person_id)   WHERE person_id IS NOT NULL;

-- ── Bảng sự kiện truy cập & cảnh báo (Sprint 4) ───────────────────────────────
CREATE TABLE IF NOT EXISTS access_events (
    id               BIGSERIAL PRIMARY KEY,
    event_type       VARCHAR(50)  NOT NULL,   -- blacklist_person | blacklist_vehicle | zone_denied | spoof_detected | object_linked
    entity_type      VARCHAR(20),             -- person | vehicle
    entity_id        VARCHAR(100),
    severity         VARCHAR(20)  NOT NULL DEFAULT 'MEDIUM',  -- LOW | MEDIUM | HIGH | CRITICAL
    camera_id        VARCHAR(50),
    source_id        VARCHAR(50),
    reason           TEXT,
    event_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    json_path        TEXT,                    -- Đường dẫn file JSON đầy đủ trên disk
    alert_sent       BOOLEAN DEFAULT FALSE,   -- Đã gửi Telegram/Webhook chưa
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_events_time     ON access_events (event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_access_events_entity   ON access_events (entity_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_access_events_severity ON access_events (severity, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_access_events_camera   ON access_events (camera_id, event_timestamp DESC);

-- ── Bảng theo dõi người lạ (guest_faces) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS guest_faces (
    stranger_id    VARCHAR(50) PRIMARY KEY,
    source_id      VARCHAR(50),
    first_seen     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    quality_frames INT DEFAULT 0,
    face_embedding vector(512),
    metadata_json  JSONB
);

CREATE INDEX IF NOT EXISTS idx_guest_faces_last_seen ON guest_faces (last_seen DESC);

-- HNSW Index cho Stranger Re-ID (tìm stranger tương tự ở camera khác)
CREATE INDEX IF NOT EXISTS idx_guest_hnsw
    ON guest_faces USING hnsw (face_embedding vector_cosine_ops)
    WITH (m = 8, ef_construction = 32);

-- ── Hàm dọn dẹp retention ──────────────────────────────────────────────────────
-- Gọi thủ công hoặc qua cron job hàng tuần
-- Xóa: access_events MEDIUM/LOW cũ hơn 90 ngày, HIGH/CRITICAL cũ hơn 365 ngày
CREATE OR REPLACE FUNCTION cleanup_old_events() RETURNS void AS $$
BEGIN
    -- Xóa event mức thấp cũ hơn 90 ngày
    DELETE FROM access_events
    WHERE severity IN ('LOW', 'MEDIUM')
      AND event_timestamp < NOW() - INTERVAL '90 days';

    -- Xóa event mức cao cũ hơn 1 năm
    DELETE FROM access_events
    WHERE severity IN ('HIGH', 'CRITICAL')
      AND event_timestamp < NOW() - INTERVAL '365 days';

    -- Xóa guest_faces không thấy trong 30 ngày
    DELETE FROM guest_faces
    WHERE last_seen < NOW() - INTERVAL '30 days';

    RAISE NOTICE 'Cleanup complete at %', NOW();
END;
$$ LANGUAGE plpgsql;

