# SV-PRO 项目知识库 (GitNexus Context)

> 本文档为 AI Agent 提供项目的深度上下文，以减少每次会话的 Token 消耗。
> 所有技术解释均使用中文。

---

## 项目概述

**SV-PRO** 是一个智能视觉监控系统，核心功能：

| 功能模块 | 技术方案 | 输入 | 输出 |
|---------|---------|------|------|
| **LPR** 车牌识别 | YOLOv8n (检测) + PaddleOCR (识别) | 车辆视频流 | 越南车牌文本 |
| **FR** 人脸识别 | SCRFD-10GF (检测) + ArcFace R100 (比对) | 人脸视频流 | 人员 ID + 相似度 |
| **Anti-Spoofing** | MiniFASNet ONNX | 人脸帧 | 活体/欺骗分数 |
| **Access Control** | PostgreSQL 规则引擎 | 识别结果 | 门禁放行/拒绝 |
| **Blacklist Engine** | 3级缓存 + pgvector ANN | 人脸/车牌 | 告警事件 |
| **Alert System** | Telegram Bot + Webhook | 黑名单事件 | 告警消息 |

**技术栈：**
- 视频管道：Savant (GStreamer-based) + DeepStream
- AI 推理：ONNX Runtime (CUDA GPU)
- 后端 API：FastAPI (异步)
- 数据库：PostgreSQL 16 + pgvector (向量搜索)
- 缓存：Redis 7
- 前端：React 18 + Vite + TypeScript
- 监控：Prometheus + Grafana
- 容器：Docker Compose + NVIDIA GPU

---

## 目录结构

```
backend/               # FastAPI REST API 服务
  ├── database.py      # asyncpg 连接池管理
  ├── main.py          # 入口，注册9个路由器
  └── routers/
       ├── auth.py     # JWT + API Key 认证
       ├── cameras.py  # 摄像头 CRUD
       ├── users.py    # 用户管理 + 人脸注册
       ├── vehicles.py # 车辆管理 + 黑名单
       ├── events.py   # 通行事件 + AI Core 数据接入
       ├── doors.py    # 门禁控制 + 继电器触发
       ├── strangers.py # 陌生人追踪
       ├── metrics.py  # Prometheus 指标查询
       └── health.py   # 健康检查

src/                   # Savant pyfunc 插件（核心AI模块）
  ├── fr/
  │    ├── face_recognizer.py  # 人脸识别主流程
  │    ├── face_quality.py     # 质量评估
  │    └── stranger_reid.py     # 陌生人 ReID
  │
  ├── lpr/
  │    └── plate_ocr.py         # 车牌识别主流程（1098行）
  │
  ├── business/
  │    ├── blacklist_engine.py  # 黑名单/白名单引擎
  │    ├── alert_manager.py     # 告警分发（Telegram+Webhook）
  │    ├── access_control.py    # 区域门禁规则
  │    ├── object_linker.py     # FR+LPR 结果关联
  │    └── audit_logger.py      # 审计日志
  │
  ├── ingress/
  │    ├── eos_guard.py         # RTSP EOS 风暴保护
  │    └── ingress_manager.py   # 动态摄像头启动器
  │
  ├── watchdog/
  │    └── pipeline_watchdog.py # 管道健康监控 + 熔断器
  │
  └── telemetry.py              # Prometheus 指标定义

dashboard/             # React 前端
  ├── src/App.tsx       # 根路由器 + 全局认证状态
  ├── src/components/AppShell.tsx # 主布局（侧边栏+顶部栏）
  └── src/pages/        # 9个页面
       ├── DashboardPage.tsx  # 系统概览
       ├── CamerasPage.tsx   # 摄像头管理
       ├── UsersPage.tsx     # 用户管理+人脸注册
       ├── VehiclesPage.tsx  # 车辆管理
       ├── EventsPage.tsx    # 通行事件日志
       ├── DoorsPage.tsx     # 门禁控制
       ├── StrangersPage.tsx # 陌生人追踪
       └── AlertsPage.tsx    # 告警历史

module/
  └── module.yml        # Savant 管道 YAML（3阶段流水线）
  # Stage 1: YOLOv8s 车辆检测 + NvSORT 追踪
  # Stage 2a: LPR 车牌识别
  # Stage 2b: FR 人脸识别 + Anti-Spoofing
  # Stage 3: 黑名单引擎 + 告警分发

models/                 # ONNX 模型文件
  ├── scrfd_10g_bnkps.onnx  # SCRFD 人脸检测
  ├── glintr100.onnx        # ArcFace 人脸识别 (512维向量)
  ├── anti_spoof/minifasnet.onnx # 活体检测
  ├── yolov8/yolov8s*.engine # YOLOv8s 车辆检测 (TensorRT FP16)
  └── buffalo_l/            # InsightFace 套件

scripts/
  ├── download_models.py    # 自动下载模型脚本
  └── sql/seed_cameras.sql  # 摄像头初始数据

monitoring/grafana/         # Prometheus + Grafana 配置

tests/                       # Pytest 测试套件
  ├── unit/                  # 单元测试（business/fr/lpr/ingress/watchdog）
  └── integration/          # 集成测试
```

