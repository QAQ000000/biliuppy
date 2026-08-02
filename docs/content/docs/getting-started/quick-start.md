+++
title = "快速开始"
description = "启动纯 Python 服务并完成第一次录制投稿配置。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-02T08:00:00+00:00
draft = false
weight = 20
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "从源码或 Docker 启动 biliup。"
toc = true
top = false
+++

## 环境要求

- Python 3.10 以上，推荐 3.12
- uv
- FFmpeg
- Node.js 20，仅构建 WebUI 时需要

确认 FFmpeg 可用：

```shell
ffmpeg -version
```

## Windows 源码启动

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\dev.ps1
```

已经构建过 WebUI 时可以跳过前端构建：

```powershell
.\scripts\dev.ps1 -SkipFrontend
```

只在可信任的本机调试环境中关闭认证：

```powershell
.\scripts\dev.ps1 -SkipFrontend -NoAuth
```

## Linux 或 macOS 源码启动

```shell
sh scripts/dev.sh
```

也可以逐步运行：

```shell
uv sync --extra dev
npm ci
npm run build
uv run biliup server
```

默认地址为 <http://localhost:19159>。自定义端口和数据根目录：

```shell
uv run biliup server --host 127.0.0.1 --port 19160 --home ./runtime
```

## Docker

```shell
docker compose up -d --build
docker compose ps
```

打开 <http://localhost:19159>。主机的 `./data` 保存数据库、账号、录像、日志和缓存。

## 第一次配置

1. 首次启用认证时，在登录页注册管理账号。
2. 打开“投稿管理”，进入用户管理并扫码登录 B 站。
3. 新建投稿模板，设置分区、标题、标签和账号。
4. 打开“录播管理”，新增主播并关联投稿模板。
5. 在“任务平台”确认主播状态正常。

没有关联投稿模板时只录制，不会自动投稿。可以在投稿管理中选择已有录像进行手动上传。

## 配置文件

YAML 和 TOML 均受支持：

```shell
uv run biliup validate-config ./public/config.yaml
uv run biliup server --config ./public/config.yaml
```

配置文件中的主播只在数据库没有录制任务时首次导入，不会覆盖 WebUI 中已有任务。
