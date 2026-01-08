import os
import re
import json
import tempfile
import asyncio
from urllib.parse import urlparse, parse_qs, unquote

import httpx
import img2pdf
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(title="JSON CoverImg -> PDF")

# ===== 安全设置（强烈建议按你的业务收紧）=====
ALLOWED_HOSTS = {
    "iteach-cloudedit.xdf.cn",
    "iteachcdn.xdf.cn",
}
# 同时也允许 coverImg 的 CDN 域名（如果 coverImg 还有其它域名，需要加进来）
ALLOWED_IMAGE_HOSTS = {
    "iteachcdn.xdf.cn",
}
MAX_PAGES = 2000  # 防止超大 JSON
MAX_IMAGES = 2000  # 防止超大图片数
REQUEST_TIMEOUT = 30.0  # 单次请求超时
TOTAL_TIMEOUT = 180.0  # 整个生成任务最大时间（秒）
CONCURRENCY = 10  # 同时下载图片的并发数

# ===== 简单并发闸门（避免服务器被打爆）=====
sema = asyncio.Semaphore(2)  # 同时最多2个生成任务，你可按机器规格调整


class GenerateReq(BaseModel):
    url: str = Field(..., description="display 链接（含 jsonUrl）或 json.json 直链")
    output_name: str = Field("output.pdf", description="下载时显示的PDF文件名")


def safe_filename(name: str) -> str:
    # 只保留安全字符，避免路径穿越
    name = name.strip()
    name = re.sub(r"[^\w\-.() \u4e00-\u9fff]+", "_", name)  # 允许中文
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if len(name) > 120:
        name = name[:120]
    return name or "output.pdf"


def host_allowed(url: str, allowed_hosts: set[str]) -> None:
    try:
        h = urlparse(url).hostname
    except Exception:
        raise HTTPException(400, "URL 解析失败")
    if not h or h not in allowed_hosts:
        raise HTTPException(400, f"不允许访问的域名：{h}")


def extract_json_url(maybe_display_url: str) -> str:
    # 如果本身就是 json 直链
    if "jsonUrl=" not in maybe_display_url and maybe_display_url.lower().endswith(
        ".json"
    ):
        return maybe_display_url

    parsed = urlparse(maybe_display_url)
    qs = parse_qs(parsed.query)

    if "jsonUrl" not in qs or not qs["jsonUrl"]:
        raise HTTPException(
            400, "链接中未找到 jsonUrl 参数，请确认传入 display 链接或 json.json 直链。"
        )

    json_url = unquote(qs["jsonUrl"][0])
    return json_url


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url)
    r.raise_for_status()
    return r.text


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    r.raise_for_status()
    return r.content


async def build_pdf_bytes(json_obj: dict) -> bytes:
    pages = json_obj.get("pages", [])
    if not isinstance(pages, list):
        raise HTTPException(400, "JSON 格式不正确：pages 不是数组。")

    if len(pages) > MAX_PAGES:
        raise HTTPException(400, f"pages 太多（{len(pages)}），超过限制 {MAX_PAGES}")

    pages_sorted = sorted(pages, key=lambda x: x.get("_idx", 0))

    image_urls = []
    for page in pages_sorted:
        cover = page.get("coverImg", "")
        if cover:
            image_urls.append((page.get("_idx", 0), cover))

    if not image_urls:
        raise HTTPException(400, "没有提取到任何 coverImg。")

    if len(image_urls) > MAX_IMAGES:
        raise HTTPException(
            400, f"图片太多（{len(image_urls)}），超过限制 {MAX_IMAGES}"
        )

    # 校验所有图片域名（SSRF 防护）
    for _, u in image_urls:
        host_allowed(u, ALLOWED_IMAGE_HOSTS)

    limits = httpx.Limits(
        max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY
    )
    timeout = httpx.Timeout(REQUEST_TIMEOUT)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
    }

    async with httpx.AsyncClient(
        timeout=timeout, headers=headers, limits=limits, follow_redirects=True
    ) as client:
        # 用临时目录存图片，img2pdf 直接吃文件路径最省事
        with tempfile.TemporaryDirectory(prefix="temp_images_") as td:

            dl_sema = asyncio.Semaphore(CONCURRENCY)

            async def download_one(idx: int, url: str) -> str | None:
                async with dl_sema:
                    try:
                        content = await fetch_bytes(client, url)
                        path = os.path.join(td, f"{int(idx):06d}.img")
                        with open(path, "wb") as f:
                            f.write(content)
                        return path
                    except Exception:
                        return None

            tasks = [download_one(idx, url) for idx, url in image_urls]
            results = await asyncio.gather(*tasks)

            files = [p for p in results if p]
            if not files:
                raise HTTPException(400, "图片全部下载失败。")

            # 注意：img2pdf 对部分图片格式可能不兼容（比如某些 webp）
            # 如果你遇到这种情况，我可以再给你加 Pillow 转 PNG 的兜底逻辑。
            try:
                pdf_bytes = img2pdf.convert(files)
            except Exception as e:
                raise HTTPException(500, f"生成 PDF 失败：{e}")

            return pdf_bytes


