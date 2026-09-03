# NetEase Weekly Clipper

从三平台发现华语摇滚新发行，经机器初筛和人工审批生成网易云周更歌单，再自动完成研究、TTS、片头、歌曲正片、整片渲染及发布文案的一体化生产工具。

## 核心流程
1. **新歌发现**：每天北京时间 01:00 及服务启动时采集 QQ 音乐、网易云音乐和 KKBOX 新发行。
2. **机筛与人审**：去重和宽松机筛后，在 8502 工作台批量审批、排序并记录反馈。
3. **歌单发布**：将定稿歌曲按指定顺序发布到网易云周更歌单。
4. **视频制作**：自动完成资料研究、克隆音色 TTS、片头、歌曲正片和整片渲染。
5. **交付文案**：根据本期发行生成可直接发布的标题与正文。

---

## 快速开始
详细的功能说明和配置指南请参考：
👉 **[项目详细说明文档 (docs.md)](./docs.md)**

完整模块关系见 **[一体化系统架构](./docs/architecture.md)**。

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
