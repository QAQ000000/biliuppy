# biliup 1.1.7 Python Edition

biliup 是一个多平台直播检测、录制和 B 站投稿服务。后端、调度、平台解析、登录和上传逻辑全部使用 Python，基于 FastAPI、SQLAlchemy 和 Alembic；FFmpeg 负责媒体录制，Next.js 静态导出提供 WebUI。

> 本项目仅供个人学习研究，不保证稳定性。使用者应遵守直播平台条款、版权规定及当地法律，禁止商业用途。

## 当前能力

- 自动检测开播和下播，按时间或文件大小分段录制
- Bilibili、斗鱼、虎牙、抖音、Twitch、YouTube 等平台解析
- WebUI 管理主播、投稿模板、录像、任务、历史和日志
- B 站扫码登录、手动投稿和下播后自动投稿
- UPOS 上传线路切换、分片并发、失败重试和进度日志
- SQLite 数据库自动迁移，兼容已有任务和历史数据
- 日志轮转、分类查看、大小上限和清理
- 直播历史数量上限、分页和清理，清理历史不会删除录像文件

## 运行要求

- Python 3.10 以上，推荐 Python 3.12
- [uv](https://docs.astral.sh/uv/)
- FFmpeg，并确保 `ffmpeg` 可以从 `PATH` 运行
- Node.js 20，仅源码构建 WebUI 时需要

发布 wheel 已包含 WebUI，不需要 Node.js，也不需要单独复制前端目录。

## 源码启动

Windows：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\dev.ps1
```

Linux 或 macOS：

```shell
sh scripts/dev.sh
```

脚本会安装依赖、构建 WebUI，并以热重载模式启动。只在可信任的本机环境调试时可以关闭认证：

```powershell
.\scripts\dev.ps1 -NoAuth
```

也可以逐步运行：

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server --host 0.0.0.0 --port 19159
```

默认地址为 <http://localhost:19159>。首次启用认证时，在登录页注册管理账号。完整参数：

```shell
uv run biliup server --help
```

## Docker 部署

```shell
docker compose up -d --build
```

打开 <http://localhost:19159>。Compose 将主机的 `./data` 挂载为容器的 `/data`，所有运行数据都可持久化：

```text
data/
  data.sqlite3       # SQLite 数据库
  config/            # 生成或导入的配置
  downloads/         # 录像文件
  logs/              # 程序日志
  cache/             # 临时缓存和直播封面
  *.json             # 扫码登录生成的 B 站账号文件
```

查看运行状态和日志：

```shell
docker compose ps
docker compose logs -f biliup
```

## 数据与升级

源码运行时，`BILIUP_HOME` 默认是项目根目录，当前数据库位于 `data/data.sqlite3`。指定 `--home` 后，所有相对路径都从该目录解析：

```powershell
uv run biliup server --home C:\biliup-data --port 19160
```

启动时 Alembic 会自动升级数据库，不会重建已有数据库。升级前建议停止服务并备份整个运行目录，至少备份 SQLite 数据库和账号 JSON 文件。

Docker 升级：

```shell
docker compose down
docker compose build --pull
docker compose up -d
```

源码升级后重新执行：

```shell
uv sync --extra dev
npm ci
npm run build
uv run pytest -q
```

不要删除旧数据库来解决迁移问题。若启动失败，应先保留现场并查看 `logs/biliup.log`。

## 基本工作流

1. 在 WebUI 的投稿管理中扫码登录 B 站账号。
2. 创建投稿模板，设置分区、标题、标签、上传线路和并发数。
3. 创建录制任务，并关联投稿模板。
4. 开播后状态进入 `Downloading`，下播后完成分段和历史入库。
5. 有投稿模板时进入上传；投稿失败会保留录像，便于手动重试。

手动上传时，WebUI 会读取提交瞬间的投稿模板。修改 `lines` 或 `threads` 后重新上传会使用新配置，不影响已经运行中的上传任务。

## 配置

服务兼容 YAML 和 TOML：

```shell
uv run biliup validate-config ./public/config.yaml
uv run biliup server --config ./public/config.yaml
```

当数据库中没有录制任务时，配置文件中的 `streamers` 会首次导入；已有 WebUI 任务不会被覆盖。常用配置包括：

| 配置 | 作用 |
| --- | --- |
| `segment_time` / `file_size` | 录像分段时长和大小上限 |
| `filtering_threshold` | 删除过小的无效录像分片 |
| `delay` | 下播后二次确认时间 |
| `lines` / `threads` | B 站上传线路和单文件并发数 |
| `log_file_max_size_mb` | 单个日志文件大小上限，保留 5 个轮转备份 |
| `history_max_records` | 直播历史数据库记录上限 |

完整示例见 [public/config.yaml](./public/config.yaml) 和 [public/config.toml](./public/config.toml)。环境变量见 [.env.example](./.env.example)。

## 日志与历史

- WebUI 实时日志按程序、录制和上传分类展示。
- 全局设置可调整日志文件大小上限，也可清空当前日志和轮转备份。
- 直播历史支持分页、数量上限和手动清理。
- 清理直播历史只删除数据库记录，不删除磁盘录像。

日志中会隐藏 Cookie、访问令牌、上传签名等敏感字段，但对外提交日志前仍应人工检查。

## 开发验证

```shell
uv run pytest -q
uv run ruff check biliup/api biliup/core biliup/database biliup/services tests
npm run lint
npm run build
```

工程边界和 AI 修改规则见 [ARCHITECTURE.md](./ARCHITECTURE.md) 与 [AGENTS.md](./AGENTS.md)。

## 主要接口

- `/v1/streamers`：录制任务管理
- `/v1/upload/streamers`：投稿模板管理
- `/v1/uploads`：手动投稿和上传任务
- `/v1/configuration`：运行配置
- `/v1/status`：调度状态
- `/v1/videos`：录像列表
- `/v1/streamer-info`：直播历史
- `/v1/get_qrcode`：B 站二维码登录
- `/v1/logs`、`/v1/ws/logs`：日志读取与实时推送

服务启动后可访问 <http://localhost:19159/docs> 查看 OpenAPI 文档。

## License

MIT，详见 [LICENSE](./LICENSE)。