---

## 核心数据流

```
RTSP 摄像头流
    │
    ▼
Ingress Manager ──ZMQ──▶ Savant AI Core (GPU)
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
         ▼                     ▼                     ▼
    车牌检测              人脸检测+活体          车辆检测
    (YOLOv8n)             (SCRFD+ArcFace)       (YOLOv8s)
         │                     │                     │
         ▼                     ▼                     ▼
    PaddleOCR 识别        质量过滤               NvSORT 追踪
         │                     │                     │
         └─────────┬───────────┘                     │
                   ▼                                 │
              BlacklistEngine ◀──────────────────────┘
              (人脸+车牌联合检测)
                   │
          ┌────────┴────────┐
          ▼                 ▼
      AlertManager      AuditLogger
      (Telegram+Webhook)   (DB)
          │
          ▼
    JSON Egress + Prometheus Metrics
```

---

## 数据库 Schema（`backend/database.py`）

**关键表（从路由器的 SQL 查询推断）：**

| 表名 | 主要字段 | 用途 |
|------|---------|------|
| `users` | id, name, role (staff/blacklist/guest), face_embedding (512维向量) | 人员管理 |
| `vehicles` | id, plate_number, owner_name, is_blacklisted | 车辆管理 |
| `cameras` | id, name, rtsp_url, source_id, zone, fps_limit, enabled | 摄像头配置 |
| `doors` | id, name, camera_id, relay_url, is_open | 门禁设备 |
| `events` | id, person_id, camera_id, door_id, access_result, timestamp | 通行日志 |
| `strangers` | uid, face_embedding, first_seen, last_seen, notes | 陌生人记录 |

**向量搜索：** 使用 `pgvector` 扩展存储 512 维人脸向量，支持 `cosine distance` 近似最近邻搜索。

---

## LPR 车牌识别（`src/lpr/plate_ocr.py`）

**输入：** 车辆 ROI 图像
**输出：** 车牌文本 + 类别 + 置信度

**流水线：**
1. **YOLOv8n 检测** → 提取车牌 bounding box
2. **PaddleOCR SVTR_LCNet** → 字符识别
3. **Temporal Voting** → 多帧投票去重纠错
4. **Vietnamese Normalization** → 常见 OCR 错误修正
5. **Category Classification** → 正则匹配分类

**车牌分类正则：**

| 类别 | 正则模式 | 示例 |
|------|---------|------|
| `XE_MAY_DAN_SU` | `^[1-9][0-9]\s?[-.]?\s?[A-Z][1-9]\s?[-.]?\s?(\d{3}\.?\d{2}\|\d{4,5})$` | `29-E1 12345` |
| `O_TO_DAN_SU` | `^[1-9][0-9][A-Z][A-Z]?\s?[-.]?\s?(\d{3}\.?\d{2}\|\d{4,5})$` | `30A 12345` |
| `XE_QUAN_DOI` | 特殊军牌代码 | `AA 12345` |
| `BIEN_CA_NHAN` | 个性化牌照 | - |
| `KHONG_XAC_DINH` | 无法分类 | - |

**常见 OCR 错误修正映射：**
```
O ↔ D  |  1 ↔ T  |  0 ↔ D  |  6 ↔ G  |  7 ↔ T  |  8 ↔ B
```

**特殊处理：**
- **夜间模式**：亮度均值 < 80 → Gamma 校正 + CLAHE 增强
- **模糊过滤**：Laplacian 方差 < 25 → 跳过识别
- **去重机制**：同一车牌 60 秒内不重复保存
- **背景保存**：Worker 线程异步写 JSON + 切割图像到 `/Detect/`

---

## FR 人脸识别（`src/fr/face_recognizer.py`）

