# KKBOX Web Player Chrome 扩展采集与本地桥接使用指南

## 1. 架构定位与稳定性设计

- **官方 Open API（第一优先级）**：配置 `KKBOX_CLIENT_ID` / `KKBOX_CLIENT_SECRET` 时直接走官方接口。
- **Web Player Chrome 扩展（无凭证全量新歌采集通道）**：
  - 基于真实官方 Web Player SPA 路由（`https://play.kkbox.com/discover/new-releases` 与 `/discover/categories`）。
  - 利用用户真实的 Chrome 登录会话与台湾/香港节点上下文，实现完整的港台新碟/新发单曲采集。
  - 通过本地标准库 HTTP 桥接（`127.0.0.1:8765/ingest/kkbox`）安全同步至 SQLite 发现库中。
  - **绝不读取或传输 Cookie、密码、Token、localStorage 或 sessionStorage**。

### 生产级稳定性与防休眠（Crash/Suspension Resilient）机制
1. **Offscreen Document 保活（Keepalive）**：
   - 采集开始时自动创建 `chrome.offscreen` 文档，建立 Runtime Port 双向心跳连接，防止 Chrome MV3 Service Worker 在多批次抓取过程中被浏览器意外挂起/终止；任务结束后自动销毁回收。
2. **检查点与断点续传（Checkpoint Persistence）**：
   - 任务状态（`job_id`, `stage`, `pending_listing_urls`, `processed_listing_urls`, `pending_album_urls`, `processed_album_urls`, `failed_album_urls`, `retry_album_urls`, `cumulative_ingested`）实时持久化在 `chrome.storage.local`；
   - 若遇浏览器崩溃、休眠或再次触发，自动从上次检查点无缝恢复，绝不丢弃已发现未处理的专辑队列。
3. **小批次增量入库（Incremental Batch Ingestion）**：
   - 绝不在内存中积压数百张唱片；每批次提取 10 张专辑（并发度 2），解析完毕立即向本地桥接执行 POST 入库（底层 SQLite 幂等去重），入库成功后更新检查点。
4. **离线与受限暂停态（Pausable States）**：
   - 桥接服务离线时自动切换为 `paused_bridge_offline`，保留队列进度，桥接恢复后可一键继续；
   - 鉴权缺失或地区受限时自动暂存并设置 24 小时重试定时器。

---

## 2. 采集技术机制

1. **真实 SPA 路由发现**：
   - 主根路径：`https://play.kkbox.com/discover/new-releases`（自动获取 `available_types` 导航下的全部子分类路由，如 `/discover/new-releases/:type` 与 `/discover/new-releases/:type/:id`）。
   - 分类根路径：`https://play.kkbox.com/discover/categories`（匹配「華語」、「粵語」、「獨立」、「搖滾」等高召回中文关键词，发现 `/discover/categories/:categoryId/new-releases` 页面）。
   - **绝不访问排行榜**：严格过滤排斥任何 `/charts`、`/ranking`、`kma.kkbox.com` 等榜单链接。
2. **DOM 渐进式沉降（Settling & InfiniteLoader）**：
   - 针对前端 `InfiniteLoader` 列表，执行渐进式平滑滚动，直到连续多次没有新专辑生成或达到上限为止；
   - 语义化触发可见的「載入更多」按钮。
3. **安全限额与低并发**：
   - 专辑链接去重聚合，默认上限提升至 500 张；
   - 详情解析采用低并发（2 个后台非激活标签页），包含 30 秒超时控制与自动单次重试；
4. **保守元数据与类型推导**：
   - 仅 1 首曲目推导为 `single`，含明确 Single/單曲或 EP 标签推导为相应类型，其余为 `album`；
   - 发行日期缺失时保持空字符串 `""`，**绝不伪造为当天日期**。

---

## 3. 一次性安装与日常运行步骤

### 步骤 1：启动本地 Python 桥接服务
```bash
python3 -m song_discovery.cli bridge --port 8765 --db-path output/discovery.db
```

### 步骤 2：在 Chrome 中加载扩展
1. 打开 Chrome，访问 `chrome://extensions/` 开启右上角「开发者模式」。
2. 点击「加载已解压的扩展程序」，选择项目中的 `chrome_extension/kkbox_collector` 目录。

### 步骤 3：准备浏览器运行环境（一次性）
1. 开启代理软件的 **台湾 (TW)** 或 **香港 (HK)** 节点；
2. 在 Chrome 中打开 [play.kkbox.com](https://play.kkbox.com) 并完成正常登录。

### 步骤 4：运行与监控
- 扩展将基于 `chrome.alarms` 每 5 天在后台静默自动采集；
- 点击扩展弹窗可查看：**已解析专辑、队列待处理、已入库曲目、重试失败、断点续传次数**；
- 若需立即运行或从断点恢复，点击 **「立即开始 / 恢复采集」**。
