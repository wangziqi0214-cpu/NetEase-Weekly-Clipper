# Walkthrough: Encapsulated NetEase Crawler / 演练：封装的网易云爬虫

I have consolidated the playlist track fetching, lyric parsing, and audio downloading features into a single utility module. / 我已将歌单曲目获取、歌词解析和音频下载功能整合到一个工具模块中。

## Changes Overview / 变更概览

### [NEW] `src/utils/netease_crawler.py`
This is the core encapsulated module. It provides the `NetEaseCrawler` class with the following methods: / 这是核心封装模块。它提供具有以下方法的 `NetEaseCrawler` 类：
- **`get_playlist_tracks(url_or_id)`**: Fetches all songs in a playlist. / 获取歌单中的所有歌曲。
- **`get_song_lyrics(song_id)`**: Retrieves and parses lyrics. / 获取并解析歌词。
- **`download_audio(song_id, save_dir)`**: Downloads the MP3 file. / 下载 MP3 文件。

### [NEW] `__init__.py` files
Added to `src/` and `src/utils/` to ensure the project structure is recognized as a Python package. / 添加到 `src/` 和 `src/utils/` 中，以确保项目结构被视为 Python 包。

## How to Use / 如何使用

You can now use the crawler in any script like this: / 您现在可以像这样在任何脚本中使用该爬虫：

```python
from src.utils.netease_crawler import NetEaseCrawler

# Initialize (automatically loads cookie.txt if present)
# 初始化（如果存在 cookie.txt 则自动加载）
crawler = NetEaseCrawler()

# 1. Fetch playlist metadata / 获取歌单元数据
data = crawler.get_playlist_tracks("https://music.163.com/playlist?id=17684977343")

# 2. Extract first song / 提取第一首歌
song = data['tracks'][0]

# 3. Get lyrics / 获取歌词
lyrics = crawler.get_song_lyrics(song['id'])

# 4. Download audio / 下载音频
path = crawler.download_audio(song['id'])
```

## Verification Results / 验证结果

- **Module Integrity**: Verified that `NetEaseCrawler` can be imported and initialized. / **模块完整性**：经验证 `NetEaseCrawler` 可以成功导入并初始化。
- **Functional Check**: A dedicated test script `test_crawler_encapsulation.py` has been provided to run the full flow. / **功能检查**：已提供专门的测试脚本 `test_crawler_encapsulation.py` 来运行完整流程。

> [!TIP]
> Make sure to keep your `cookie.txt` in the project root for VIP or high-quality audio downloads. / 请务必在项目根目录中保留 `cookie.txt`，以便进行 VIP 或高质量音频下载。