@app.get("/", response_class=HTMLResponse)
def index():
    # 简单前端页面（也可独立放静态站点）
    return """
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>JSON -> PDF 工具</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;max-width:860px;margin:40px auto;padding:0 16px;}
    input,button{font-size:16px;padding:10px;}
    input{width:100%;box-sizing:border-box;margin:8px 0;}
    button{cursor:pointer;}
    .row{display:flex;gap:12px;align-items:center;}
    .row > div{flex:1;}
    #msg{white-space:pre-wrap;margin-top:12px;}
  </style>
</head>
<body>
  <h2>从 display 链接生成 PDF</h2>
  <p>输入 display 链接（含 jsonUrl）或 json.json 直链，输出自定义 PDF 名称。</p>

  <label>链接</label>
  <input id="url" placeholder="https://iteach-cloudedit...display.html?...&jsonUrl=...json.json&..." />

  <div class="row">
    <div>
      <label>PDF 文件名</label>
      <input id="name" placeholder="Unit21.pdf" value="output.pdf" />
    </div>
    <div style="flex:0">
      <label>&nbsp;</label><br />
      <button onclick="go()">生成并下载</button>
    </div>
  </div>

  <div id="msg"></div>

<script>
async function go(){
  const msg = document.getElementById("msg");
  msg.textContent = "处理中...\\n";
  const url = document.getElementById("url").value.trim();
  const output_name = document.getElementById("name").value.trim() || "output.pdf";
  if(!url){ msg.textContent = "请填写链接"; return; }

  const resp = await fetch("/api/generate", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({url, output_name})
  });

  if(!resp.ok){
    const t = await resp.text();
    msg.textContent = "失败：\\n" + t;
    return;
  }

  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = output_name.endsWith(".pdf") ? output_name : (output_name + ".pdf");
  a.click();
  msg.textContent = "完成，已开始下载。";
}
</script>
</body>
</html>
"""


@app.post("/api/generate")
async def generate(req: GenerateReq):
    # 限制同时生成任务数
    async with sema:
        out_name = safe_filename(req.output_name)

        json_url = extract_json_url(req.url)

        # 校验 json 域名（SSRF 防护）
        host_allowed(json_url, ALLOWED_HOSTS)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        }

        timeout = httpx.Timeout(REQUEST_TIMEOUT)
        async with httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        ) as client:
            try:
                # 总超时控制
                async def _work():
                    text = await fetch_text(client, json_url)
                    try:
                        json_obj = json.loads(text)
                    except json.JSONDecodeError as e:
                        raise HTTPException(400, f"JSON 解析失败：{e}")
                    return await build_pdf_bytes(json_obj)

                pdf_bytes = await asyncio.wait_for(_work(), timeout=TOTAL_TIMEOUT)

            except asyncio.TimeoutError:
                raise HTTPException(
                    504, f"生成超时（>{TOTAL_TIMEOUT}s），请重试或减少内容。"
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(500, f"服务异常：{e}")

        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )
