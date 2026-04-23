-- Migration 006: Camera ROI polygon
-- Lưu vùng quan tâm cho từng camera dưới dạng polygon (n điểm, toạ độ chuẩn hoá [0,1]).
-- AI Core đọc qua /api/cameras (đã có) + LISTEN cameras_changed → áp dụng cho FR/LPR.

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS roi_polygon JSONB;

-- roi_polygon format:
--   null      → không giới hạn (default, dùng full frame)
--   [{x:0.1,y:0.2}, {x:0.9,y:0.2}, {x:0.9,y:0.8}, {x:0.1,y:0.8}]  → polygon (≥3 điểm)
--
-- Toạ độ normalized (0-1) → resolution-independent, áp được cho mọi camera.

COMMENT ON COLUMN cameras.roi_polygon IS
    'Polygon vùng quan tâm: list of {x,y} normalized [0,1]. NULL = full frame.';

DO $$
BEGIN
    RAISE NOTICE 'cameras.roi_polygon column ready';
END;
$$;
