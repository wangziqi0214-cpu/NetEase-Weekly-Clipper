# NetEase Weekly Clipper

自动化将网易云音乐歌单转换为 3:4 竖屏短视频的生产力工具。

## 核心流程
1. **数据抓取 (Crawler)**: 自动提取歌单元数据与高潮片段。
2. **AI 研究 (Researcher)**: 使用 Gemini 生成专业乐评。
3. **可视化编辑 (Editor)**: 拖拽式排版与曲目微调。
4. **视频渲染 (Renderer)**: 生成具有“高级感”的开场与正片视频。

---

## 快速开始
详细的功能说明和配置指南请参考：
👉 **[项目详细说明文档 (docs.md)](./docs.md)**

### 安装依赖
```bash
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，再填写本机使用的 KKBOX 凭据。网易云登录 Cookie 保存在本地 `cookie.txt`。这两个文件都不会提交到 Git。

人工审核工作台的前端依赖与构建：

```bash
cd review_workbench
npm ci
npm run build
```

### 运行流水线
```bash
python run.py crawl "歌单链接"
python run.py research
python run.py edit
python run.py render
```

### 一键快捷方式 (Windows)
直接双击根目录下的 **`一键生成视频.bat`**，输入歌单链接即可自动完成全流程。

或者使用命令行：
```bash
python run.py full "歌单链接"
```

## 目录结构
- `src/`: 核心源代码
- `song_discovery/`: 三平台新歌采集、筛选、发布与视频工作流
- `review_workbench/`: 人工审核工作台前端源码
- `chrome_extension/`: KKBOX 采集扩展（备用方案）
- `assets/`: 静态资源文件
- `config/`: 布局配置文件
- `output/`: 结果导出目录

`output/`、本地数据库、模型、下载音频、封面、克隆音色、日志和历史备份只保留在本机，不进入 Git 仓库。
