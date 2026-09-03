# NetEase Weekly Clipper 项目说明文档

NetEase Weekly Clipper 是一个自动化工具，旨在将网易云音乐播放列表转换为适用于社交媒体（如抖音、小红书）的竖屏短视频（3:4）。

---

## 1. 核心工作流

项目通过以下四个主要阶段完成视频制作：

### 1.1 数据抓取 (Crawler)
**命令**: `python run.py crawl "PLAYLIST_URL"`
- **功能**:
    - 自动提取歌单中的所有歌曲元数据（曲目名、艺人、专辑、封面）。
    - 抓取 Lrc 歌词并解析。
    - **亮点**:
        - 集成了 `pyncm` API，支持通过 `cookie.txt` 进行身份验证。
        - 智能检测歌曲高潮部分：优先使用 API提供的高潮时间，若无则通过歌词密度算法（寻找 30s 内歌词最密集的区间）自动计算。

### 1.2 AI 内容增强 (Researcher)
**命令**: `python run.py research`
- **功能**:
    - 使用 Google Gemini 1.5 Flash 模型为每首歌撰写专业的“Apple Music 风格”推荐语。
    - **亮点**:
        - 结合了 DuckDuckGo 搜索，自动寻找歌曲的创作背景、乐评或厂牌信息。
        - 智能提示词确保输出文风冷静、客观且有温度，避免使用俗套的“网感”词汇。

### 1.3 可视化编辑 (Editor)
**命令**: `python run.py edit`
- **功能**:
    - 基于 Streamlit 的可视化界面。
    - **亮点**:
        - **视觉画布**: 支持鼠标拖拽调整封面图、标题、描述、歌词和进度条的位置。
        - **即时预览**: 点击按钮即可渲染当前帧的预览图。
        - **曲目微调**: 手动调整每首歌的裁剪时间范围。
        - **字体管理**: 自动扫描系统字体并支持在界面中直接切换。

### 1.4 视频渲染 (Renderer)
**命令**: `python run.py render` / `python run.py intro`
- **功能**:
    - 使用 MoviePy 和 PIL 引擎生成最终高清视频。
    - **亮点**:
        - **多级渲染**: 支持渲染精美的开场视频 (`intro`) 和正片长片。
        - **特效处理**: 包含背景模糊、暗部梯度覆盖、噪点纹理、扫描线动画等高级视觉效果。
        - **动态排版**: 支持文字倾斜 (Skew)、多行自动换行以及平滑的转场（Crossfade）。

---

## 2. 详细配置指南

### 2.1 视频排版 (`config/default_layout.json`)
此文件控制正片视频的默认 UI。主要参数包括：
- `album_art`: 封面图的大小和位置。
- `title`/`artist`: 歌曲名和歌手名的字体、字径。
- `description`: 推荐语的排版。
- `lyrics`: 歌词背景透明度和位置。

### 2.2 开场特效 (`config/intro_layout.json`)
此文件定义了开场视频的“高级感”：
- `header`: 标题的渐变颜色（红->橙）。
- `album_list`: 滚动列表的间距、斜率设定。
- `global_effects`: 全局噪点和扫描线的开关。

### 2.3 字体设置
项目默认使用以下字体（Windows 系统）：
- 粗体: `msyhbd.ttc` (微软雅黑)
- 常规: `msyh.ttc`

---

## 3. 技术特性

- **MoviePy 版本兼容**: 内置兼容层，同时支持 MoviePy v1 和 v2 版本。
- **PIL 辅助渲染**: 由于 MoviePy 原生 TextClip 可能存在环境依赖问题，项目采用 PIL 手动渲染文字块并转换为 ImageClip，确保跨平台渲染的一致性。
- **自动化管线**: 通过 `run.py` 调度，用户只需关注 `project.json` 的中间状态流转。

---

## 4. 目录结构

- `src/crawler`: 接口抓取与逻辑解析。
- `src/researcher`: AI 智能搜索与文案撰写。
- `src/editor`: Streamlit UI 与配置保存。
- `src/renderer`: 视频导出与图像合成逻辑。
- `assets`: 存放字体、BGM、噪声纹理等静态资源。
- `output`: 最终视频和预览图的输出地。
