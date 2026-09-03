.PHONY: help install build-ui test workbench daemon-install

PYTHON ?= .venv311/bin/python

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## 创建本地环境并安装后端、前端依赖
	python3.11 -m venv .venv311
	.venv311/bin/python -m pip install -r requirements.txt
	npm --prefix review_workbench ci

build-ui: ## 构建新歌人工审批前端
	npm --prefix review_workbench run build

workbench: build-ui ## 在 8502 端口启动审批工作台
	$(PYTHON) -m uvicorn song_discovery.review_api:app --host 127.0.0.1 --port 8502

daemon-install: build-ui ## 安装每日采集与开机启动服务
	./scripts/launchd/install_launchd.sh

test: ## 验证后端、审批前端和 KKBOX 扩展
	$(PYTHON) -m pytest -q
	npm --prefix review_workbench run build
	node --test tests/chrome_extension/test_extractors.js tests/chrome_extension/test_job_manager.js tests/chrome_extension/test_sidecar_polling.js
