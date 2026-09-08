# 本周交付与艺人资料库实施说明

## 目标

本次改动把“本周交付”当作一个可持续编辑的工作集合，并为每位艺人建立本地资料卡。歌曲审核状态、网易云匹配结果和艺人资料彼此独立：移除歌曲不会删除候选或人工审核历史，资料缺失也不会影响歌曲进入人工审核。

## 本周交付的编辑规则

1. 交付预览先读取当前周的歌单顺序和当前周的移除记录。
2. 自动匹配、人工匹配和其他已解析歌曲都显示“移出本周”操作。
3. 操作写入 `delivery_exclusions`，键为“歌单名称 + candidate_id”，因此只影响本周，不改变候选的 `review_status`、人工备注或匹配覆盖。
4. 移除时同步从已保存顺序中删除；重新加载预览、重新保存排序、导出和发布都会应用移除记录。
5. 已经实际加入网易云歌单的歌曲不能再移除，防止本地状态与远端歌单不一致。
6. 未发布歌曲可以恢复，恢复后回到候选集合，仍需重新确认顺序。

## 艺人资料库

### 数据

SQLite 的 `artist_knowledge` 表以规范化艺人名为主键，保存：

- `factual_summary`：事实摘要；
- `sources`：经校验的 HTTP/HTTPS 来源链接；
- `uncertainty`：`low`、`medium` 或 `high`；
- `identity_context`：艺人类型、地区、风格、成员、活动年份和代表作品；
- `status`：`pending`、`completed`、`sparse` 或 `failed`；
- 搜索词、错误信息和创建/更新时间。

### 搜索流程与高召回机制

1. **多平台检索提示词**：后台任务调用 `agy --model gemini-3.8-flash-high --output-format json --dangerously-skip-permissions`，明确跨 Google 搜索、主流流媒体/社交主页（Spotify、Instagram、YouTube、Bandcamp）、百科/垂直乐评平台（Wikipedia、StreetVoice 街声、豆瓣音乐）以及媒体专访/音乐节阵容进行多来源核验。
2. **名称变体生成**：自动拓展艺人名大小写、空格/连字符（如 `schoolgirl byebye`、`Schoolgirl Byebye`、`Schoolgirl-Byebye`、`Schoolgirl Bye Bye`）及中英文双语别名变体，并在数据库层面支持大小写不敏感与宽松分隔符匹配。
3. **两阶段召回**：先进行艺人主体多来源检索；仅当公开信息确实稀少时，才结合“艺人 + 歌名 + 专辑名”进行第二阶段发行上下文深入补充。
4. **失败与无资料严格区分**：网络超时、命令异常或结构化解析失败直接置为 `failed` 并记录 `error`，绝不把检索失败当成“艺人无资料/小众发行”；严禁编造来源 URL，摘要明确评估置信度（low/medium/high）。
5. **来源清洗与标签呈现**：支持字符串、Markdown 链接与字典格式来源，过滤占位/虚假域名并去除末尾标点符号，前端展示平台专属徽标。

### 运行约束、自动同步与重试恢复

- 默认最多两个后台 worker，单个任务超时为 600 秒；
- 同一艺人同时只允许一个任务；精准维护内存队列及活跃任务集合，杜绝重复排队和静默丢弃；
- 子进程调用强制指定 UTF-8 编码环境，保障多语言/非 ASCII 艺人检索顺畅执行；
- 在 macOS LaunchAgent 或其他 ASCII/空 locale 环境中，后台 AGy 调用不会把中文提示词直接塞进 Python `argv`；系统会把 prompt 写入 UTF-8 临时文件，再用 ASCII-only wrapper 启动 `agy --model gemini-3.8-flash-high`，同时以 UTF-8 replacement 策略读取 stdout/stderr，避免中文艺人名触发 ASCII codec encode/decode 崩溃；
- **全链路自动异步收集**：
  - **新歌入库自动触发**：榜单抓取、新碟抓取及 KKBOX 扩展流在候选写入/更新时，自动解构多艺人并异步加入待检索队列，不因是否是新歌而漏排历史失败或未完成记录；
  - **守护进程周期补偿**：Supervisor 守护进程在启动时以及每次抓取周期结束时，自动扫描未收录或上次失败（`failed`）的艺人进行自动入队补偿；
  - **人审台启动预热**：人审台（`review_api`）启动时，自动在后台对待审核（`pending`）与已通过（`approved`）候选中的未收录艺人进行预热排队；
  - **CLI 全量同步与回填**：提供 `sync-artist-knowledge`（别名 `backfill-artist-knowledge`）命令，支持 `--status active|pending|approved|all`、`--limit`、`--concurrency`、`--force` 及 `--artist "NAME"`，支持幂等且可重复执行；
