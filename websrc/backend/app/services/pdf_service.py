"""
PDF 生成服务模块
核心业务逻辑，优化了图片下载和 PDF 生成性能
支持进度回调
"""
import asyncio
from io import BytesIO
from typing import List, Tuple, Optional, Callable, Any

import httpx
import img2pdf
from fastapi import HTTPException

from app.config import settings
from app.utils.validators import validate_host
from app.utils.http_client import HTTPClientManager


# 进度回调类型：(阶段, 当前, 总数, 额外数据)
ProgressCallback = Callable[[str, int, int, Any], None]


async def download_image(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    idx: int,
    url: str
) -> Tuple[int, Optional[bytes]]:
    """
    下载单张图片
    返回 (索引, 图片数据) 元组，失败返回 (索引, None)
    """
    async with semaphore:
        try:
            response = await client.get(url)
            response.raise_for_status()
            return (idx, response.content)
        except Exception:
            return (idx, None)


async def download_images_parallel(
    image_urls: List[Tuple[int, str]],
    progress_callback: Optional[ProgressCallback] = None
) -> List[bytes]:
    """
    并行下载所有图片，支持进度回调
    返回按索引排序的图片字节数据列表
    """
    semaphore = asyncio.Semaphore(settings.download_concurrency)
    client = await HTTPClientManager.get_client()
    
    total = len(image_urls)
    completed = 0
    lock = asyncio.Lock()
    
    async def download_with_progress(idx: int, url: str) -> Tuple[int, Optional[bytes]]:
        nonlocal completed
        result = await download_image(client, semaphore, idx, url)
        
        async with lock:
            completed += 1
            if progress_callback:
                progress_callback("downloading", completed, total, None)
        
        return result
    
    # 创建下载任务
    tasks = [
        download_with_progress(idx, url)
        for idx, url in image_urls
    ]
    
    # 并行执行所有下载任务
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 处理结果
    valid_results = []
    for result in results:
        if isinstance(result, tuple) and len(result) == 2:
            idx, data = result
            if data is not None:
                valid_results.append((idx, data))
    
    if not valid_results:
        raise HTTPException(400, "图片全部下载失败")
    
    # 按索引排序
    valid_results.sort(key=lambda x: x[0])
    
    return [data for _, data in valid_results]


def convert_images_to_pdf(images: List[bytes]) -> bytes:
    """
    将图片字节数据转换为 PDF
    使用内存流，避免磁盘 IO
    """
    try:
        image_streams = [BytesIO(img) for img in images]
        pdf_bytes = img2pdf.convert(image_streams)
        return pdf_bytes
    except img2pdf.ImageOpenError as e:
        raise HTTPException(400, f"图片格式不支持：{e}")
    except Exception as e:
        raise HTTPException(500, f"生成 PDF 失败：{e}")


async def build_pdf_from_json(
    json_obj: dict,
    progress_callback: Optional[ProgressCallback] = None
) -> bytes:
    """
    从 JSON 对象构建 PDF
    主要业务流程：解析 JSON -> 下载图片 -> 生成 PDF
    支持进度回调
    """
    # 1. 解析 pages
    if progress_callback:
        progress_callback("parsing", 0, 0, None)
    
    pages = json_obj.get("pages", [])
    if not isinstance(pages, list):
        raise HTTPException(400, "JSON 格式不正确：pages 不是数组")
    
    if len(pages) > settings.max_pages:
        raise HTTPException(
            400, 
            f"pages 太多（{len(pages)}），超过限制 {settings.max_pages}"
        )
    
    # 2. 按 _idx 排序
    pages_sorted = sorted(pages, key=lambda x: x.get("_idx", 0))
    
    # 3. 提取图片 URL
    image_urls: List[Tuple[int, str]] = []
    for page in pages_sorted:
        cover = page.get("coverImg", "")
        if cover:
            image_urls.append((page.get("_idx", 0), cover))
    
    if not image_urls:
        raise HTTPException(400, "没有提取到任何 coverImg")
    
    if len(image_urls) > settings.max_images:
        raise HTTPException(
            400,
            f"图片太多（{len(image_urls)}），超过限制 {settings.max_images}"
        )
    
    # 4. 校验所有图片域名（SSRF 防护）
    for _, url in image_urls:
        validate_host(url, settings.allowed_image_hosts)
    
    # 通知总数
    total_images = len(image_urls)
    if progress_callback:
        progress_callback("start", 0, total_images, None)
    
    # 5. 并行下载所有图片
    images = await download_images_parallel(image_urls, progress_callback)
    
    # 6. 转换为 PDF
    if progress_callback:
        progress_callback("converting", total_images, total_images, None)
    
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(
        None,
        convert_images_to_pdf,
        images
    )
    
    if progress_callback:
        progress_callback("done", total_images, total_images, len(pdf_bytes))
    
    return pdf_bytes
