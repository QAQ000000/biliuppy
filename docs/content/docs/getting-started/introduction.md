+++
title = "项目说明"
description = "biliup Python Edition 的能力、架构和使用入口。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-02T08:00:00+00:00
draft = false
weight = 10
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "多平台直播检测、FFmpeg 录制、WebUI 管理和 B 站自动投稿服务。"
toc = true
top = false
+++

## 组成

- FastAPI 提供 Web API、登录会话和静态 WebUI。
- SQLAlchemy 与 Alembic 管理 SQLite 数据和升级。
- Python 平台插件完成开播检测与直播流解析。
- FFmpeg 负责低开销录制和视频分段。
- Python 投稿器完成扫码登录、上传和投稿提交。
- Next.js WebUI 管理主播、模板、录像、任务、历史和日志。

服务不需要 Rust 运行时。发布 wheel 已包含构建好的 WebUI；只有从源码重新构建前端时才需要 Node.js。

## 工作流程

```text
检测开播 -> 获取直播流 -> FFmpeg 录制/分段 -> 历史入库
                                              -> B 站上传 -> 投稿提交 -> 后处理
```

投稿失败时会记录错误并保留录像文件，不执行默认删除操作。修改投稿模板中的线路或并发数后重新发起手动上传，新任务会读取更新后的配置。

## 从哪里开始

- [快速开始](../quick-start/)：第一次启动并完成录制投稿配置。
- [部署与升级](../../guide/introduction/)：Docker、源码运行、数据目录和数据库迁移。
- [配置说明](../../guide/configuration/)：全局设置、主播覆写和上传线路。
- [日常运维](../../guide/operations/)：日志、历史、备份和故障排查。
- [常见问题](../../help/faq/)：登录、录制与投稿问题。