- 已有 `completed` 或 `sparse` 资料默认不重复检索，支持显式强制刷新（`--force` 或界面按钮）；
- 网络超时、编码错误或结构化解析失败置为 `failed`，在下次调度周期或启动补偿时会自动参与重试。若本机守护服务已经运行，修复部署后可重启 LaunchAgent，或等待下一轮 `DiscoverySupervisor.enqueue_missing_artist_knowledge()` 扫描；它会重新排队 `failed` 与中断的 `pending` 记录，无需手工修改生产数据库。

### 本地画像第二层筛选

候选入库/更新后会立即执行一次只读本地 SQLite 的 `artist_knowledge` 筛选；艺人资料写入或更新后，也会对该艺人未人工处理过的候选自动补筛。这个步骤不联网、不调用 AGy、不触发资料采集，只消费已经落库的资料。

筛选规则保持高召回：

- 找不到资料、资料仍为 `pending`/`collecting`、采集 `failed`、`sparse`、无来源或 `uncertainty=high` 时，候选保留 `pending`，只追加“资料未完成或稀疏；不作为排除依据”的解释；
- 只有 `completed`、有来源、非高不确定资料明确显示艺人为 DJ、制作人、普通/主流流行、偶像、说唱、OST、古典/纯音乐等非目标类型，并且没有摇滚、乐队、独立、朋克、金属、自赏、后摇等目标证据时，才把未人工处理过的 `pending` 候选移入可恢复的 `machine_filtered`；
- 人工通过、否决、暂缓或恢复过的候选不会被后续画像补筛再次自动覆盖；
- 画像结论写入候选 `relevance_reasons`，前缀为 `本地画像筛选:`，自动机筛备注前缀为 `本地画像机筛:`，审核表和 `/api/candidates` 的 `screening_reasons`、`local_profile_screening` 都能看到原因。

### 页面行为

审核表和本周交付表中的艺人名称显示为可悬停信息。
- **严格只读本地**：悬停时严格只读本地 SQLite，绝不发起实时联网搜索或同步阻塞；
- **自动轮询呈现**：当本地资料库尚未收录但后台正在异步排队检索时，悬停卡片提示“正在后台异步检索中...”，并在悬停期间以 2.5 秒轻量轮询本地接口（鼠标移出立即停止轮询），检索就绪后无需刷新页面即可展示；
- **兜底手动按钮**：保留显式的“后台收集/重新检索”按钮作为应急兜底；
- **资料呈现**：已有资料展示摘要、类型、风格、来源（带平台标徽与直链）和更新时间；检索失败显示失败原因；小众独立音乐人显示“资料稀疏”并提示不作为排除理由。

## 接口约定

- `DELETE /api/publication/delivery/items/{candidate_id}`：移出当前周交付；
- `POST /api/publication/delivery/items/{candidate_id}/restore`：恢复当前周交付；
- `GET /api/artist-knowledge?artist=...`：严格只读本地资料，默认 `auto_collect=false`；未命中且未在队列时返回 `local_missing=true`；若后台正在收集则返回 `status: "collecting"`；
- `POST /api/artist-knowledge/collect`：手动排队单个艺人（应急兜底）；
- `POST /api/artist-knowledge/batch`：默认批量只读；后台回填时显式传 `auto_collect=true`。

## 验证清单

- 数据库初始化后自动创建新表和索引；
- 交付预览过滤移除项，恢复后重新出现；
- 已发布歌曲拒绝移除；
- 顺序保存继续要求候选不重复且完整；
- 艺人资料解析、来源清理、稀疏资料和超时路径可测试；
- Python 测试通过，React/TypeScript 构建通过；
- 不重启正在运行的视频任务，不自动发布歌单。
