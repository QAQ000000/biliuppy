+++
title = "Introduction"
description = "biliup 纯 Python 服务安装与运行说明"
date = 2026-08-01T08:00:00+00:00
draft = false
weight = 10
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "多平台直播录制、任务管理和 B站投稿服务。"
toc = true
top = false
+++

## 架构

biliup 使用 FastAPI 提供 Web API 和静态 WebUI，SQLAlchemy/Alembic 管理 SQLite，Python 平台插件解析直播流，FFmpeg 完成录制，纯 Python 投稿器完成登录和上传。

## 数据目录

所有运行数据从 `BILIUP_HOME` 派生，包括 `data`、`config`、`downloads`、`logs` 和 `cache`。源码启动会自动接管原有的 `data/data.sqlite3`，不再依赖当前工作目录。

## 运行

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server
```

完整的部署、配置和开发命令见项目根目录 `README.md`、`ARCHITECTURE.md` 和 `AGENTS.md`。
