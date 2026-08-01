+++
title = "Quick Start"
description = "使用纯 Python 服务启动 biliup。"
date = 2026-08-01T08:20:00+00:00
draft = false
weight = 20
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "使用纯 Python 服务启动 biliup。"
toc = true
top = false
+++

## 环境要求

- Python 3.10 以上，推荐 3.12
- uv
- FFmpeg
- Node.js 20（构建 WebUI）

## 源码启动

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server
```

Windows 可以运行 `scripts/dev.ps1`。服务默认位于 `http://localhost:19159`。

## Docker

```shell
docker compose up -d --build
```

Compose 将 `./data` 挂载为唯一持久化目录。

## 配置文件

YAML 和 TOML 均受支持：

```shell
uv run biliup validate-config ./config.yaml
uv run biliup server --config ./config.yaml
```

配置文件中的主播只会在数据库为空时首次导入，不覆盖 WebUI 中已有任务。
