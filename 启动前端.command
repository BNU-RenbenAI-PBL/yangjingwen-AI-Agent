#!/bin/bash
# 阅伴 · 启动前端（双击运行，会自动打开浏览器）
cd "$(dirname "$0")/frontend"

echo "正在启动前端，稍后会自动打开浏览器 http://localhost:5500"
echo "（保持这个窗口开着，别关闭）"
sleep 1
open "http://localhost:5500" 2>/dev/null
python3 -m http.server 5500
