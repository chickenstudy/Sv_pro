# Lược đồ Cơ sở dữ liệu SV-PRO

Sử dụng **PostgreSQL 16+** với extension `pgvector` làm kho dữ liệu trung tâm. Thiết kế phục vụ hai workload song song:
- **OLTP quan hệ:** Quản lý cameras, users, vehicles, recognition logs
- **ANN Vector Search:** Tìm kiếm cosine similarity hàng triệu face embeddings với HNSW index

---

## Mục lục

1. [Extensions & Dependencies](#1-extensions--dependencies)
2. [Schema tổng quan (ERD)](#2-schema-tổng-quan-erd)
3. [Bảng `cameras`](#3-bảng-cameras)
4. [Bảng `users`](#4-bảng-users)
5. [Bảng `vehicles`](#5-bảng-vehicles)
6. [Bảng `recognition_logs` (Partitioned)](#6-bảng-recognition_logs-partitioned)
7. [Bảng `guest_faces` (Stranger Tracking)](#7-bảng-guest_faces-stranger-tracking)
8. [Bảng `access_events` (Access Control Log)](#8-bảng-access_events-access-control-log)
9. [Indexes & Performance Tuning](#9-indexes--performance-tuning)
10. [Câu lệnh Vector Search](#10-câu-lệnh-vector-search)
11. [Partitioning Strategy](#11-partitioning-strategy)
12. [Backup & Maintenance](#12-backup--maintenance)

---

## 1. Extensions & Dependencies

```sql
-- Extension bắt buộc
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector: lưu trữ và tìm kiếm vector ANN
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- Trigram index: tìm kiếm biển số gần đúng
CREATE EXTENSION IF NOT EXISTS btree_gin;      -- GIN index hỗ trợ cho JSONB metadata
CREATE EXTENSION IF NOT EXISTS pg_stat_statements; -- Giám sát performance query

-- Kiểm tra phiên bản pgvector (cần >= 0.7.0 cho HNSW)
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

---

## 2. Schema tổng quan (ERD)

```
┌─────────────┐         ┌──────────────────┐
│   cameras   │         │      users       │
│─────────────│         │──────────────────│
│ id (PK)     │         │ id (PK)          │
│ name        │    ┌───►│ name             │
│ rtsp_url    │    │    │ role             │
│ ai_mode     │    │    │ face_embedding   │◄── VECTOR(512)
│ location_   │    │    │ access_zones     │
│   zone      │    │    │ embedding_version│
│ fps_limit   │    │    │ created_at       │
│ is_active   │    │    └──────────────────┘
└──────┬──────┘    │              │
       │           │              │
       │           │    ┌─────────┴──────┐
       │           │    │    vehicles    │
       │           │    │────────────────│
       │           └────┤ owner_id (FK)  │
       │                │ plate_number   │
       │                │ category       │
       │                └────────────────┘
       │
       │    ┌──────────────────────────────────────┐
       └───►│         recognition_logs             │
            │        (PARTITIONED BY MONTH)        │
            │──────────────────────────────────────│
            │ id (PK)                              │
            │ timestamp          camera_id (FK)    │
            │ object_type        label             │
            │ confidence         vector_match_score│
            │ image_path         stranger_label    │
            │ metadata_json (JSONB)                │
            └──────────────────────────────────────┘

┌──────────────────────────────┐
│         guest_faces          │
│──────────────────────────────│
│ stranger_id (PK)             │
│ embedding   VECTOR(512)      │
│ first_seen   last_seen       │
│ camera_id    frame_count     │
│ is_registered  expires_at    │
└──────────────────────────────┘
```

---

## 3. Bảng `cameras`

```sql
CREATE TABLE cameras (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    rtsp_url       TEXT NOT NULL,
    ai_mode        VARCHAR(20) NOT NULL DEFAULT 'dual'
                   CHECK (ai_mode IN ('face', 'alpr', 'dual', 'off')),
    location_zone  VARCHAR(100),          -- Tên khu vực: "Cổng chính", "Tầng B1"
    fps_limit      SMALLINT DEFAULT 15
                   CHECK (fps_limit BETWEEN 1 AND 60),
    resolution_w   SMALLINT DEFAULT 1920,
    resolution_h   SMALLINT DEFAULT 1080,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON COLUMN cameras.fps_limit IS
    'Giới hạn FPS xử lý AI. Giảm xuống 5-10 FPS cho camera ít người để tiết kiệm GPU.';
COMMENT ON COLUMN cameras.ai_mode IS
    'face: chỉ FR | alpr: chỉ LPR | dual: cả hai | off: tắt AI';
```

---

## 4. Bảng `users`

```sql
CREATE TABLE users (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(100) NOT NULL,
    employee_code       VARCHAR(50) UNIQUE,           -- Mã nhân viên (nếu có)
    role                VARCHAR(50) NOT NULL
                        CHECK (role IN ('staff', 'resident', 'visitor', 'blacklist', 'vip')),
    face_embedding      VECTOR(512),                  -- ArcFace R100, L2-normalized
    embedding_version   VARCHAR(30) DEFAULT 'arcface-r100-v1',
    access_zones        TEXT[] DEFAULT ARRAY[]::TEXT[], -- Zones được phép: '{"Gate1","B1"}'
    phone               VARCHAR(20),
    notes               TEXT,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON COLUMN users.face_embedding IS
    'Float32 vector 512 chiều từ InsightFace ArcFace R100, L2-normalized.';
COMMENT ON COLUMN users.embedding_version IS
    'Versioning model để biết khi nào cần re-index toàn bộ embeddings.';
COMMENT ON COLUMN users.access_zones IS
    'Danh sách zone được phép vào. Trống = không giới hạn zone.';
```

---

## 5. Bảng `vehicles`

```sql
CREATE TABLE vehicles (
    id             SERIAL PRIMARY KEY,
    plate_number   VARCHAR(20) NOT NULL UNIQUE,        -- Đã chuẩn hóa: "51A-12345"
    plate_raw      VARCHAR(20),                        -- Raw string từ OCR trước normalize
    owner_id       INTEGER REFERENCES users(id)
                   ON DELETE SET NULL,
    category       VARCHAR(50)
                   CHECK (category IN ('car','motorcycle','bus','truck','other')),
    brand          VARCHAR(50),
    color          VARCHAR(30),
    is_registered  BOOLEAN DEFAULT TRUE,               -- False = xe chưa đăng ký (stranger vehicle)
    is_blacklisted BOOLEAN DEFAULT FALSE,
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Index cho tìm kiếm biển số gần đúng (OCR đôi khi đọc sai 1-2 ký tự)
CREATE INDEX idx_vehicles_plate_trgm ON vehicles
USING gin (plate_number gin_trgm_ops);
```

### Tìm kiếm biển số gần đúng (fuzzy matching)

```sql
-- Tìm biển số tương tự khi OCR có thể đọc sai 1 ký tự
SELECT plate_number, similarity(plate_number, '51A-12345') AS sim
FROM vehicles
WHERE plate_number % '51A-12345'   -- pg_trgm operator
ORDER BY sim DESC
LIMIT 3;
```

---

## 6. Bảng `recognition_logs` (Partitioned)

> ⚠️ **Bắt buộc dùng partitioning.** Với 10 camera hoạt động 24/7, bảng này có thể tích lũy **50–100 triệu rows** sau 1 năm. RANGE PARTITION theo tháng cho phép DROP partition cũ nhanh và query theo khoảng thời gian hiệu quả.

```sql
-- Bảng cha (không lưu data trực tiếp)
CREATE TABLE recognition_logs (
    id                  BIGSERIAL,
    timestamp           TIMESTAMPTZ NOT NULL,
    camera_id           INTEGER NOT NULL REFERENCES cameras(id),
    object_type         VARCHAR(20) NOT NULL
                        CHECK (object_type IN ('face', 'plate')),
    label               VARCHAR(100),       -- Tên người hoặc biển số đã chuẩn hóa
    confidence          FLOAT CHECK (confidence BETWEEN 0 AND 1),
    vector_match_score  FLOAT,              -- Cosine similarity (chỉ cho face)
    image_path          TEXT,               -- ./Detect/{cam}/{date}/{type}/filename.jpg
    stranger_label      VARCHAR(50),        -- STR_XXXXXXXX nếu là người lạ
    metadata_json       JSONB,              -- Full JSON output từ AI Core (audit log)
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Tạo partition theo tháng (tạo trước 2-3 tháng)
CREATE TABLE recognition_logs_2026_03
    PARTITION OF recognition_logs
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

CREATE TABLE recognition_logs_2026_04
    PARTITION OF recognition_logs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

-- Script tự động tạo partition tháng tiếp theo (chạy cron đầu mỗi tháng)
-- Xem: scripts/db/create_monthly_partition.py
```

### Indexes trên recognition_logs

```sql
-- Index chính: tìm log theo thời gian và camera
CREATE INDEX idx_logs_cam_time
    ON recognition_logs (camera_id, timestamp DESC);

-- Index tìm kiếm theo label (biển số hoặc tên người)
CREATE INDEX idx_logs_label
    ON recognition_logs (label)
    WHERE label IS NOT NULL;

-- GIN index cho tìm kiếm trong JSONB metadata
CREATE INDEX idx_logs_metadata
    ON recognition_logs USING gin (metadata_json);

-- Index cho stranger tracking reports
CREATE INDEX idx_logs_stranger
    ON recognition_logs (stranger_label, timestamp DESC)
    WHERE stranger_label IS NOT NULL;
```

---

## 7. Bảng `guest_faces` (Stranger Tracking)

```sql
CREATE TABLE guest_faces (
    stranger_id     VARCHAR(20) PRIMARY KEY,    -- "STR_A1B2C3D4"
    embedding       VECTOR(512) NOT NULL,
    first_seen      TIMESTAMPTZ DEFAULT NOW(),
    last_seen       TIMESTAMPTZ DEFAULT NOW(),
    first_camera_id INTEGER REFERENCES cameras(id),
    last_camera_id  INTEGER REFERENCES cameras(id),
    frame_count     INTEGER DEFAULT 1,          -- Số frame chất lượng tốt đã ghi nhận
    camera_history  INTEGER[] DEFAULT ARRAY[]::INTEGER[], -- Danh sách cameras đã xuất hiện
    is_registered   BOOLEAN DEFAULT FALSE,      -- True sau khi operator register
    registered_user_id INTEGER REFERENCES users(id),
    notes           TEXT,
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- Index cho Re-ID job (tìm stranger gần đây)
CREATE INDEX idx_guest_recent ON guest_faces (last_seen DESC)
WHERE is_registered = FALSE;

-- Index HNSW cho federated Re-ID matching
CREATE INDEX idx_guest_embed_hnsw ON guest_faces
USING hnsw (embedding vector_cosine_ops)
WITH (m = 8, ef_construction = 32);  -- Nhỏ hơn users vì table nhỏ hơn
```

---

## 8. Bảng `access_events` (Access Control Log)

```sql
CREATE TABLE access_events (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    camera_id       INTEGER REFERENCES cameras(id),
    user_id         INTEGER REFERENCES users(id),
    stranger_id     VARCHAR(20),
    access_type     VARCHAR(20) CHECK (access_type IN ('face', 'plate', 'manual')),
    decision        VARCHAR(10) CHECK (decision IN ('granted', 'denied', 'alert')),
    denial_reason   VARCHAR(100),   -- 'unknown_person' | 'blacklist' | 'wrong_zone' | 'spoof_detected'
    door_triggered  BOOLEAN DEFAULT FALSE,
    liveness_score  FLOAT,
    match_score     FLOAT,
    image_path      TEXT
) PARTITION BY RANGE (timestamp);

-- Tạo partition tương tự recognition_logs
```

---

## 9. Indexes & Performance Tuning

### HNSW Index cho Face Search (bảng `users`)

```sql
-- Tạo HNSW index (chạy 1 lần khi setup, mất 1-5 phút với > 100K faces)
CREATE INDEX idx_users_face_hnsw ON users
USING hnsw (face_embedding vector_cosine_ops)
WITH (
    m = 16,               -- Số kết nối mỗi node (tăng = accuracy↑ memory↑)
    ef_construction = 64  -- Độ rộng tìm kiếm khi build (tăng = accuracy↑ build time↑)
);

-- Tăng ef_search khi query để cân bằng accuracy/speed
-- Mặc định ef_search = 40, khuyến nghị = 100 cho production
SET hnsw.ef_search = 100;
```

### Hướng dẫn chọn HNSW vs IVFFlat

| Tiêu chí | HNSW | IVFFlat |
|----------|------|---------|
| Số vectors | < 5 triệu | > 5 triệu |
| Recall@1 | ~99% | ~95% (với nprobe=20) |
| Query latency | ~2ms | ~8ms |
| RAM usage (1M vectors, 512d) | ~4GB | ~2GB |
| Build time | Chậm hơn | Nhanh hơn |
| **Khuyến nghị** | **Production mặc định** | **Khi RAM hạn chế** |

```sql
-- IVFFlat alternative (dùng khi > 5M faces)
CREATE INDEX idx_users_face_ivfflat ON users
USING ivfflat (face_embedding vector_cosine_ops)
WITH (lists = 1000);    -- lists ≈ sqrt(num_vectors)

-- Tăng nprobe khi query (accuracy/speed trade-off)
SET ivfflat.probes = 20;
```

### PostgreSQL config tối ưu cho pgvector

Thêm vào `postgresql.conf`:

```ini
# Tăng shared buffers để cache HNSW index trong RAM
shared_buffers = 8GB              # ~25% tổng RAM

# Cho phép parallel query trên vector search
max_parallel_workers_per_gather = 4
enable_partitionwise_join = on

# Maintenance cho VACUUM tự động (quan trọng cho partitioned tables)
autovacuum_vacuum_scale_factor = 0.02
autovacuum_analyze_scale_factor = 0.01
```

---

## 10. Câu lệnh Vector Search

### Tìm khuôn mặt khớp nhất (production query)

```sql
-- Query chuẩn production với HNSW (< 2ms với index)
SELECT
    u.id,
    u.name,
    u.role,
    u.access_zones,
    1 - (u.face_embedding <=> $1::vector) AS similarity
FROM users u
WHERE
    u.is_active = TRUE
    AND u.embedding_version = 'arcface-r100-v1'
    AND 1 - (u.face_embedding <=> $1::vector) > 0.60
ORDER BY u.face_embedding <=> $1::vector
LIMIT 1;
```

### Tìm top-5 khuôn mặt giống nhất (debug / enrollment verification)

```sql
SELECT
    u.id,
    u.name,
    u.role,
    ROUND((1 - (u.face_embedding <=> $1::vector))::numeric, 4) AS similarity
FROM users u
WHERE u.is_active = TRUE
ORDER BY u.face_embedding <=> $1::vector
LIMIT 5;
```

### Kiểm tra duplicate embedding khi đăng ký user mới

```sql
-- Trước khi thêm user mới, kiểm tra trùng embedding
SELECT id, name, ROUND((1 - (face_embedding <=> $1::vector))::numeric, 4) AS similarity
FROM users
WHERE 1 - (face_embedding <=> $1::vector) > 0.80
ORDER BY face_embedding <=> $1::vector
LIMIT 1;
-- Nếu trả về kết quả → khuôn mặt đã được đăng ký trước đó
```

### Thống kê nhận diện theo ngày

```sql
SELECT
    DATE(timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh') AS date,
    object_type,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE stranger_label IS NULL) AS known,
    COUNT(*) FILTER (WHERE stranger_label IS NOT NULL) AS stranger,
    ROUND(AVG(confidence)::numeric, 3) AS avg_confidence,
    ROUND(AVG(vector_match_score)::numeric, 3) AS avg_match_score
FROM recognition_logs
WHERE timestamp > NOW() - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
```

### Top 10 người xuất hiện nhiều nhất trong tuần

```sql
SELECT
    label,
    COUNT(*) AS appearances,
    COUNT(DISTINCT camera_id) AS cameras_seen,
    MIN(timestamp) AS first_seen_today,
    MAX(timestamp) AS last_seen_today
FROM recognition_logs
WHERE
    timestamp > NOW() - INTERVAL '7 days'
    AND object_type = 'face'
    AND stranger_label IS NULL
GROUP BY label
ORDER BY appearances DESC
LIMIT 10;
```

---

## 11. Partitioning Strategy

### Script tự động tạo partition hàng tháng

```python
# scripts/db/create_monthly_partition.py
# Chạy cron đầu mỗi tháng: 0 0 1 * * python create_monthly_partition.py

import psycopg2
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

def create_next_month_partition(conn):
    now = datetime.now()
    next_month = now + relativedelta(months=1)
    partition_name = f"recognition_logs_{next_month.strftime('%Y_%m')}"
    start = next_month.replace(day=1)
    end = start + relativedelta(months=1)

    sql = f"""
    CREATE TABLE IF NOT EXISTS {partition_name}
        PARTITION OF recognition_logs
        FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}');
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f"Created partition: {partition_name}")
```

### Xóa partition cũ (data retention)

```sql
-- Xóa log cũ hơn 6 tháng (nhanh, không tốn thời gian DELETE từng row)
DROP TABLE recognition_logs_2025_09;
DROP TABLE recognition_logs_2025_10;
```

---

## 12. Backup & Maintenance

### Backup hàng ngày

```bash
#!/bin/bash
# scripts/backup_db.sh — Chạy cron 2:00 AM hàng ngày

DATE=$(date +%Y%m%d)
BACKUP_DIR="/backups/svpro"

# Backup schema + data (trừ recognition_logs cũ hơn 30 ngày)
pg_dump svpro \
    --exclude-table-data="recognition_logs_$(date -d '2 months ago' +%Y_%m)*" \
    -Fc -f "$BACKUP_DIR/svpro_$DATE.dump"

# Backup embeddings riêng (critical data)
psql svpro -c "\COPY users (id, name, face_embedding, embedding_version) TO '$BACKUP_DIR/embeddings_$DATE.csv' CSV"

# Upload lên S3/MinIO
aws s3 cp "$BACKUP_DIR/svpro_$DATE.dump" "s3://svpro-backups/db/"

# Xóa backup local cũ hơn 7 ngày
find "$BACKUP_DIR" -name "*.dump" -mtime +7 -delete
```

### VACUUM và ANALYZE định kỳ

```sql
-- Chạy sau khi insert lượng lớn face embeddings
VACUUM ANALYZE users;

-- Rebuild HNSW index nếu có nhiều UPDATE/DELETE
REINDEX INDEX CONCURRENTLY idx_users_face_hnsw;

-- Kiểm tra tình trạng index
SELECT
    indexname,
    pg_size_pretty(pg_relation_size(indexname::regclass)) AS index_size,
    idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE tablename = 'users';
```

### Re-index khi đổi embedding model

```sql
-- Khi nâng cấp sang ArcFace model mới (ví dụ: arcface-r100-v2)
-- Bước 1: Thêm cột embedding mới
ALTER TABLE users ADD COLUMN face_embedding_v2 VECTOR(512);

-- Bước 2: Script re-embed toàn bộ users (chạy background)
-- python scripts/reembed_users.py --model arcface-r100-v2

-- Bước 3: Sau khi hoàn tất, swap cột
ALTER TABLE users RENAME COLUMN face_embedding TO face_embedding_v1_backup;
ALTER TABLE users RENAME COLUMN face_embedding_v2 TO face_embedding;
UPDATE users SET embedding_version = 'arcface-r100-v2';

-- Bước 4: Rebuild HNSW index
DROP INDEX idx_users_face_hnsw;
CREATE INDEX idx_users_face_hnsw ON users
USING hnsw (face_embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
```

---

*© 2026 SV-PRO Database Design Documentation — Phiên bản 1.1*
