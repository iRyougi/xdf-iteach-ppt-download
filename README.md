## 使用方式

1. 传 display 链接 + 自定义 PDF 名称
   ```
   python link_to_pdf.py "https://iteach-cloudedit.xdf.cn/display.html?...&jsonUrl=https://iteachcdn.xdf.cn/netdisk/dev/xxxx/json.json&..." -o "新概念 Unit21.pdf"
   ```
2. 直接传 json.json 直链 + 自定义 PDF 名称
   ```
   python link_to_pdf.py "https://iteachcdn.xdf.cn/netdisk/dev/xxxx/json.json" -o "Unit21.pdf"
   ```
3. 顺便把 json.json 保存下来（可选）
   ```
   python link_to_pdf.py "https://iteach-cloudedit.xdf.cn/display.html?...&jsonUrl=..." -o "Unit21.pdf" --save-json "downloaded.json"
   ```
