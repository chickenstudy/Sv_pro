-- Migration 003: Hỗ trợ Enrollment API
-- Thêm cột embedding_version vào bảng users nếu chưa có.
-- Cột này cho phép theo dõi phiên bản embedding và invalidate cache đúng cách.

-- ── 1. Thêm embedding_version vào users (nếu chưa có) ───────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS embedding_version INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN users.embedding_version IS
    'Tăng lên 1 mỗi lần đăng ký lại khuôn mặt — dùng để phát hiện cache stale.';

-- ── 2. Index hỗ trợ tìm user có embedding để invalidate cache hàng loạt ──────
CREATE INDEX IF NOT EXISTS idx_users_has_embedding
    ON users (id)
    WHERE face_embedding IS NOT NULL;

-- ── 3. Index blacklist để prefetch nhanh hơn ──────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_blacklist_active
    ON users (role, id)
    WHERE role = 'blacklist' AND active = TRUE;

CREATE INDEX IF NOT EXISTS idx_users_staff_active
    ON users (role, id)
    WHERE role = 'staff' AND active = TRUE;
