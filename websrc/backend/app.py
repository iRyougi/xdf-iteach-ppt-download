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
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>JSON → PDF 工具 | 神椿仮想世界研究開發部</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      /* 亮色模式变量 */
      --bg-gradient-start: #fdf2f8;
      --bg-gradient-end: #ede9fe;
      --card-bg: rgba(255, 255, 255, 0.85);
      --card-shadow: 0 8px 32px rgba(149, 117, 205, 0.15);
      --card-border: rgba(255, 255, 255, 0.6);
      --text-primary: #374151;
      --text-secondary: #6b7280;
      --text-muted: #9ca3af;
      --accent-gradient: linear-gradient(135deg, #ec4899, #a855f7, #6366f1);
      --accent-color: #a855f7;
      --accent-hover: #9333ea;
      --input-bg: rgba(255, 255, 255, 0.9);
      --input-border: #e5e7eb;
      --input-focus-border: #a855f7;
      --btn-text: #ffffff;
      --success-bg: rgba(16, 185, 129, 0.1);
      --success-text: #059669;
      --error-bg: rgba(239, 68, 68, 0.1);
      --error-text: #dc2626;
      --toggle-bg: #e5e7eb;
      --toggle-dot: #ffffff;
      --back-btn-bg: rgba(255, 255, 255, 0.7);
      --back-btn-hover: rgba(255, 255, 255, 0.95);
    }

    [data-theme="dark"] {
      /* 暗色模式变量 */
      --bg-gradient-start: #1a1625;
      --bg-gradient-end: #0f172a;
      --card-bg: rgba(30, 27, 45, 0.9);
      --card-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
      --card-border: rgba(255, 255, 255, 0.08);
      --text-primary: #f3f4f6;
      --text-secondary: #d1d5db;
      --text-muted: #9ca3af;
      --accent-gradient: linear-gradient(135deg, #f472b6, #c084fc, #818cf8);
      --accent-color: #c084fc;
      --accent-hover: #a855f7;
      --input-bg: rgba(45, 40, 65, 0.8);
      --input-border: rgba(255, 255, 255, 0.1);
      --input-focus-border: #c084fc;
      --btn-text: #ffffff;
      --success-bg: rgba(16, 185, 129, 0.15);
      --success-text: #34d399;
      --error-bg: rgba(239, 68, 68, 0.15);
      --error-text: #f87171;
      --toggle-bg: #374151;
      --toggle-dot: #f3f4f6;
      --back-btn-bg: rgba(45, 40, 65, 0.7);
      --back-btn-hover: rgba(55, 50, 80, 0.95);
    }

    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    body {
      font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      min-height: 100vh;
      background: linear-gradient(135deg, var(--bg-gradient-start), var(--bg-gradient-end));
      color: var(--text-primary);
      transition: background 0.4s ease, color 0.3s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      position: relative;
    }

    /* 背景装饰 */
    body::before {
      content: '';
      position: fixed;
      top: -50%;
      left: -50%;
      width: 200%;
      height: 200%;
      background: radial-gradient(circle at 30% 30%, rgba(236, 72, 153, 0.08) 0%, transparent 50%),
                  radial-gradient(circle at 70% 70%, rgba(99, 102, 241, 0.08) 0%, transparent 50%);
      animation: bgFloat 20s ease-in-out infinite;
      pointer-events: none;
      z-index: 0;
    }

    @keyframes bgFloat {
      0%, 100% { transform: translate(0, 0) rotate(0deg); }
      50% { transform: translate(-2%, -2%) rotate(3deg); }
    }

    /* 返回主站按钮 */
    .back-btn {
      position: fixed;
      top: 20px;
      left: 20px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 18px;
      background: var(--back-btn-bg);
      backdrop-filter: blur(10px);
      border: 1px solid var(--card-border);
      border-radius: 50px;
      color: var(--text-secondary);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      transition: all 0.3s ease;
      z-index: 100;
    }

    .back-btn:hover {
      background: var(--back-btn-hover);
      color: var(--accent-color);
      transform: translateX(-3px);
      box-shadow: 0 4px 15px rgba(168, 85, 247, 0.2);
    }

    .back-btn svg {
      width: 16px;
      height: 16px;
      transition: transform 0.3s ease;
    }

    .back-btn:hover svg {
      transform: translateX(-3px);
    }

    /* 主题切换开关 */
    .theme-toggle {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 100;
    }

    .toggle-wrapper {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 14px;
      background: var(--back-btn-bg);
      backdrop-filter: blur(10px);
      border: 1px solid var(--card-border);
      border-radius: 50px;
      transition: all 0.3s ease;
    }

    .toggle-icon {
      font-size: 16px;
      transition: opacity 0.3s ease;
    }

    .toggle-icon.sun { opacity: 1; }
    .toggle-icon.moon { opacity: 0.5; }
    [data-theme="dark"] .toggle-icon.sun { opacity: 0.5; }
    [data-theme="dark"] .toggle-icon.moon { opacity: 1; }

    .toggle-switch {
      position: relative;
      width: 50px;
      height: 26px;
      cursor: pointer;
    }

    .toggle-switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }

    .toggle-slider {
      position: absolute;
      inset: 0;
      background: var(--toggle-bg);
      border-radius: 26px;
      transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .toggle-slider::before {
      content: '';
      position: absolute;
      width: 20px;
      height: 20px;
      left: 3px;
      bottom: 3px;
      background: var(--toggle-dot);
      border-radius: 50%;
      transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    }

    .toggle-switch input:checked + .toggle-slider {
      background: var(--accent-gradient);
    }

    .toggle-switch input:checked + .toggle-slider::before {
      transform: translateX(24px);
    }

    /* 主卡片容器 */
    .container {
      position: relative;
      z-index: 1;
      width: 100%;
      max-width: 520px;
    }

    .card {
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      padding: 40px;
      box-shadow: var(--card-shadow);
      transition: all 0.4s ease;
    }

    .card:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 40px rgba(149, 117, 205, 0.2);
    }

    /* 标题区域 */
    .header {
      text-align: center;
      margin-bottom: 32px;
    }

    .logo {
      width: 64px;
      height: 64px;
      margin: 0 auto 16px;
      background: var(--accent-gradient);
      border-radius: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      box-shadow: 0 8px 24px rgba(168, 85, 247, 0.3);
    }

    .title {
      font-size: 24px;
      font-weight: 700;
      background: var(--accent-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin-bottom: 8px;
    }

    .subtitle {
      font-size: 14px;
      color: var(--text-muted);
      line-height: 1.6;
    }

    /* 表单元素 */
    .form-group {
      margin-bottom: 20px;
    }

    .form-label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-secondary);
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .form-label svg {
      width: 14px;
      height: 14px;
      opacity: 0.7;
    }

    .form-input {
      width: 100%;
      padding: 14px 18px;
      background: var(--input-bg);
      border: 2px solid var(--input-border);
      border-radius: 12px;
      font-size: 15px;
      color: var(--text-primary);
      transition: all 0.3s ease;
      font-family: inherit;
    }

    .form-input::placeholder {
      color: var(--text-muted);
    }

    .form-input:focus {
      outline: none;
      border-color: var(--input-focus-border);
      box-shadow: 0 0 0 4px rgba(168, 85, 247, 0.1);
    }

    /* 按钮 */
    .btn-primary {
      width: 100%;
      padding: 16px 24px;
      background: var(--accent-gradient);
      border: none;
      border-radius: 12px;
      color: var(--btn-text);
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.3s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      font-family: inherit;
      position: relative;
      overflow: hidden;
    }

    .btn-primary::before {
      content: '';
      position: absolute;
      top: 0;
      left: -100%;
      width: 100%;
      height: 100%;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
      transition: left 0.5s ease;
    }

    .btn-primary:hover::before {
      left: 100%;
    }

    .btn-primary:hover {
      transform: translateY(-2px);
      box-shadow: 0 8px 25px rgba(168, 85, 247, 0.4);
    }

    .btn-primary:active {
      transform: translateY(0);
    }

    .btn-primary:disabled {
      opacity: 0.7;
      cursor: not-allowed;
      transform: none;
    }

    .btn-primary svg {
      width: 20px;
      height: 20px;
    }

    /* 消息提示 */
    .message {
      margin-top: 20px;
      padding: 14px 18px;
      border-radius: 12px;
      font-size: 14px;
      display: none;
      align-items: flex-start;
      gap: 10px;
      animation: slideUp 0.3s ease;
    }

    @keyframes slideUp {
      from {
        opacity: 0;
        transform: translateY(10px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .message.show {
      display: flex;
    }

    .message.loading {
      background: rgba(168, 85, 247, 0.1);
      color: var(--accent-color);
    }

    .message.success {
      background: var(--success-bg);
      color: var(--success-text);
    }

    .message.error {
      background: var(--error-bg);
      color: var(--error-text);
    }

    .message svg {
      width: 18px;
      height: 18px;
      flex-shrink: 0;
      margin-top: 1px;
    }

    .spinner {
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    /* 页脚 */
    .footer {
      text-align: center;
      margin-top: 24px;
      font-size: 12px;
      color: var(--text-muted);
    }

    .footer a {
      color: var(--accent-color);
      text-decoration: none;
      transition: opacity 0.2s;
    }

    .footer a:hover {
      opacity: 0.8;
    }

    /* 响应式 */
    @media (max-width: 560px) {
      .card {
        padding: 28px 24px;
        border-radius: 20px;
      }

      .back-btn {
        padding: 8px 14px;
        font-size: 13px;
      }

      .toggle-wrapper {
        padding: 6px 10px;
      }

      .title {
        font-size: 20px;
      }
    }
  </style>
</head>
<body>
  <!-- 返回主站按钮 -->
  <a href="https://www.iryougi.com" class="back-btn">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M19 12H5M12 19l-7-7 7-7"/>
    </svg>
    返回主站
  </a>

  <!-- 主题切换 -->
  <div class="theme-toggle">
    <div class="toggle-wrapper">
      <span class="toggle-icon sun">☀️</span>
      <label class="toggle-switch">
        <input type="checkbox" id="themeToggle" onchange="toggleTheme()">
        <span class="toggle-slider"></span>
      </label>
      <span class="toggle-icon moon">🌙</span>
    </div>
  </div>

  <!-- 主卡片 -->
  <div class="container">
    <div class="card">
      <div class="header">
        <div class="logo">📄</div>
        <h1 class="title">JSON → PDF 转换器</h1>
        <p class="subtitle">输入 display 链接或 json.json 直链<br>一键生成精美 PDF 文档</p>
      </div>

      <div class="form-group">
        <label class="form-label">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
          </svg>
          链接地址
        </label>
        <input type="text" id="url" class="form-input" placeholder="https://iteach-cloudedit...display.html?...&jsonUrl=...">
      </div>

      <div class="form-group">
        <label class="form-label">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
          </svg>
          PDF 文件名
        </label>
        <input type="text" id="name" class="form-input" placeholder="output.pdf" value="output.pdf">
      </div>

      <button class="btn-primary" id="submitBtn" onclick="generatePDF()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        生成并下载 PDF
      </button>

      <div class="message" id="message">
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"></svg>
        <span class="text"></span>
      </div>

      <div class="footer">
        Powered by <a href="https://www.iryougi.com">神椿仮想世界研究開發部</a>
      </div>
    </div>
  </div>

  <script>
    // 主题切换功能
    function toggleTheme() {
      const isDark = document.getElementById('themeToggle').checked;
      document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
      localStorage.setItem('theme', isDark ? 'dark' : 'light');
    }

    // 初始化主题
    function initTheme() {
      const savedTheme = localStorage.getItem('theme');
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      const isDark = savedTheme ? savedTheme === 'dark' : prefersDark;
      
      document.getElementById('themeToggle').checked = isDark;
      document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
    }

    // 显示消息
    function showMessage(type, text) {
      const msg = document.getElementById('message');
      const icon = msg.querySelector('.icon');
      const textEl = msg.querySelector('.text');
      
      msg.className = 'message show ' + type;
      textEl.textContent = text;
      
      const icons = {
        loading: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
        success: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
        error: '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
      };
      
      icon.innerHTML = icons[type] || '';
      icon.classList.toggle('spinner', type === 'loading');
    }

    // 生成PDF
    async function generatePDF() {
      const urlInput = document.getElementById('url');
      const nameInput = document.getElementById('name');
      const btn = document.getElementById('submitBtn');
      
      const url = urlInput.value.trim();
      const output_name = nameInput.value.trim() || 'output.pdf';
      
      if (!url) {
        showMessage('error', '请输入链接地址');
        urlInput.focus();
        return;
      }
      
      btn.disabled = true;
      showMessage('loading', '正在处理中，请稍候...');
      
      try {
        const resp = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, output_name })
        });
        
        if (!resp.ok) {
          const text = await resp.text();
          throw new Error(text || '生成失败');
        }
        
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = output_name.endsWith('.pdf') ? output_name : output_name + '.pdf';
        a.click();
        URL.revokeObjectURL(a.href);
        
        showMessage('success', '生成成功！文件已开始下载');
      } catch (err) {
        showMessage('error', '生成失败：' + err.message);
      } finally {
        btn.disabled = false;
      }
    }

    // 回车提交
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.key === 'Enter' && !document.getElementById('submitBtn').disabled) {
        generatePDF();
      }
    });

    // 初始化
    initTheme();
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
