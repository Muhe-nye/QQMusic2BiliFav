# QQMusic2BiliFav

把 QQ 音乐歌单转换为 Bilibili 收藏夹视频（优先匹配 MV / 官方版本 / 歌曲视频）。

## 功能

- 拉取 QQ 音乐歌单（自动分页，读取全量歌曲）
- 在 B 站搜索候选视频
- 按规则打分：时长、歌名命中、歌手命中、MV/官方关键词、降权词（live/cover）
- 自动创建目标收藏夹（不存在时）
- 自动添加命中视频到收藏夹
- 输出 `sync_report.json` 供人工复核
- 支持两种同步逻辑：`incremental` 增量同步 / `copy` 全量复制
- 收藏夹优先匹配：先在目标收藏夹命中同曲，命中则跳过，不再全站搜索

## 环境变量（推荐）

推荐方式：使用本地环境文件，避免每次手动 `set`。

1. 复制模板：

```powershell
Copy-Item .\.env.example .\.env.local
```

2. 编辑 `.env.local` 填入你的 Cookie 值（不要提交到 git）。

需要的字段：

- `QQ_PLAYLIST_ID`（可选，设置后可不传 `--qq-playlist-id`）
- `BILI_SESSDATA`
- `BILI_JCT` (csrf)
- `BILI_DEDEUSERID`
- `BILI_BUVID3`（可选）

程序默认会自动按顺序读取：`.env.local` -> `.env`。
也可以手动指定：`--env-file .\my.env`。

## 运行

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID>
```

常用参数：

- `--folder-name "我的收藏夹名"`：覆盖默认收藏夹名（默认用 QQ 歌单名）
- `--sync-mode incremental`：同步模式（`incremental` 或 `copy`）
- `--env-file .\my.env`：指定环境变量文件
- `--dry-run`：只匹配不写入 B 站
- `--concurrency 3`：并发数
- `--request-interval 0.35`：请求最小间隔秒数（越大越慢，风控更少）
- `--retry-times 3`：遇到 412/429/5xx 等时自动重试次数
- `--retry-base-delay 1.0`：重试退避基础秒数（会指数增长）
- `--duration-tolerance 25`：时长容差（秒）
- `--score-threshold 52`：最低得分阈值
- `--max-songs 20`：只处理前 N 首（调试建议）
- `--retry-from .\sync_report.json`：指定补跑来源报告
- `--retry-mode both`：补跑类型，可选 `error` / `skipped` / `both`
- `--report sync_report.json`：报告输出路径

命名规则：

- 默认收藏夹名 = QQ 歌单名
- 若已存在同名收藏夹：直接复用该收藏夹
- 仅在“需要新建但原名冲突”时，自动尝试 `原名（歌单）`

示例（先试跑）：

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID> --dry-run --score-threshold 56
```

长期增量同步（推荐定时执行）：

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID> --sync-mode incremental --concurrency 1 --request-interval 1.2 --report incremental_report.json
```

说明：`incremental` 不依赖本地状态文件，每次都会扫描 QQ 歌单和 B 站收藏夹。每首歌先在收藏夹内做同曲匹配，命中即跳过；未命中才会全站搜索并尝试添加。

全量复制（会遍历整张 QQ 歌单）：

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID> --sync-mode copy --report copy_report.json
```

只补 error（慢速 + 重试）：

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID> --retry-from .\sync_report.json --retry-mode error --concurrency 1 --request-interval 1.2 --retry-times 5 --retry-base-delay 1.5 --report retry_error_report.json
```

同时补 error + skipped（默认 `both`）：

```powershell
uv run python .\main.py --qq-playlist-id <QQ_PLAYLIST_ID> --retry-from .\sync_report.json --retry-mode both --concurrency 1 --request-interval 1.2 --retry-times 5 --retry-base-delay 1.5 --report retry_both_report.json
```

## 匹配策略说明

优先搜索词：

1. `歌手 + 歌名 + MV`
2. `歌手 + 歌名 + 官方`
3. `歌手 + 歌名`

打分核心：

- 时长越接近分越高
- 标题命中歌名、歌手加分
- 标题含 `MV/官方/official/vevo` 加分
- 标题含 `live/cover/翻唱/dj` 降权
- 播放量较高适度加分

## 注意

- B 站接口策略会变动，若出现 412/风控错误，需要降低频率、切换网络或更新请求头。
- 建议先 `--dry-run` 看报告再执行真实写入。

## 项目结构

- `main.py`：CLI 入口与任务编排
- `source/config.py`：环境变量与报告过滤工具
- `source/engine.py`：同步引擎
- `source/providers/qq_music.py`：QQ 音乐数据提供方
- `source/platforms/bilibili.py`：B 站搜索/收藏夹/重试与限速逻辑
- `source/models.py`：数据模型
