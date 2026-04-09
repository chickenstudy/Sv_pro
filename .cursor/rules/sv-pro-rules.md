# SV-PRO 项目开发规则

## 项目概述

SV-PRO (Surveillance Vision Professional) 是一个智能视觉监控系统，核心功能：
- **LPR**：车牌识别（YOLOv8n + PaddleOCR）
- **FR**：人脸识别（SCRFD + ArcFace + Anti-Spoofing）
- **Access Control**：门禁控制（区域+时间规则）
- **Blacklist Engine**：黑名单检测（3级缓存 + pgvector ANN）
- **Alert System**：Telegram + Webhook 告警
- **Monitoring**：Prometheus + Grafana

## 技术栈

| 层级 | 技术 |
|------|------|
| 视频管道 | Savant (GStreamer-based) + DeepStream |
| AI 推理 | ONNX Runtime (CUDA GPU) |
| 后端 API | FastAPI (异步) |
| 数据库 | PostgreSQL 16 + pgvector |
| 缓存 | Redis 7 |
| 前端 | React 18 + Vite + TypeScript |

## 目录结构

```
backend/              # FastAPI REST API
  ├── database.py     # asyncpg 连接池
  ├── main.py         # 入口，注册9个路由器
  └── routers/        # auth, cameras, users, vehicles, events, doors, strangers, metrics, health

src/                  # Savant pyfunc 插件
  ├── fr/             # 人脸识别 (SCRFD + ArcFace)
  ├── lpr/            # 车牌识别 (YOLOv8n + PaddleOCR)
  ├── business/       # 业务逻辑 (blacklist, alert, access_control)
  ├── ingress/        # RTSP 入口管理 (EOS 保护)
  ├── watchdog/       # 流水线健康监控 (熔断器)
  └── telemetry.py    # Prometheus 指标

dashboard/            # React 前端 (App.tsx + pages/)
module/               # Savant 管道 YAML (3阶段流水线)
models/               # ONNX 模型文件
```

## 核心开发规则

### 1. 始终使用简体中文
- 所有代码注释必须用中文
- 所有文档必须用中文
- AI Agent 必须用中文回答

### 2. 数据库操作
- 始终使用 `backend/database.py` 的 `get_db()` dependency 获取连接
- 使用 `asyncpg` 的异步查询
- 人脸向量存储用 `pgvector` 的 `vector(512)` 类型

### 3. 流水线模块开发（src/）
- Savant pyfunc 插件必须实现 `NvDsPyFuncPlugin` 基类
- Prometheus 指标必须使用 `src/telemetry.py` 中定义的指标名称（命名空间 `svpro`）
- 错误处理必须优雅降级（检测可选依赖）
- 缓存使用 2-3 层策略：L1(进程内) → L2(Redis) → DB

### 4. API 开发（backend/）
- 所有端点必须有认证（JWT 或 API Key）
- 使用 FastAPI 的 `Depends` 进行依赖注入
- 错误响应使用 `HTTPException` 并包含 `detail`
- 摄像头无关的端点不需要 `camera_id` 标签

### 5. 前端开发（dashboard/）
- 使用 React 18 + TypeScript
- 不使用 Redux，使用 useState + Context
- 图标库：`lucide-react`
- API 调用使用原生 fetch

### 6. 车牌识别特殊规则（src/lpr/plate_ocr.py）
- 车牌必须分类到以下类别：`XE_MAY_DAN_SU`, `O_TO_DAN_SU`, `XE_QUAN_DOI`, `BIEN_CA_NHAN`, `KHONG_XAC_DINH`
- OCR 错误修正映射：O↔D, 1↔T, 0↔D, 6↔G, 7↔T, 8↔B
- 夜间模式：亮度 < 80 时使用 Gamma + CLAHE
- 去重：同一车牌 60 秒内不重复

### 7. 人脸识别特殊规则（src/fr/face_recognizer.py）
- 人脸向量：512 维
- 相似度阈值：≥ 0.5 为同一人
- 活体阈值：≥ 0.60
- 陌生人累积：≥3 个高质量帧才生成 ID
- 缓存 TTL：L1=60秒, L2=5分钟

### 8. 黑名单引擎规则（src/business/blacklist_engine.py）
- 检查顺序：车辆黑名单 → 人员黑名单 → 区域权限 → 时间规则
- 严重级别：LOW | MEDIUM | HIGH | CRITICAL
- 告警限流：每实体 1次/5分钟，全局 50次/分钟

### 9. EOS 风暴保护规则（src/ingress/eos_guard.py）
- 阈值：5 EOS/秒
- 超过阈值暂停转发，冷却 5 秒
- 冷却结束后重置计数器

### 10. Watchdog 规则（src/watchdog/pipeline_watchdog.py）
- 健康检查间隔：30 秒
- 卡死判定：120 秒无 JSON egress
- 重启顺序：JSON Egress → AI Core → Ingress
- 熔断器：10分钟内超过 3 次重启 → OPEN 状态
- 指数退避：5s → 10s → 20s → ... → 最大 120s

## Git 工作流

- 所有新功能必须有对应的单元测试（`tests/unit/`）
- 集成测试放在 `tests/integration/`
- 修改核心模块（blacklist_engine, face_recognizer, plate_ocr）前先读懂现有逻辑
- commit message 使用中文，简短描述改动原因

## 阅读顺序建议

修改前应阅读的相关文件：

| 修改目标 | 必读文件 |
|---------|---------|
| LPR 逻辑 | `src/lpr/plate_ocr.py`, `src/business/blacklist_engine.py` |
| FR 逻辑 | `src/fr/face_recognizer.py`, `src/business/blacklist_engine.py` |
| API 端点 | `backend/main.py`, 对应 `routers/*.py` |
| 告警系统 | `src/business/alert_manager.py` |
| 门禁规则 | `src/business/access_control.py` |
| 前端页面 | `dashboard/src/App.tsx`, 对应 `pages/*.tsx` |
| 数据库 | `backend/database.py` |
| 监控指标 | `src/telemetry.py` |
| Docker | `docker-compose.yml`, `Dockerfile.*` |
