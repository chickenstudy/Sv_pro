# SV-PRO Docker 部署指南

## 📋 前提条件

### 硬件要求
- **GPU**: NVIDIA GPU với CUDA 支持（用于 AI 推理）
- **内存**: 最低 16GB RAM（推荐 32GB）
- **磁盘**: 至少 100GB 可用空间

### 软件要求
- **Docker**: 20.10+
- **Docker Compose**: v2.0+
- **NVIDIA Container Toolkit**: 用于 GPU 支持
- **Git**: 用于代码拉取

---

## 🚀 快速开始

### 1. 拉取代码

```bash
git clone <repository-url> /opt/sv-pro
cd /opt/sv-pro
```

### 2. 配置环境变量

复制环境变量文件并编辑：

```bash
cp .env.example .env
nano .env
```

**重要配置项**：

```env
# PostgreSQL（用于存储用户、车辆、人脸等数据）
POSTGRES_USER=svpro_user
POSTGRES_PASSWORD=svpro_pass      # ⚠️ 生产环境必须修改
POSTGRES_DB=svpro_db

# Redis（用于缓存）
REDIS_HOST=redis
REDIS_PORT=6379

# JWT 密钥（生产环境必须使用强随机密钥）
JWT_SECRET=your-strong-random-secret-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Telegram 告警（可选）
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Grafana 管理员密码
GF_ADMIN_USER=admin
GF_ADMIN_PASS=svpro2024            # ⚠️ 生产环境必须修改
```

### 3. 准备模型文件

将模型文件放入 `models/` 目录：

```
models/
├── yolov8/
│   └── yolov8s.onnx                    # 车辆检测模型
├── yolov8n_plate/
│   └── yolov8n_plate.onnx               # 车牌检测模型
├── scrfd_10g_bnkps.onnx                 # 人脸检测模型
├── glintr100.onnx                        # 人脸识别模型
├── anti_spoof/
│   └── minifasnet.onnx                  # 反欺诈模型
```

### 4. 构建并启动

```bash
# 构建所有镜像（首次运行需要较长时间）
docker compose build

# 启动所有服务
docker compose up -d

# 查看服务状态
docker compose ps
```

---

## 📁 目录结构

```
Sv_pro/
├── docker-compose.yml        # Docker 编排配置
├── Dockerfile.backend         # FastAPI 后端镜像
├── Dockerfile.savant-ai-core  # AI 推理核心镜像（需要 GPU）
├── Dockerfile.ingress-manager # RTSP 流管理镜像
├── .env                      # 环境变量配置
├── .dockerignore             # Docker 构建排除
│
├── backend/                  # FastAPI 后端代码
│   ├── main.py
│   ├── routers/
│   └── frontend/             # Vue.js 前端静态文件
│
├── src/                      # Savant Pipeline Python 模块
│   ├── lpr/                  # 车牌识别模块
│   ├── fr/                   # 人脸识别模块
│   ├── business/             # 业务逻辑模块
│   ├── ingress/              # RTSP 流管理
│   └── converters/           # 模型输出转换器
│
├── module/                   # Savant Pipeline 配置
│   └── module.yml            # 主配置文件
│
├── models/                   # AI 模型文件
├── tracker/                  # DeepStream 跟踪器配置
│   └── config_tracker_NvSORT.yml
│
├── scripts/sql/              # 数据库脚本
│   ├── schema.sql            # 数据库表结构
│   ├── seed_cameras.sql      # 摄像头初始数据
│   └── migrations/
│       └── 001_sprint4_5.sql # Sprint 4-5 迁移
│
├── monitoring/               # 监控配置
│   ├── prometheus.yml
│   └── grafana/
│       ├── dashboard.json
│       └── provisioning/
│
└── output/                   # JSON 输出目录
```

---

## 🌐 服务访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| **API 后端** | http://localhost:8000 | FastAPI 接口 |
| **API 文档** | http://localhost:8000/docs | Swagger UI |
| **前端** | http://localhost:8000 | Nginx 静态文件 |
| **Prometheus** | http://localhost:9090 | 指标采集 |
| **Grafana** | http://localhost:3001 | 可视化仪表板 |
| **AI Core** | http://localhost:9080 | Savant 健康检查 |
| **PostgreSQL** | localhost:5433 | 数据库（外部） |
| **Redis** | localhost:6379 | 缓存（外部） |

---

## 🔧 常用命令

### 启动/停止服务

```bash
# 启动所有服务
docker compose up -d

# 停止所有服务
docker compose down

# 重启特定服务
docker compose restart savant-ai-core

# 查看日志
docker compose logs -f savant-ai-core
docker compose logs -f backend
docker compose logs -f ingress-manager
```

### 查看状态

```bash
# 查看所有容器状态
docker compose ps

# 查看资源使用
docker stats

# 查看 GPU 使用
nvidia-smi
```

### 数据库操作

```bash
# 连接 PostgreSQL
docker exec -it svpro_postgres psql -U svpro_user -d svpro_db

# 执行 SQL 文件
docker exec -it svpro_postgres psql -U svpro_user -d svpro_db -f /sql/schema.sql
```

### 清理

```bash
# 删除所有容器和数据卷（⚠️ 会丢失所有数据）
docker compose down -v

# 重新构建特定镜像
docker compose build savant-ai-core
docker compose up -d savant-ai-core
```

---

## 🐛 故障排查

### 问题 1: GPU 不可用

```bash
# 检查 NVIDIA 驱动
nvidia-smi

# 检查 Docker NVIDIA 支持
docker run --rm --gpus all nvidia/cuda:11.8-base-ubuntu22.04 nvidia-smi
```

### 问题 2: savant-ai-core 启动失败

```bash
# 查看详细日志
docker compose logs savant-ai-core

# 常见原因：
# - 模型文件缺失 → 检查 models/ 目录
# - 端口被占用 → 9080 端口可能被其他服务占用
```

### 问题 3: 后端连接数据库失败

```bash
# 检查 PostgreSQL 是否就绪
docker compose ps postgres

# 检查连接字符串
docker exec -it svpro_backend env | grep POSTGRES
```

### 问题 4: ingress-manager 无法连接 RTSP 流

```bash
# 检查数据库中的摄像头配置
docker exec -it svpro_postgres psql -U svpro_user -d svpro_db -c "SELECT id, name, rtsp_url FROM cameras;"

# 查看 ingress-manager 日志
docker compose logs -f ingress-manager
```

---

## 📊 监控

### Prometheus 查询示例

```promql
# CPU 使用率
rate(process_cpu_seconds_total[5m])

# 内存使用
process_resident_memory_bytes

# HTTP 请求速率
rate(http_requests_total[5m])

# GPU 利用率
DCGM_FI_DEV_GPU_UTIL
```

### Grafana 仪表板

访问 http://localhost:3001 并登录：
- 用户名: `admin`
- 密码: `svpro2024`

---

## 🔒 安全建议

1. **修改所有默认密码**
2. **使用 HTTPS**（通过 Nginx 反向代理）
3. **限制数据库访问**（配置防火墙）
4. **定期备份数据**
5. **更新 Docker 镜像**（定期拉取最新版本）

---

## 📞 支持

如有问题，请检查：
1. Docker 日志: `docker compose logs`
2. Prometheus 指标: http://localhost:9090
3. Grafana 仪表板: http://localhost:3001
