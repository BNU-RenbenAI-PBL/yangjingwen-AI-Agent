#!/bin/bash
# 阅伴 · 启动后端（双击运行）
# 首次运行会自动安装依赖，之后直接启动
cd "$(dirname "$0")/backend"

# 检查并创建虚拟环境
if [ ! -d ".venv" ]; then
    echo "首次运行，正在创建环境并安装依赖（约 1-2 分钟）..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

# 检查 .env 是否已填 key
if [ ! -f ".env" ]; then
    cp .env.example .env
fi
if ! grep -q "DEEPSEEK_API_KEY=sk-" .env; then
    echo ""
    echo "⚠️  还没填 key：请先编辑 backend/.env 文件，"
    echo "   把 DEEPSEEK_API_KEY= 后面填上你的 DeepSeek key（sk- 开头），再重新双击本脚本。"
    echo ""
    read -p "按回车键退出..."
    exit 1
fi

echo "正在启动后端服务 http://127.0.0.1:8000 ..."
echo "（保持这个窗口开着，别关闭）"
.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000
