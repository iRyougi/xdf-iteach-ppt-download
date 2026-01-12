# JSON → PDF 转换服务

将 JSON 中的 `coverImg` 图片转换为 PDF 文档的 Web 服务。

## 项目结构

```
json2pdf/
├── app/
│   ├── __init__.py          # 包初始化
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py            # 配置管理
│   ├── routes/
│   │   ├── __init__.py
│   │   └── api.py           # API 路由
│   ├── services/
│   │   ├── __init__.py
│   │   └── pdf_service.py   # PDF 生成服务
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── http_client.py   # HTTP 客户端
│   │   └── validators.py    # 验证工具
│   └── templates/
│       └── index.html       # 前端页面
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 功能特性

- 实时进度条：显示图片下载进度
- 计时器：显示处理耗时
- SSE 流式响应：实时推送进度状态
- 暗色模式：支持明暗主题切换

## 性能优化说明

相比原版的主要优化：

1. **HTTP 连接池复用**：使用单例模式管理 HTTP 客户端，避免重复创建连接
2. **内存流处理**：使用 `BytesIO` 替代临时文件，减少磁盘 IO
3. **HTTP/2 支持**：启用 HTTP/2 协议，提升并发下载效率
4. **并发数提升**：默认并发从 10 提升到 20
5. **线程池执行**：PDF 转换在线程池中执行，避免阻塞事件循环
6. **多 Worker 模式**：Docker 中使用 2 个 worker 进程
7. **SSE 进度推送**：使用 Server-Sent Events 实时推送进度

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker 部署

### 方式一：使用 docker-compose（推荐）

```bash
# 构建并启动
docker-compose up -d --build

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 方式二：手动构建

```bash
# 构建镜像
docker build -t json2pdf:latest .

# 运行容器
docker run -d \
  --name json2pdf \
  -p 8000:8000 \
  -e MAX_TASKS=4 \
  -e DOWNLOAD_CONCURRENCY=20 \
  --restart unless-stopped \
  json2pdf:latest
```

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_TASKS` | 4 | 同时处理的最大任务数 |
| `DOWNLOAD_CONCURRENCY` | 20 | 图片下载并发数 |
| `REQUEST_TIMEOUT` | 30 | 单个请求超时（秒） |
| `TOTAL_TIMEOUT` | 180 | 整体任务超时（秒） |

## API 接口

### POST /api/generate

生成 PDF 文件（直接下载，无进度）。

**请求体：**
```json
{
  "url": "https://iteach-cloudedit.xdf.cn/...",
  "output_name": "output.pdf"
}
```

**响应：** PDF 文件流

### POST /api/generate-with-progress

生成 PDF 文件（SSE 流式返回进度）。

**请求体：**
```json
{
  "url": "https://iteach-cloudedit.xdf.cn/...",
  "output_name": "output.pdf"
}
```

**响应：** SSE 事件流

```
data: {"stage": "waiting", "message": "排队中..."}
data: {"stage": "downloading", "current": 5, "total": 100, "percent": 4}
data: {"stage": "converting", "percent": 95}
data: {"stage": "complete", "task_id": "xxx", "filename": "output.pdf", "size": 1234567}
```

### GET /api/download/{task_id}

下载已生成的 PDF（配合 SSE 接口使用）。

**参数：**
- `task_id`: 生成完成后返回的任务 ID
- `filename`: 可选，下载文件名

### GET /health

健康检查端点，返回 `{"status": "healthy"}`

## 安全说明

- 域名白名单限制，防止 SSRF 攻击
- 文件名过滤，防止路径穿越
- 请求数量限制，防止资源耗尽
