+++
title = "配置说明"
description = "全局设置、主播覆写、投稿参数和路径环境变量。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-02T08:00:00+00:00
draft = false
weight = 20
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "理解 WebUI、SQLite 和 YAML/TOML 配置之间的关系。"
toc = true
top = false
+++

## 配置来源

WebUI 保存的全局设置和任务位于 SQLite。YAML/TOML 主要用于首次导入和部署默认值：数据库已有主播任务时，不会被配置文件覆盖。

验证配置文件：

```shell
uv run biliup validate-config ./public/config.yaml
```

示例文件：

- `public/config.yaml`
- `public/config.toml`

平台专用的扩展字段会被保留。全局设置修改后对新启动的检测、录制和投稿任务生效；已经运行中的任务不会中途切换参数。

## 录制设置

| 字段 | 说明 |
| --- | --- |
| `downloader` | 当前统一使用 FFmpeg 录制 |
| `segment_time` | 单个分片最长时长，格式 `HH:MM:SS` |
| `file_size` | 单个分片大小上限，单位 Byte |
| `filtering_threshold` | 小于此值的分片视为无效并删除，单位 MiB |
| `filename_prefix` | 文件名模板，支持 `{streamer}`、`{title}` 和时间格式 |
| `delay` | 下播确认窗口，单位秒；窗口内恢复直播会刷新流地址并归入同一场录像 |
| `upload_delay` | 确认下播后、开始投稿前的额外等待时间，单位秒 |
| `checker_concurrency` | 最大同时直播状态检测请求数 |
| `recorder_stall_timeout` | FFmpeg 存活但录像文件持续不增长时，等待多少秒后触发恢复；`0` 表示关闭 |
| `recorder_retry_limit` | 短时间连续录制失败达到多少次后进入熔断冷却 |
| `recorder_retry_backoff` | 连续录制失败的指数退避起始秒数，单次最大 60 秒 |
| `pool1_size` | 最大同时录制任务数 |

同时设置 `segment_time` 和 `file_size` 时，先达到限制的一项结束当前分片。

## 投稿设置

| 字段 | 说明 |
| --- | --- |
| `submit_api` | 投稿提交接口，例如 `web`、`app`、`b-cut-android` |
| `lines` | UPOS 上传线路，`AUTO` 自动选择 |
| `threads` | 单个文件的并发分片上传数 |
| `pool2_size` | 最大同时投稿任务数 |

常用线路包括 `bda2`、`bldsa`、`qn`、`tx` 和 `txa`。线路效果与运营商和所在地区有关，应以实际上传日志为准。并发数不是越大越好，通常从 `3` 开始测试，部分线路会限制并发数。

手动重新上传会读取提交时的最新模板和全局设置。修改线路不会改变已经运行中的上传任务。

## 日志与历史

| 字段 | 说明 |
| --- | --- |
| `log_file_max_size_mb` | 单个 `biliup.log` 大小上限，轮转后保留 5 个备份 |
| `history_max_records` | 直播历史数据库记录数量上限 |

历史超出上限时删除最旧数据库记录，不删除磁盘录像。WebUI 中的手动清理同样不会删除录像。

## 主播覆写

每个主播可以在“配置覆写”中覆盖部分全局设置，例如分段、过滤阈值、B 站画质、CDN 和弹幕录制。优先级为：

```text
主播覆写 > WebUI 全局设置/数据库配置 > 程序默认值
```

覆写只影响该主播，不改变其他录制任务。

## 路径和服务环境变量

常用变量：

```text
BILIUP_HOME
BILIUP_DATABASE
BILIUP_DATA_DIR
BILIUP_CONFIG_DIR
BILIUP_DOWNLOAD_DIR
BILIUP_LOG_DIR
BILIUP_FRONTEND_DIR
BILIUP_HOST
BILIUP_PORT
BILIUP_CORS_ORIGIN
BILIUP_AUTH_ENABLED
BILIUP_LOG_LEVEL
```

所有相对目录都从 `BILIUP_HOME` 解析。完整示例见项目根目录 `.env.example`。