**流水线：**
```
Frame → SCRFD-10GF (检测) → 5点 landmarks → 质量评估
      → ArcFace R100 (512维向量) → L1/L2 缓存 → pgvector ANN → 结果
      → MiniFASNet (活体检测, 阈值 0.60)
```

**3级缓存策略：**

| 层级 | 存储 | TTL | 容量 | 用途 |
|------|------|-----|------|------|
| L1 | 进程内 LRU dict | 60秒 | 1000 | 热路径加速 |
| L2 | Redis | 5分钟 | - | 多进程共享 |
| L3 | pgvector ANN | - | - | 未知人员Fallback |

**陌生人追踪：**
- 累积 ≥3 个高质量帧 → 生成唯一 ID (SHA-256 前8位)
- 60秒无匹配 → 新陌生人记录
- 存储 512 维向量到 `strangers` 表

**Anti-Spoofing：**
- MiniFASNet V2 ONNX，输入 80×80
- 分数 ≥ 0.60 → 活体通过
- 分数 < 0.60 → 拒绝并记录

---

## Blacklist Engine（`src/business/blacklist_engine.py`）

**3层缓存：**
```
L1 (进程内 dict) → L2 (Redis) → DB (PostgreSQL)
```

**检查流程：**
1. **车辆黑名单** → `vehicles.is_blacklisted = TRUE`
2. **人员黑名单** → `users.role = 'blacklist'`
3. **区域权限** → 用户角色是否有权进入该 zone
4. **时间规则** → 是否在允许时间段内

**违规事件数据结构（BlacklistEvent）：**
```python
@dataclass
class BlacklistEvent:
    event_type: str      # "blacklist_person" | "blacklist_vehicle" | "zone_denied" | "time_denied"
    entity_type: str     # "person" | "vehicle"
    entity_id: str       # person_id 或 plate_number
    entity_name: str
    severity: Severity   # LOW | MEDIUM | HIGH | CRITICAL
    camera_id: str
    source_id: str
    reason: str
    timestamp: str       # ISO 8601 (越南时区)
    face_crop: np.ndarray | None
    plate_crop: np.ndarray | None
```

---

## Alert Manager（`src/business/alert_manager.py`）

**发送渠道：**
- **Telegram**：图片 + Markdown 格式消息（Bot API）
- **Webhook**：JSON POST 到外部系统

**限流机制：**
- 每个实体：1次/5分钟（可配置）
- 全局 RPM：50条/分钟
- 超过限流 → 排队，指数退避重试（最多3次）

**消息模板（Telegram）：**
```
🟡 *发现关注名单对象！*
📷 摄像头: `cam_01`
🕐 时间: `2026-04-08T15:30:00+07:00`
👤 姓名: `Nguyễn Văn A` — ID: `123`
📝 原因: _在黑名单中_
```

---

## EOS 风暴保护（`src/ingress/eos_guard.py`）

**问题：** RTSP 断连 → GStreamer 频繁发送 EOS → ZMQ 队列溢出 → AI Core 崩溃循环

**解决方案：**
- 统计 1 秒内 EOS 事件数量
- 阈值：5 EOS/秒
- 超过阈值 → 暂停转发 EOS，冷却 5 秒
- 冷却结束后 → 重置计数器 + 触发重连回调

---

## Pipeline Watchdog（`src/watchdog/pipeline_watchdog.py`）

**健康检查机制：**
- 每 30 秒检查 JSON egress 活跃度
- 超过 120 秒无新数据 → 判定为卡死

**自动重启顺序：**
```
JSON Egress → AI Core → Ingress
```

**熔断器：**
- 10 分钟内超过 3 次重启 → OPEN 状态（停止重启）
- 指数退避：5s → 10s → 20s → ... → 最大 120s
- OPEN 后需手动或超时后恢复 HALF-OPEN

---

## Prometheus 指标（`src/telemetry.py`）

**命名空间：** `svpro`

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `svpro_frames_processed_total` | Counter | source_id | 已处理帧数 |
| `svpro_lpr_events_total` | Counter | camera_id | LPR 事件数 |
| `svpro_fr_events_total` | Counter | camera_id | FR 事件数 |
| `svpro_ingress_fps` | Gauge | camera_id | 实际 FPS |
| `svpro_aicore_queue_depth` | Gauge | camera_id | 队列深度 |
| `svpro_dropped_total` | Counter | camera_id, component, drop_reason | 丢弃帧数 |
| `svpro_aicore_inference_ms` | Histogram | camera_id, model | 推理延迟 |
| `svpro_lpr_ocr_total` | Counter | camera_id, result | OCR 结果 |
| `svpro_fr_recognition_total` | Counter | camera_id, result | FR 结果 |
| `svpro_alerts_sent_total` | Counter | channel | 已发送告警 |
| `svpro_watchdog_restarts_total` | Counter | component | 重启次数 |
| `svpro_watchdog_circuit_open` | Gauge | - | 熔断器状态 |

