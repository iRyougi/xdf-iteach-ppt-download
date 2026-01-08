#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从 display 链接中解析 jsonUrl，下载 json.json，提取 coverImg 并按 _idx 排序生成 PDF。

依赖：
  pip install requests img2pdf

可选（如果你想做更多图片处理才需要）：
  pip install pillow
"""

import os
import json
import argparse
import tempfile
from urllib.parse import urlparse, parse_qs, unquote

import requests
import img2pdf


def ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def download_text(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    # json 通常是 utf-8；requests 会猜编码，不放心可强制：
    if not r.encoding:
        r.encoding = "utf-8"
    return r.text


def download_bytes(url: str, timeout: int = 30) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.content


def extract_json_url(maybe_display_url: str) -> str:
    """
    输入可能是：
    1) display.html?...&jsonUrl=https://.../json.json&...
    2) 直接就是 https://.../json.json

    返回 json.json 的真实 URL
    """
    # 如果本身就是 json 直链
    if "jsonUrl=" not in maybe_display_url and maybe_display_url.lower().endswith(
        ".json"
    ):
        return maybe_display_url

    parsed = urlparse(maybe_display_url)
    qs = parse_qs(parsed.query)

    if "jsonUrl" not in qs or not qs["jsonUrl"]:
        raise ValueError(
            "链接中未找到 jsonUrl 参数。请确认传入的是 display 链接或 json.json 直链。"
        )

    # parse_qs 解析后仍可能是编码过的
    json_url = qs["jsonUrl"][0]
    json_url = unquote(json_url)
    return json_url


def json_to_pdf(json_obj: dict, output_pdf: str) -> str:
    pages = json_obj.get("pages", [])
    if not isinstance(pages, list):
        raise ValueError("JSON 格式不正确：pages 不是数组。")

    # 按 _idx 排序
    pages_sorted = sorted(pages, key=lambda x: x.get("_idx", 0))

    image_infos = []
    for page in pages_sorted:
        cover_img = page.get("coverImg", "")
        if cover_img:
            image_infos.append(
                {
                    "idx": page.get("_idx", 0),
                    "url": cover_img,
                    "name": page.get("name", f"page_{page.get('_idx', 0)}"),
                }
            )

    if not image_infos:
        raise RuntimeError("没有提取到任何 coverImg 链接，无法生成 PDF。")

    ensure_dir(output_pdf)

    # 用临时目录存图，避免污染工作目录
    with tempfile.TemporaryDirectory(prefix="temp_images_") as temp_dir:
        downloaded = []

        for i, info in enumerate(image_infos, start=1):
            url = info["url"]
            idx = info["idx"]

            print(f"下载图片 {i}/{len(image_infos)} | idx={idx} | {info['name']}")
            try:
                content = download_bytes(url, timeout=30)
            except Exception as e:
                print(f"  ⚠️ 下载失败: {e}")
                continue

            img_path = os.path.join(temp_dir, f"{int(idx):06d}.png")
            with open(img_path, "wb") as f:
                f.write(content)

            downloaded.append(img_path)

        if not downloaded:
            raise RuntimeError("所有图片都下载失败，无法生成 PDF。")

        print(f"\n开始生成 PDF: {output_pdf}")
        with open(output_pdf, "wb") as f:
            f.write(img2pdf.convert(downloaded))

    print(f"✅ 完成：{output_pdf}")
    return output_pdf


def main():
    parser = argparse.ArgumentParser(
        description="从 display 链接（含 jsonUrl）或 json.json 直链下载 JSON，并提取 coverImg 生成 PDF"
    )
    parser.add_argument(
        "url",
        help="display 链接（包含 jsonUrl=...）或 json.json 直链",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="输出 PDF 文件名（例如: output.pdf 或 outputs/xxx.pdf）",
    )
    parser.add_argument(
        "--save-json",
        default="",
        help="可选：把下载到的 json.json 另存到指定路径（例如: downloaded.json）",
    )

    args = parser.parse_args()

    json_url = extract_json_url(args.url)
    print(f"解析到 jsonUrl: {json_url}")

    print("下载 json.json ...")
    json_text = download_text(json_url, timeout=30)

    # 解析 JSON
    try:
        json_obj = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ JSON 解析失败：{e}")

    # 可选保存 json
    if args.save_json:
        ensure_dir(args.save_json)
        with open(args.save_json, "w", encoding="utf-8") as f:
            f.write(json_text)
        print(f"已保存 JSON 到：{args.save_json}")

    # 生成 PDF
    json_to_pdf(json_obj, args.output)


if __name__ == "__main__":
    main()
