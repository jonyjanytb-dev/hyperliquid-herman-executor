#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  echo "[1/3] 创建 Python 虚拟环境..."
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

echo "[2/3] 安装/更新依赖..."
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[3/3] 启动本地交互式终端..."
python terminal.py