**Drop reasons：** `queue_full` | `send_timeout` | `eos_storm_guarded` | `low_quality` | `ocr_fail`

---

## Docker Compose 服务架构

```
┌──────────────────────────────────────────────────────────┐
│                    SV-PRO Architecture                   │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  ┌─────────────┐   ZMQ IPC    ┌──────────────────┐      │
│  │   Ingress   │ ──────────▶  │   AI Core        │      │
│  │   Manager   │ input-video  │   (GPU Pipeline) │      │
│  │  (RTSP→ZMQ) │              │   Savant+DS     │      │
│  └─────────────┘              └────────┬─────────┘      │
│                                        │                 │
│                   ┌────────────────────┼────────────┐   │
│                   ▼                    ▼            ▼   │
│              ZMQ output           /Detect/       /metrics│
│              .ipc socket        (crops+JSON)   (Prom)   │
│                                        │            │   │
│  ┌────────────────────────────────────┴┐        ┌──┴─┐ │
│  │         JSON Egress Adapter          │        │    │ │
│  └──────────────────────────────────────┘        │    │ │
│                                                  ▼    ▼ │
│  ┌──────────────────────────────────────────────┐     │
│  │  Monitoring: Prometheus ──▶ Grafana         │◀────┘
│  │  Redis Exporter ── PostgreSQL Exporter       │
│  └──────────────────────────────────────────────┘
│                                                           │
│  ┌──────────────────────────────────────────────┐        │
│  │  Application: Backend FastAPI ◀── Dashboard  │        │
│  │  PostgreSQL (pgvector) ── Redis (cache)      │        │
│  └──────────────────────────────────────────────┘
└──────────────────────────────────────────────────────────┘
```

**服务列表：**

| Service | Port | 职责 |
|---------|------|------|
| `ingress-manager` | - | 动态 RTSP 拉流（根据数据库摄像头配置）|
| `savant-ai-core` | 8080 | GPU AI 流水线（LPR+FR）|
| `json-egress` | - | 元数据 JSON 输出适配器 |
| `video-egress` | 554, 888 | RTSP 输出（人脸/车牌截图流）|
| `postgres` | 5432 | PostgreSQL 16 + pgvector |
| `backend` | 8000 | FastAPI REST API |
| `redis` | 6379 | 热缓存 + 发布订阅 |
| `prometheus` | 9090 | 指标采集 |
| `grafana` | 3001 | 可视化仪表盘 |
| `redis-exporter` | 9121 | Redis 指标导出 |
| `postgres-exporter` | 9187 | PostgreSQL 指标导出 |
| `db-init` | - | 一次性初始化（迁移+种子数据）|

---

## API 端点总览（`backend/`）

| 路由 | 方法 | 路径 | 认证 | 功能 |
|------|------|------|------|------|
| auth | POST | `/auth/login` | Public | 用户名密码登录，返回 JWT |
| auth | GET | `/auth/me` | JWT | 获取当前用户信息 |
| cameras | GET | `/api/cameras` | JWT | 列表 |
| cameras | POST | `/api/cameras` | JWT | 创建 |
| cameras | PATCH | `/api/cameras/{id}` | JWT | 更新 |
| cameras | DELETE | `/api/cameras/{id}` | JWT | 删除 |
| users | GET | `/api/users` | JWT | 列表 |
| users | POST | `/api/users` | JWT | 创建 |
| users | GET | `/api/users/{id}` | JWT | 详情 |
| users | PATCH | `/api/users/{id}` | JWT | 更新 |
| users | DELETE | `/api/users/{id}` | JWT | 删除 |
| users | POST | `/api/users/{id}/enroll` | JWT | 注册人脸（保存512维向量）|
| vehicles | GET | `/api/vehicles` | JWT | 列表 |
| vehicles | POST | `/api/vehicles` | JWT | 创建 |
| vehicles | PATCH | `/api/vehicles/{plate}` | JWT | 更新 |
| vehicles | DELETE | `/api/vehicles/{plate}` | JWT | 删除 |
| vehicles | PATCH | `/api/vehicles/{plate}/blacklist` | JWT | 切换黑名单状态 |
| events | GET | `/api/events` | JWT | 列表（支持分页+过滤）|
| events | GET | `/api/events/stats` | JWT | 统计数据 |
| events | GET | `/api/events/{id}` | JWT | 详情 |
| events | POST | `/api/events/ingest` | API Key | AI Core 数据接入 |
| doors | GET | `/api/doors` | JWT | 列表 |
| doors | GET | `/api/doors/{id}` | JWT | 详情 |
| doors | POST | `/api/doors/{id}/trigger` | JWT | 触发开门 |
| doors | PATCH | `/api/doors/{id}/toggle` | JWT | 切换开关状态 |
| strangers | GET | `/api/strangers` | JWT | 列表 |
| strangers | GET | `/api/strangers/{uid}` | JWT | 详情 |
| strangers | DELETE | `/api/strangers/{uid}` | JWT | 删除 |
| strangers | POST | `/api/strangers/{uid}/notes` | JWT | 添加备注 |
| metrics | GET | `/api/metrics/summary` | JWT | Prometheus 汇总指标 |
| metrics | GET | `/api/metrics/pipeline` | JWT | 管道指标 |
| metrics | GET | `/api/metrics/watchdog` | JWT | 看门狗指标 |
| health | GET | `/health` | Public | 健康检查（DB+Redis）|

