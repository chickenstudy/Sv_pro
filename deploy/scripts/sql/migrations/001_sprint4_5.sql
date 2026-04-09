-- Sprint 4 & 5: Thêm bảng cameras và hoàn thiện schema còn thiếu
-- Chạy sau khi schema.sql chính đã được áp dụng.

-- ── Bảng cameras ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cameras (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    rtsp_url    VARCHAR(500) NOT NULL,
    location    VARCHAR(200),
    zone        VARCHAR(100),
    ai_mode     VARCHAR(20) DEFAULT 'both',  -- lpr | fr | both | off
    fps_limit   SMALLINT DEFAULT 10,
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cameras_enabled ON cameras (enabled);
CREATE INDEX IF NOT EXISTS idx_cameras_zone    ON cameras (zone) WHERE zone IS NOT NULL;

-- ── Trigger tự cập nhật updated_at ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_cameras_updated_at') THEN
        CREATE TRIGGER set_cameras_updated_at
        BEFORE UPDATE ON cameras
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_users_updated_at') THEN
        CREATE TRIGGER set_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_vehicles_updated_at') THEN
        CREATE TRIGGER set_vehicles_updated_at
        BEFORE UPDATE ON vehicles
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
    END IF;
END;
$$;

-- ── View tiện ích: Recognition events gần nhất ────────────────────────────────
CREATE OR REPLACE VIEW v_recent_events AS
SELECT
    ae.id,
    ae.event_type,
    ae.entity_id,
    ae.severity,
    ae.camera_id,
    c.name     AS camera_name,
    c.location AS camera_location,
    ae.reason,
    ae.event_timestamp,
    ae.alert_sent
FROM access_events ae
LEFT JOIN cameras c ON ae.camera_id = c.name
ORDER BY ae.event_timestamp DESC;

-- ── View: Blacklist summary ────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_blacklist_summary AS
SELECT 'person' AS type, person_id AS id, name, blacklist_reason AS reason, updated_at
FROM users WHERE role = 'blacklist' AND active = TRUE
UNION ALL
SELECT 'vehicle', plate_number, plate_number, blacklist_reason, updated_at
FROM vehicles WHERE is_blacklisted = TRUE
ORDER BY updated_at DESC;

-- ── Hàm tạo partition tháng tiếp theo (gọi hàng tháng) ───────────────────────
CREATE OR REPLACE FUNCTION create_next_month_partition() RETURNS void AS $$
DECLARE
    next_m DATE;
BEGIN
    next_m := DATE_TRUNC('month', CURRENT_DATE + INTERVAL '1 month');
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS recognition_logs_%s PARTITION OF recognition_logs FOR VALUES FROM (%L) TO (%L)',
        TO_CHAR(next_m, 'YYYY_MM'),
        next_m,
        next_m + INTERVAL '1 month'
    );
    RAISE NOTICE 'Partition % created', TO_CHAR(next_m, 'YYYY_MM');
END;
$$ LANGUAGE plpgsql;
