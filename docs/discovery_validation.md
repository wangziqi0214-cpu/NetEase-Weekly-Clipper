# 多平台新歌采集验证说明

## 平台采集机制与数据源说明

1. **QQ 音乐 (QQMusicCollector)**:
   - 接口：`https://u.y.qq.com/cgi-bin/musicu.fcg` (官方接口)
   - 覆盖：内地 (area=1) 与港台 (area=2) 新碟
   - 曲目列表获取：优先采用 `music.musichallAlbum.AlbumSongList.GetAlbumSongList`，兼顾 `album.AlbumSongList.GetAlbumSongList`。

2. **网易云音乐 (NetEaseCollector)**:
   - 接口：本地 `NeteaseCloudMusicApiEnhanced` 服务的 `/album/new?area=ZH` 与 `/album?id=...`。
   - 窗口：默认拉取 15 页滑动窗口（offset 0~280，覆盖 300 张专辑），跨页自动去重与早停。
   - 容灾：专辑详情针对瞬态 405/网络错误执行 3 次带防缓存时间戳 `_t` 的重试，重试耗尽仅跳过故障专辑并记录警告。

3. **KKBOX (KKBOXCollector & Chrome 扩展桥接)**:
   - **官方 Open API 通道**：配置 `KKBOX_CLIENT_ID` / `KKBOX_CLIENT_SECRET` 时直接通过官方 OAuth2 接口采集。
   - **Chrome 扩展 + 本地桥接通道**：未配置官方凭证时，系统如实标记为待扩展桥接（`awaiting_extension_bridge`），通过 `chrome_extension/kkbox_collector` 与本地 `127.0.0.1:8765/ingest/kkbox` 完成无密钥采集。
   - **注意**：公开榜单（如 KMA 日榜）仅能反映上榜热门曲目，不能代替全量新碟/新歌发布列表。

## 物理选曲规则

- **Single 单曲发行**：无论曲目数量（例如含伴奏带 2 首），始终选取**物理顺序第 1 首原曲**。
- **Album / 多曲 EP 发行（>=2 首）**：选取**物理顺序第 2 首**。
- **单曲目发行（==1 首）**：选取唯一曲目。
