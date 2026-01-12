"""
HTTP 客户端模块
使用单例模式复用连接池，提高性能
"""
import httpx
from contextlib import asynccontextmanager
from typing import Optional

from app.config import settings


class HTTPClientManager:
    """HTTP 客户端管理器，复用连接池"""
    
    _client: Optional[httpx.AsyncClient] = None
    
    @classmethod
    def get_client_config(cls) -> dict:
        """获取客户端配置"""
        return {
            "timeout": httpx.Timeout(
                connect=10.0,
                read=settings.request_timeout,
                write=10.0,
                pool=5.0
            ),
            "limits": httpx.Limits(
                max_connections=settings.max_connections,
                max_keepalive_connections=settings.max_keepalive,
            ),
            "headers": {"User-Agent": settings.user_agent},
            "follow_redirects": True,
            "http2": True,  # 启用 HTTP/2 提升性能
        }
    
    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(**cls.get_client_config())
        return cls._client
    
    @classmethod
    async def close(cls) -> None:
        """关闭客户端"""
        if cls._client and not cls._client.is_closed:
            await cls._client.aclose()
            cls._client = None


@asynccontextmanager
async def get_http_client():
    """获取 HTTP 客户端的上下文管理器"""
    client = await HTTPClientManager.get_client()
    try:
        yield client
    except Exception:
        raise


async def fetch_json(url: str) -> dict:
    """获取 JSON 数据"""
    async with get_http_client() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def fetch_bytes(url: str) -> bytes:
    """获取二进制数据"""
    async with get_http_client() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
