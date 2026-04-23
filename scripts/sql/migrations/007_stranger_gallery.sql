-- Migration 007: Multi-embedding gallery cho stranger Re-ID.
-- Mỗi stranger giữ tối đa K=5 embedding chất lượng cao thay vì 1 centroid.
-- Lý do: 1 person quan sát từ nhiều góc/ánh sáng → 1 centroid bị "trôi" →
-- match precision kém. Multi-embedding gallery: match nếu giống BẤT KỲ 1
-- trong K reference vectors → recall tăng đáng kể (industry standard).

CREATE TABLE IF NOT EXISTS stranger_embeddings (
    id           BIGSERIAL PRIMARY KEY,
    stranger_id  TEXT NOT NULL,
    embedding    vector(512) NOT NULL,
    quality      DOUBLE PRECISION DEFAULT 0.0,
    source_id    TEXT,                       -- camera detect lần này
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Index by stranger_id để evict / count nhanh
CREATE INDEX IF NOT EXISTS idx_se_stranger
    ON stranger_embeddings (stranger_id, quality DESC);

-- HNSW cho ANN search — giống guest_faces
CREATE INDEX IF NOT EXISTS idx_se_hnsw
    ON stranger_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 8, ef_construction = 32);

-- FK soft-link to guest_faces (không cascade vì FR có thể insert trước
-- khi UPSERT guest_faces; ràng buộc kiểm tra ở app layer)
-- ALTER TABLE stranger_embeddings ADD CONSTRAINT fk_se_stranger
--     FOREIGN KEY (stranger_id) REFERENCES guest_faces(stranger_id)
--     ON DELETE CASCADE;

-- Backfill: cho tất cả stranger đã có trong guest_faces, copy embedding chính
-- sang gallery để Re-ID hoạt động ngay sau migration (không phải chờ tích lũy).
INSERT INTO stranger_embeddings (stranger_id, embedding, quality, source_id, created_at)
SELECT
    g.stranger_id,
    g.face_embedding,
    1.0,                              -- quality unknown, đặt cao để priority cho seed embedding
    g.source_id,
    g.first_seen
FROM guest_faces g
WHERE g.face_embedding IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM stranger_embeddings se WHERE se.stranger_id = g.stranger_id
  );

-- Cascade DELETE: khi xóa stranger ở guest_faces → tự dọn gallery
CREATE OR REPLACE FUNCTION cleanup_stranger_embeddings()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM stranger_embeddings WHERE stranger_id = OLD.stranger_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cleanup_se ON guest_faces;
CREATE TRIGGER trg_cleanup_se
    AFTER DELETE ON guest_faces
    FOR EACH ROW
    EXECUTE FUNCTION cleanup_stranger_embeddings();

DO $$
DECLARE
    n INTEGER;
BEGIN
    SELECT COUNT(*) INTO n FROM stranger_embeddings;
    RAISE NOTICE 'stranger_embeddings ready — backfilled % rows', n;
END;
$$;
