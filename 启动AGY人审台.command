#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

if [[ ! -f review_workbench/dist/index.html ]]; then
  echo "正在构建 AGY 人审工作台……"
  (cd review_workbench && npm install && npm run build)
fi

echo "AGY 人审工作台已启动：http://127.0.0.1:8502/"
open "http://127.0.0.1:8502/"
exec .venv311/bin/python -m uvicorn song_discovery.review_api:app --host 127.0.0.1 --port 8502
