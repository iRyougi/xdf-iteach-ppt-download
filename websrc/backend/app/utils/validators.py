"""
验证器模块
URL 解析、安全校验等工具函数
"""
import re
from urllib.parse import urlparse, parse_qs, unquote

from fastapi import HTTPException

from app.config import settings


def safe_filename(name: str) -> str:
    """生成安全的文件名，防止路径穿越"""
    name = name.strip()
    name = re.sub(r"[^\w\-.() \u4e00-\u9fff]+", "_", name)
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if len(name) > 120:
        name = name[:120]
    return name or "output.pdf"


def validate_host(url: str, allowed_hosts: set) -> None:
    """校验 URL 域名是否在白名单中"""
    try:
        hostname = urlparse(url).hostname
    except Exception:
        raise HTTPException(400, "URL 解析失败")
    
    if not hostname or hostname not in allowed_hosts:
        raise HTTPException(400, f"不允许访问的域名：{hostname}")


def extract_json_url(maybe_display_url: str) -> str:
    """从 display URL 中提取 JSON URL"""
    # 如果本身就是 json 直链
    if "jsonUrl=" not in maybe_display_url and maybe_display_url.lower().endswith(".json"):
        return maybe_display_url

    parsed = urlparse(maybe_display_url)
    qs = parse_qs(parsed.query)

    if "jsonUrl" not in qs or not qs["jsonUrl"]:
        raise HTTPException(
            400, 
            "链接中未找到 jsonUrl 参数，请确认传入 display 链接或 json.json 直链。"
        )

    return unquote(qs["jsonUrl"][0])
