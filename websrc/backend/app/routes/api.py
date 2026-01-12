"""
API 路由模块
处理 HTTP 请求和响应，支持 SSE 进度上报
"""
import asyncio
import json
import uuid
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.utils import safe_filename, validate_host, extract_json_url, fetch_json
from app.services.pdf_service import build_pdf_from_json

router = APIRouter()

# 并发控制信号量
_semaphore = asyncio.Semaphore(settings.max_tasks)

# 存储生成的 PDF（临时缓存，用于 SSE 模式）
_pdf_cache: Dict[str, bytes] = {}


class GenerateRequest(BaseModel):
    """PDF 生成请求"""
    url: str = Field(..., description="display 链接（含 jsonUrl）或 json.json 直链")
    output_name: str = Field("output.pdf", description="下载时显示的 PDF 文件名")


@router.post("/generate")
async def generate_pdf(req: GenerateRequest):
    """
    生成 PDF 接口（直接返回文件，无进度）
    """
    async with _semaphore:
        output_name = safe_filename(req.output_name)
        json_url = extract_json_url(req.url)
        validate_host(json_url, settings.allowed_hosts)
        
        try:
            async def _work():
                json_obj = await fetch_json(json_url)
                return await build_pdf_from_json(json_obj)
            
            pdf_bytes = await asyncio.wait_for(
                _work(),
                timeout=settings.total_timeout
            )
            
        except asyncio.TimeoutError:
            raise HTTPException(504, f"生成超时（>{settings.total_timeout}s）")
        except HTTPException:
            raise
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败：{e}")
        except Exception as e:
            raise HTTPException(500, f"服务异常：{e}")
        
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{output_name}"',
                "Content-Length": str(len(pdf_bytes)),
            }
        )


@router.post("/generate-with-progress")
async def generate_pdf_with_progress(req: GenerateRequest):
    """
    生成 PDF 接口（SSE 流式返回进度）
    返回 SSE 事件流，包含进度信息和最终的下载 ID
    """
    output_name = safe_filename(req.output_name)
    task_id = str(uuid.uuid4())
    
    async def event_generator():
        try:
            # 等待获取信号量
            yield f"data: {json.dumps({'stage': 'waiting', 'message': '排队中...'})}\n\n"
            
            async with _semaphore:
                yield f"data: {json.dumps({'stage': 'started', 'message': '开始处理'})}\n\n"
                
                # 提取 JSON URL
                json_url = extract_json_url(req.url)
                validate_host(json_url, settings.allowed_hosts)
                
                yield f"data: {json.dumps({'stage': 'fetching', 'message': '获取数据中...'})}\n\n"
                
                # 获取 JSON
                json_obj = await fetch_json(json_url)
                
                # 进度回调队列
                progress_queue: asyncio.Queue = asyncio.Queue()
                
                def progress_callback(stage: str, current: int, total: int, extra: Any):
                    """进度回调，将进度放入队列"""
                    progress_queue.put_nowait({
                        "stage": stage,
                        "current": current,
                        "total": total,
                        "extra": extra
                    })
                
                # 启动 PDF 生成任务
                pdf_task = asyncio.create_task(
                    build_pdf_from_json(json_obj, progress_callback)
                )
                
                # 轮询进度队列并发送 SSE
                while not pdf_task.done():
                    try:
                        progress = await asyncio.wait_for(
                            progress_queue.get(),
                            timeout=0.5
                        )
                        
                        # 构造进度消息
                        if progress["stage"] == "start":
                            msg = {
                                "stage": "downloading",
                                "current": 0,
                                "total": progress["total"],
                                "percent": 0,
                                "message": f"准备下载 {progress['total']} 张图片..."
                            }
                        elif progress["stage"] == "downloading":
                            percent = int(progress["current"] / progress["total"] * 90) if progress["total"] > 0 else 0
                            msg = {
                                "stage": "downloading",
                                "current": progress["current"],
                                "total": progress["total"],
                                "percent": percent,
                                "message": f"下载中 {progress['current']}/{progress['total']}"
                            }
                        elif progress["stage"] == "converting":
                            msg = {
                                "stage": "converting",
                                "percent": 95,
                                "message": "正在生成 PDF..."
                            }
                        elif progress["stage"] == "done":
                            msg = {
                                "stage": "done",
                                "percent": 100,
                                "message": "生成完成！"
                            }
                        else:
                            msg = {"stage": progress["stage"], "message": "处理中..."}
                        
                        yield f"data: {json.dumps(msg)}\n\n"
                        
                    except asyncio.TimeoutError:
                        # 发送心跳保持连接
                        yield f"data: {json.dumps({'stage': 'heartbeat'})}\n\n"
                
                # 获取结果
                pdf_bytes = await pdf_task
                
                # 缓存 PDF
                _pdf_cache[task_id] = pdf_bytes
                
                # 5 分钟后自动清理
                asyncio.create_task(cleanup_cache(task_id, 300))
                
                # 发送完成消息，包含下载 ID
                yield f"data: {json.dumps({'stage': 'complete', 'task_id': task_id, 'filename': output_name, 'size': len(pdf_bytes), 'percent': 100})}\n\n"
                
        except HTTPException as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': e.detail})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def cleanup_cache(task_id: str, delay: int):
    """延迟清理缓存"""
    await asyncio.sleep(delay)
    _pdf_cache.pop(task_id, None)


@router.get("/download/{task_id}")
async def download_pdf(task_id: str, filename: str = "output.pdf"):
    """
    下载已生成的 PDF
    """
    pdf_bytes = _pdf_cache.get(task_id)
    if not pdf_bytes:
        raise HTTPException(404, "PDF 不存在或已过期，请重新生成")
    
    output_name = safe_filename(filename)
    
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{output_name}"',
            "Content-Length": str(len(pdf_bytes)),
        }
    )
