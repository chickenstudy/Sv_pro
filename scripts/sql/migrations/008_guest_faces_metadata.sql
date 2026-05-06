-- Migration 008: Hardening schema cho guest_faces.metadata_json
-- ────────────────────────────────────────────────────────────────────────────
-- Mục đích: Chuẩn hóa JSONB schema để FE/BE không cần defensive null checks
-- khắp nơi, và để AI core có một contract rõ ràng để ghi data.
--
-- CANONICAL SCHEMA (phải luôn đồng bộ giữa 3 layer):
--
--   {
--     "cameras_seen":    [str, ...]     -- list source_id đã thấy stranger này
--     "appearances":     int            -- số lần quan sát tích lũy
--     "last_image_path": str | null     -- relative path crop mới nhất (FE /api/detect-images/)
--     "first_seen_iso":  str (ISO 8601) -- redundant với column first_seen, để FE đọc 1 chỗ
--     "last_seen_iso":   str (ISO 8601) -- redundant với column last_seen
--   }
--
-- AI core ghi: src/fr/face_recognizer.py::_upsert_guest_face() + _update_guest_face_last_image()
-- BE đọc:     backend/routers/face_search.py (metadata_json->>'last_image_path', cameras_seen)
-- FE consume: dashboard/src/pages/StrangersPage.tsx, FaceSearchPage
-- ────────────────────────────────────────────────────────────────────────────

-- 1) Đảm bảo column KHÔNG BAO GIỜ NULL — default empty object
ALTER TABLE guest_faces
    ALTER COLUMN metadata_json SET DEFAULT '{}'::jsonb;

-- 2) Backfill các row cũ đang NULL
UPDATE guest_faces
   SET metadata_json = '{}'::jsonb
 WHERE metadata_json IS NULL;

-- 3) Enforce NOT NULL sau khi backfill
ALTER TABLE guest_faces
    ALTER COLUMN metadata_json SET NOT NULL;

-- 4) Đảm bảo các key canonical luôn tồn tại (migrate từ shape cũ sang).
--    Không xóa key lạ — chỉ ADD key thiếu với default values.
UPDATE guest_faces
   SET metadata_json = jsonb_build_object(
           'cameras_seen',    COALESCE(metadata_json->'cameras_seen',    '[]'::jsonb),
           'appearances',     COALESCE(metadata_json->'appearances',     '1'::jsonb),
           'last_image_path', COALESCE(metadata_json->'last_image_path', 'null'::jsonb),
           'first_seen_iso',  COALESCE(metadata_json->'first_seen_iso',  to_jsonb(first_seen)),
           'last_seen_iso',   COALESCE(metadata_json->'last_seen_iso',   to_jsonb(last_seen))
       )
       || metadata_json    -- preserve bất kỳ key thêm (không ghi đè canonical vì dùng build_object trước)
 WHERE NOT (
       metadata_json ? 'cameras_seen'
   AND metadata_json ? 'appearances'
   AND metadata_json ? 'last_image_path'
 );

-- 5) Nếu muốn strict CHECK constraint (ngăn ghi sai schema từ mọi caller),
--    uncomment block dưới. Hiện để soft vì AI core đã tuân thủ và ta không
--    muốn block legacy ingest code nếu có.
--
-- ALTER TABLE guest_faces
--     ADD CONSTRAINT guest_faces_metadata_shape CHECK (
--         jsonb_typeof(metadata_json) = 'object'
--     AND metadata_json ? 'cameras_seen'
--     AND metadata_json ? 'appearances'
--     AND jsonb_typeof(metadata_json->'cameras_seen') = 'array'
--     AND jsonb_typeof(metadata_json->'appearances')  = 'number'
--     );

-- 6) Trigger tự sync last_seen_iso mỗi khi last_seen update — tránh drift.
CREATE OR REPLACE FUNCTION sync_guest_faces_metadata()
RETURNS TRIGGER AS $$
BEGIN
    NEW.metadata_json = jsonb_set(
        COALESCE(NEW.metadata_json, '{}'::jsonb),
        '{last_seen_iso}',
        to_jsonb(NEW.last_seen)
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_guest_faces_sync_metadata ON guest_faces;
CREATE TRIGGER trg_guest_faces_sync_metadata
    BEFORE UPDATE OF last_seen ON guest_faces
    FOR EACH ROW
    EXECUTE FUNCTION sync_guest_faces_metadata();

DO $$
DECLARE
    total  INTEGER;
    valid  INTEGER;
BEGIN
    SELECT COUNT(*) INTO total FROM guest_faces;
    SELECT COUNT(*) INTO valid FROM guest_faces
     WHERE metadata_json ? 'cameras_seen'
       AND metadata_json ? 'appearances';
    RAISE NOTICE 'guest_faces metadata normalized — % / % rows have canonical shape', valid, total;
END;
$$;
