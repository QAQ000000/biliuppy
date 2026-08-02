+++
title = "部署与升级"
description = "源码、Docker、数据目录和数据库迁移说明。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-02T08:00:00+00:00
draft = false
weight = 10
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "部署 biliup Python Edition，并安全沿用已有数据库。"
toc = true
top = false
+++

## 部署方式

长期运行推荐 Docker Compose；开发和本机调试推荐源码启动。两种方式使用相同的 FastAPI 服务和 SQLite 数据结构。

Docker：

```shell
docker compose up -d --build
```

源码：

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server --host 0.0.0.0 --port 19159
```

生产环境不要使用 `--reload` 或 `--no-auth`。

## 数据目录

所有可变数据都从 `BILIUP_HOME` 派生：

| 内容 | 默认路径 | 环境变量 |
| --- | --- | --- |
| SQLite 与账号文件 | `$BILIUP_HOME/data` | `BILIUP_DATA_DIR` |
| SQLite 数据库 | `$BILIUP_HOME/data/data.sqlite3` | `BILIUP_DATABASE` |
| 配置目录 | `$BILIUP_HOME/config` | `BILIUP_CONFIG_DIR` |
| 录像目录 | `$BILIUP_HOME/downloads` | `BILIUP_DOWNLOAD_DIR` |
| 日志目录 | `$BILIUP_HOME/logs` | `BILIUP_LOG_DIR` |
| 缓存目录 | `$BILIUP_HOME/cache` | `BILIUP_CACHE_DIR` |
| WebUI 静态文件 | 源码为 `out`，wheel 为内置目录 | `BILIUP_FRONTEND_DIR` |

源码目录启动时，`BILIUP_HOME` 默认是仓库根目录，因此会直接使用现有的 `data/data.sqlite3`。Docker Compose 将主机的 `./data` 挂载到容器 `/data`。

## 数据库升级

服务每次启动都会执行 Alembic 迁移。已有表和数据会原地升级，不需要导出后重新导入，也不应删除数据库重新开始。

升级前：

1. 停止服务，确保 SQLite 没有写入。
2. 备份整个 `BILIUP_HOME`，至少备份数据库和账号 JSON。
3. 更新代码或镜像。
4. 启动服务并检查健康状态、任务状态和日志。

Docker 更新：

```shell
docker compose down
docker compose build --pull
docker compose up -d
docker compose ps
```

源码更新：

```shell
uv sync --extra dev
npm ci
npm run build
uv run pytest -q
uv run biliup server
```

## 配置导入兼容

YAML/TOML 中的主播只在 SQLite 没有主播任务时首次导入。数据库中已有任务时，启动不会用配置文件覆盖它们。旧上传器名称 `biliup-rs` 和 `stream_gears` 会迁移为 Python `bili_web`。

## 健康检查

```shell
curl http://127.0.0.1:19159/healthz
```

正常返回：

```json
{"status":"ok"}
```

OpenAPI 文档位于 `/docs`。服务异常时先查看 `logs/biliup.log` 或 `docker compose logs biliup`。
