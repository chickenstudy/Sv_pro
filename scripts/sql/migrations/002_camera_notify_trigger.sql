-- Migration 002: PostgreSQL LISTEN/NOTIFY Trigger cho cameras table
-- Mục đích: go2rtc_sync.py nhận NOTIFY ngay khi camera thay đổi trong DB
--           thay vì phải poll mỗi 10 giây.
-- Áp dụng: go2rtc LISTEN channel 'cameras_changed'

-- ── Function: gửi NOTIFY khi camera INSERT/UPDATE/DELETE ─────────────────────
CREATE OR REPLACE FUNCTION notify_camera_change()
RETURNS trigger AS $$
BEGIN
  -- Gửi payload JSON với thông tin camera để sync service biết cụ thể
  IF TG_OP = 'DELETE' THEN
    PERFORM pg_notify(
      'cameras_changed',
      json_build_object(
        'op',         'DELETE',
        'id',         OLD.id,
        'source_id',  COALESCE(OLD.name, 'cam_' || OLD.id::text),
        'enabled',    OLD.enabled
      )::text
    );
    RETURN OLD;
  ELSE
    PERFORM pg_notify(
      'cameras_changed',
      json_build_object(
        'op',         TG_OP,
        'id',         NEW.id,
        'source_id',  COALESCE(NEW.name, 'cam_' || NEW.id::text),
        'rtsp_url',   NEW.rtsp_url,
        'enabled',    NEW.enabled
      )::text
    );
    RETURN NEW;
  END IF;
END;
$$ LANGUAGE plpgsql;

-- ── Trigger: kích hoạt sau mỗi INSERT/UPDATE/DELETE trên bảng cameras ────────
DROP TRIGGER IF EXISTS camera_change_notify ON cameras;

CREATE TRIGGER camera_change_notify
  AFTER INSERT OR UPDATE OR DELETE
  ON cameras
  FOR EACH ROW
  EXECUTE FUNCTION notify_camera_change();

-- ── Index bổ sung để go2rtc_sync.py query nhanh hơn ──────────────────────────
CREATE INDEX IF NOT EXISTS idx_cameras_enabled_id
  ON cameras (enabled, id)
  WHERE enabled = true;

-- Verify
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.triggers
    WHERE trigger_name = 'camera_change_notify'
      AND event_object_table = 'cameras'
  ) THEN
    RAISE NOTICE '✅ camera_change_notify trigger created successfully.';
  ELSE
    RAISE WARNING '❌ Trigger creation may have failed.';
  END IF;
END;
$$;
