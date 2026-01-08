#!/usr/bin/env python3
"""
从JSON文件提取coverImg图片链接，按_idx顺序下载并生成PDF
"""

import json
import os
import sys
import argparse
import requests
from PIL import Image
from io import BytesIO
import img2pdf


def extract_and_create_pdf(json_path, output_pdf="output.pdf"):
    """
    从JSON文件提取图片并生成PDF

    Args:
        json_path: JSON文件路径
        output_pdf: 输出PDF文件名
    """

    # 读取JSON文件
    print(f"读取JSON文件: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 提取所有页面信息
    pages = data.get("pages", [])
    print(f"找到 {len(pages)} 个页面")

    # 按_idx排序
    pages_sorted = sorted(pages, key=lambda x: x.get("_idx", 0))

    # 提取coverImg URLs
    image_urls = []
    for page in pages_sorted:
        cover_img = page.get("coverImg", "")
        if cover_img:
            image_urls.append(
                {
                    "idx": page.get("_idx"),
                    "url": cover_img,
                    "name": page.get("name", f"页面{page.get('_idx')}"),
                }
            )

    print(f"共提取到 {len(image_urls)} 个图片链接")

    # 创建临时目录存储下载的图片
    temp_dir = "temp_images"
    os.makedirs(temp_dir, exist_ok=True)

    # 下载图片
    downloaded_images = []
    for i, img_info in enumerate(image_urls):
        url = img_info["url"]
        idx = img_info["idx"]
        print(f"下载图片 {i+1}/{len(image_urls)}: {img_info['name']} (idx: {idx})")

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # 保存图片到临时文件
            temp_file = os.path.join(temp_dir, f"{idx:03d}.png")
            with open(temp_file, "wb") as f:
                f.write(response.content)

            downloaded_images.append(temp_file)

        except Exception as e:
            print(f"  ⚠️ 下载失败: {e}")
            continue

    print(f"\n成功下载 {len(downloaded_images)} 张图片")

    # 将图片转换为PDF
    if downloaded_images:
        print(f"\n生成PDF文件: {output_pdf}")

        # 确保输出目录存在
        output_dir = os.path.dirname(output_pdf)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 使用img2pdf转换
        with open(output_pdf, "wb") as f:
            f.write(img2pdf.convert(downloaded_images))

        print(f"✅ PDF文件生成成功: {output_pdf}")

        # 清理临时文件
        print("\n清理临时文件...")
        for img_file in downloaded_images:
            try:
                os.remove(img_file)
            except:
                pass

        try:
            os.rmdir(temp_dir)
        except:
            pass

        return output_pdf
    else:
        print("❌ 没有成功下载任何图片")
        return None


def main():
    """主函数"""
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description="从JSON文件提取coverImg图片并生成PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python extract_images_to_pdf.py input.json
  python extract_images_to_pdf.py data/lesson1.json
  python extract_images_to_pdf.py /path/to/file.json
        """,
    )

    parser.add_argument("json_file", help="输入的JSON文件路径")

    parser.add_argument(
        "-o", "--output-dir", default="outputs", help="输出目录，默认为 'outputs'"
    )

    # 解析命令行参数
    args = parser.parse_args()

    # 检查JSON文件是否存在
    if not os.path.exists(args.json_file):
        print(f"❌ 错误: 文件不存在: {args.json_file}")
        sys.exit(1)

    # 获取JSON文件名（不含扩展名）
    json_basename = os.path.basename(args.json_file)
    json_name_without_ext = os.path.splitext(json_basename)[0]

    # 构建输出PDF路径
    output_pdf = os.path.join(args.output_dir, f"{json_name_without_ext}.pdf")

    print(f"输入文件: {args.json_file}")
    print(f"输出文件: {output_pdf}")
    print("-" * 60)

    # 执行转换
    result = extract_and_create_pdf(args.json_file, output_pdf)

    if result:
        print(f"\n🎉 完成! PDF文件已保存至: {result}")
    else:
        print("\n⚠️ PDF生成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