---

## 前端架构（`dashboard/`）

**状态管理：** React useState + Context（无 Redux）
**路由：** 手动条件渲染（非 React Router）
**API 调用：** `fetch` + 原生 JavaScript

**主要页面：**

| 页面 | 功能 |
|------|------|
| `LoginPage` | JWT 登录，存储到 localStorage |
| `DashboardPage` | Stats cards + 系统概览 |
| `CamerasPage` | 摄像头 CRUD 表格 |
| `UsersPage` | 用户 CRUD + 人脸注册表单 |
| `VehiclesPage` | 车辆列表 + 黑名单切换 |
| `EventsPage` | 通行事件日志（时间、结果、摄像头过滤）|
| `DoorsPage` | 门禁设备列表 + 开门按钮 |
| `StrangersPage` | 陌生人追踪 + 备注管理 |
| `AlertsPage` | 告警历史列表 |

**AppShell 布局：**
- 可折叠侧边栏（64px ↔ 240px）
- Top bar 显示实时时间 + "Live System" 指示灯
- Logout 按钮带确认对话框
- Lucide React 图标库

---

## 关键设计模式

### 1. 优雅降级
所有模块检测可选依赖：
```python
try:
    from prometheus_client import Counter, Gauge
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    # 使用 NoOp 替代
```

### 2. 两级缓存（热路径）
```
请求 → L1 (进程内) → L2 (Redis) → DB/pgvector
     (最快)       (共享)     (最慢)
```

### 3. 后台处理
- AlertManager：队列 + 守护线程
- PlateOCR：队列 + Worker 线程
- 不阻塞主流水线

### 4. 熔断器模式
- Watchdog 检测卡死 → 自动重启
- 超过阈值 → OPEN 状态 → 停止重启
- 指数退避防止抖动

---

## 快速修改指南

### 修改 LPR 车牌分类逻辑
→ `src/lpr/plate_ocr.py` → `class PlateCategory` 枚举 + `class PlateNormalizer`

### 修改人脸识别阈值
→ `src/fr/face_recognizer.py` → `cosine_similarity >= 0.5` 或 `anti_spoof >= 0.60`

### 添加新的告警渠道
→ `src/business/alert_manager.py` → 添加 `_send_*` 方法到 `AlertManager._send()`

### 修改区域门禁规则
→ `src/business/access_control.py` → `_check_zone_access()`

### 添加新的 Prometheus 指标
→ `src/telemetry.py` → 添加新的 `Counter/Gauge/Histogram`

### 修改 EOS 风暴阈值
→ `src/ingress/eos_guard.py` → `self.threshold_per_second = 5`（第47行）

### 修改 Watchdog 重启策略
→ `src/watchdog/pipeline_watchdog.py` → `max_restarts_per_window` 和 `base_delay`

---

## 环境变量参考（`.env.example`）

| 变量 | 说明 |
|------|------|
| `POSTGRES_DSN` | PostgreSQL 连接字符串 |
| `REDIS_HOST` / `REDIS_PORT` | Redis 连接 |
| `JWT_SECRET` | JWT 签名密钥 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 告警目标 Chat ID |
| `WEBHOOK_URL` | 外部 Webhook URL |
| `PROMETHEUS_URL` | Prometheus HTTP API 地址 |
