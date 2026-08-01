# biliup 1.1.7 Python Edition

biliup 是一个多平台直播检测、录制和 B站投稿服务。本分支的后端、调度、平台解析、登录和上传逻辑全部使用 Python，基于 FastAPI 与 SQLAlchemy。FFmpeg 是唯一必需的外部媒体工具；WebUI 继续使用 Next.js 静态导出。

> 本项目仅供个人学习研究，不保证稳定性。使用者应遵守直播平台条款、版权规定及当地法律，禁止商业用途。

## 运行要求

- Python 3.10 以上，推荐 Python 3.12
- [uv](https://docs.astral.sh/uv/)
- FFmpeg
- Node.js 20（仅构建 WebUI 时需要）

发布 wheel 已包含构建好的 WebUI，使用 wheel 部署时不需要安装 Node.js，也不需要单独复制前端目录。

## Docker 部署

Docker Compose 使用 `./data` 作为唯一持久化目录。数据库、配置、日志、登录文件和录像都位于该目录中。

```shell
docker compose up -d --build
```

打开 <http://localhost:19159>。首次运行时注册管理密码，后续数据保存在：

```text
data/
  data.sqlite3
  config/
  downloads/
  logs/
  cache/
```

## Windows 手动开发

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\dev.ps1
```

关闭认证进行本地调试：

```powershell
.\scripts\dev.ps1 -NoAuth
```

已经构建过前端时：

```powershell
.\scripts\dev.ps1 -SkipFrontend -NoAuth
```

## Linux/macOS 手动开发

```shell
sh scripts/dev.sh --no-auth
```

也可以逐步执行：

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server
```

默认监听 `0.0.0.0:19159`。查看完整参数：

```shell
uv run biliup server --help
```

## 配置

服务兼容 YAML 和 TOML。指定配置文件：

```shell
uv run biliup server --config ./config.yaml
uv run biliup validate-config ./config.toml
```

当 SQLite 中没有录制任务时，配置文件中的 `streamers` 会首次导入数据库；已有数据库任务不会被覆盖。平台专用的未知配置项会被保留。

常用环境变量见 [.env.example](./.env.example)：

```text
BILIUP_HOME
BILIUP_DATABASE
BILIUP_CONFIG_DIR
BILIUP_DOWNLOAD_DIR
BILIUP_LOG_DIR
BILIUP_FRONTEND_DIR
BILIUP_CORS_ORIGIN
BILIUP_AUTH_ENABLED
```

所有相对路径都根据 `BILIUP_HOME` 解析，不再依赖启动命令所在目录。在源码目录启动时会自动接管原有的 `data/data.sqlite3`，并由 Alembic 原地升级。

投稿失败会按配置重试。达到重试上限后，任务会记录错误并保留录像文件，不会执行默认的上传后删除操作，便于人工排查和重新投稿。

## 开发验证

```shell
uv run pytest -q
uv run ruff check biliup/api biliup/core biliup/database biliup/services tests
npm run build
```

工程边界和 AI 修改规则见 [ARCHITECTURE.md](./ARCHITECTURE.md) 与 [AGENTS.md](./AGENTS.md)。

## 主要接口

WebUI 继续使用兼容接口：

- `/v1/streamers`：录制任务管理
- `/v1/upload/streamers`：投稿模板管理
- `/v1/configuration`：运行配置
- `/v1/status`：调度状态
- `/v1/videos`：录像列表
- `/v1/get_qrcode`：B站二维码登录
- `/v1/ws/logs`：日志 WebSocket

API 文档在服务启动后访问 <http://localhost:19159/docs>。

## License

MIT，详见 [LICENSE](./LICENSE)。
