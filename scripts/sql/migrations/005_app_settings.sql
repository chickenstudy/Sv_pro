-- Migration 005: app_settings (key-value store) + retention defaults
-- Mục đích: lưu cấu hình runtime (TTL ảnh detect, log, audit, guest_faces).
-- Backend đọc qua /api/settings, FE update qua PUT /api/settings/{key}.

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_by  TEXT
);

-- Default retention values (số ngày). Có thể chỉnh qua FE Settings page.
INSERT INTO app_settings (key, value, updated_by) VALUES
    ('retention.detect_days',       '30'::jsonb,  'system'),
    ('retention.audit_days',        '90'::jsonb,  'system'),
    ('retention.events_days',      '180'::jsonb,  'system'),
    ('retention.guest_faces_days',   '7'::jsonb,  'system')
ON CONFLICT (key) DO NOTHING;

-- Lịch sử các lần cleanup chạy
CREATE TABLE IF NOT EXISTS retention_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at  TIMESTAMPTZ,
    triggered_by TEXT NOT NULL DEFAULT 'cron',  -- 'cron' or 'manual:<username>'
    deleted_files INTEGER DEFAULT 0,
    deleted_bytes BIGINT  DEFAULT 0,
    deleted_rows  JSONB   DEFAULT '{}'::jsonb,  -- {"access_events":N, "recognition_logs":M, ...}
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_retention_runs_started ON retention_runs (started_at DESC);

DO $$
BEGIN
    RAISE NOTICE 'app_settings + retention_runs ready';
END;
$$;
