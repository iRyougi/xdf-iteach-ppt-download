"""
JSON → PDF 转换服务
FastAPI 应用入口
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.routes import api_router
from app.utils.http_client import HTTPClientManager


# 应用生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动和关闭时的处理"""
    # 启动时：预热 HTTP 客户端
    await HTTPClientManager.get_client()
    yield
    # 关闭时：清理资源
    await HTTPClientManager.close()


# 创建 FastAPI 应用
app = FastAPI(
    title="JSON CoverImg -> PDF",
    description="将 JSON 中的 coverImg 图片转换为 PDF 文档",
    version="2.0.0",
    lifespan=lifespan,
)

# 注册 API 路由
app.include_router(api_router, prefix="/api", tags=["PDF Generation"])

# 模板目录
TEMPLATE_DIR = Path(__file__).parent / "templates"


@app.get("/", response_class=HTMLResponse)
async def index():
    """首页"""
    template_path = TEMPLATE_DIR / "index.html"
    return template_path.read_text(encoding="utf-8")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}
