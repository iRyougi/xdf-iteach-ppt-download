"""
应用配置模块
集中管理所有配置项，便于维护和环境切换
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """应用配置"""
    
    # 允许访问的域名（SSRF 防护）
    allowed_hosts: set = field(default_factory=lambda: {
        "iteach-cloudedit.xdf.cn",
        "iteachcdn.xdf.cn",
    })
    
    allowed_image_hosts: set = field(default_factory=lambda: {
        "iteachcdn.xdf.cn",
    })
    
    # 限制配置
    max_pages: int = 2000
    max_images: int = 2000
    
    # 超时配置（秒）
    request_timeout: float = 30.0
    total_timeout: float = 180.0
    
    # 并发配置
    download_concurrency: int = 20  # 提高并发数
    max_tasks: int = 4  # 同时最多生成任务数
    
    # HTTP 客户端配置
    max_connections: int = 50
    max_keepalive: int = 20
    
    # 用户代理
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
    
    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载配置"""
        return cls(
            max_tasks=int(os.getenv("MAX_TASKS", 4)),
            download_concurrency=int(os.getenv("DOWNLOAD_CONCURRENCY", 20)),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", 30.0)),
            total_timeout=float(os.getenv("TOTAL_TIMEOUT", 180.0)),
        )


# 全局配置实例
settings = Settings.from_env()
