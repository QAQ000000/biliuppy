+++
title = "日常运维"
description = "状态检查、手动上传、日志、历史、备份和故障处理。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-09T08:00:00+00:00
draft = false
weight = 30
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "保持录制与投稿服务长期稳定运行。"
toc = true
top = false
+++

## 服务实例

同一 `BILIUP_HOME` 只能运行一个 `biliup server` 进程。不要配置多个 Uvicorn/Gunicorn worker，也不要让多套 systemd 服务共用同一 HOME，否则会重复检测、录制和投稿。

服务会使用 `$BILIUP_HOME/.biliup.lock` 阻止第二个实例启动。锁文件会长期存在，是否被占用由操作系统文件锁决定，不要通过删除锁文件强制启动第二个实例。

Linux 生产环境可以使用单个 systemd 服务。以下路径、用户和端口应按实际部署修改：

```ini
[Unit]
Description=biliuppy recording service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=biliup
Group=biliup
WorkingDirectory=/opt/biliuppy
Environment=BILIUP_HOME=/var/lib/biliuppy
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" /opt/biliuppy/.venv/bin/biliup server --host 0.0.0.0 --port 19159 --no-access-log
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
KillMode=control-group
StandardOutput=journal
StandardError=journal
SyslogIdentifier=biliuppy

[Install]
WantedBy=multi-user.target
```

保存为 `/etc/systemd/system/biliuppy.service` 后运行：

```shell
sudo systemctl daemon-reload
sudo systemctl enable --now biliuppy
sudo systemctl status biliuppy
journalctl -u biliuppy -f
```

浏览器高速上传器 `bili_browser` 使用 Playwright 的可见浏览器模式，以便使用创作中心的新版分片上传协议。Linux 服务器不需要安装桌面环境，但需要安装虚拟显示和浏览器（使用运行服务的同一个用户）：

Windows 首次安装 Chromium：

```powershell
uv run playwright install chromium
```

Linux 安装 Chromium 和虚拟显示：

```shell
sudo apt install -y xauth xvfb
uv sync
uv run playwright install --with-deps chromium
```

必须用实际运行 biliuppy 服务的系统账号执行 Playwright 安装，否则 systemd 启动后可能找不到 Chromium。systemd 示例中的 `xvfb-run` 会为 Chromium 提供虚拟屏幕；没有选择 `bili_browser` 的任务不会启动浏览器。Docker 镜像已经包含 Xvfb、Chromium，并预留 1 GB `/dev/shm`。同一 B 站账号的浏览器任务会串行执行，避免多个上传会话触发 CDN `400 InvalidArgument`。

浏览器模式的完整链路是：Playwright 上传全部分 P，程序捕获每个分 P 的 `filename` 和 `biz_id/cid`，按选择顺序构造 `videos[]`，再使用当前浏览器 Cookie 调用 Python Web 投稿 API 填写并提交稿件。只有 API 返回明确拒绝时才回退到页面提交。API 超时、连接中断或没有返回 `aid/bvid` 时属于“结果未知”，系统不会自动再次投稿；应先在创作中心确认是否已经生成稿件。

创作中心偶尔出现的“批量操作”可选浮层会被自动取消。不要通过 F12 删除浮层 DOM，这会改变页面状态并使自动化选择器失效。

systemd 默认按宿主机 journald 策略保存 stdout/stderr。可在 `/etc/systemd/journald.conf` 中设置 `SystemMaxUse`、`RuntimeMaxUse` 或 `MaxRetentionSec`，修改后重启 `systemd-journald`。这些限制作用于整个 journal，不是单个 biliuppy 服务。

## 任务状态

常见录制状态：

| 状态 | 含义 |
| --- | --- |
| `Pending` / `Checking` | 等待或正在检查直播状态 |
| `Idle` | 当前未开播 |
| `Waiting` | 已触发录制，等待资源 |
| `Downloading` | FFmpeg 正在录制 |
| `Recovering` | 录制流中断，正在刷新直播状态和流地址 |
| `ConfirmingOffline` | 已收到明确下播结果，仍处于下播确认宽限期 |
| `Degraded` | 平台检测暂不可用，或连续录制失败后正在熔断冷却 |
| `Paused` | 用户暂停 |
| `Error` | 检测、录制或处理发生错误 |

投稿状态单独显示。上传失败时录像保留在下载目录，可在投稿管理中手动重新上传。

`delay` 是录制中断后的下播确认宽限期，默认 60 秒。确认期间会进行至少 3 次明确下播检测；恢复开播会刷新流地址并继续同一场录制，超时、限流、接口异常等 `UNKNOWN` 结果不会计为下播。`upload_delay` 才是确认下播后、开始自动投稿前的等待时间。

FFmpeg 存活但录像文件在 `recorder_stall_timeout` 秒内没有增长时，也会进入恢复流程。连续失败使用指数退避；达到 `recorder_retry_limit` 后进入至少 5 分钟的熔断冷却。熔断期间不会把仍在直播的场次送去投稿。

`min_free_disk_gb` 控制下载目录所在磁盘必须保留的空间，默认 5 GB。空间不足时不会启动新录制；录制过程中低于阈值时会停止 FFmpeg、保留已有录像并显示 `Degraded`。设为 `0` 可关闭保护。

## 手动上传和线路切换

1. 在全局设置或投稿模板中修改 `lines`、`threads`。
2. 保存配置。
3. 在投稿管理中重新选择录像并上传。

新任务使用保存后的配置，正在上传的任务继续使用启动时的配置。`lines` 和 `threads` 只控制 `bili_web` 的 UPOS 上传；`bili_browser` 使用创作中心浏览器协议，这两个参数不会改变其线路或速度。上传日志会记录协议、线路、分片数量、进度、平均速度和投稿结果。

选择文件和投稿模板后，WebUI 会在真正创建任务前显示投稿预览，包括展开变量后的标题、简介、直播间信息、元数据来源及分 P 列表。元数据优先从直播历史读取；未关联历史的录像会尝试从主播和录像文件名恢复；仍无法识别时使用文件名、修改时间等文件属性。多 P 按用户选择顺序上传和提交，不按文件名或完成时间重新排序。

手动投稿最多选择 100 个文件，`threads` 范围为 1～8。活动任务总数由 `manual_upload_queue_limit` 控制；相同账号、文件和参数的活动任务会复用原任务 ID，避免重复点击造成重复投稿。

最近 100 条手动上传任务状态保存在 SQLite 中，服务重启后仍可查询。重启时尚未完成的任务会标记为 `Cancelled`，系统不会自动重复投稿。任务显示“结果未知”或在最终提交阶段异常时，也应先在创作中心确认稿件状态，再决定是否重新上传。

自动投稿成功后，默认删除操作会等待 B 站审核状态确认。审核通过后删除录像和同名 XML；审核失败、查询异常或 24 小时内无法确认时保留源文件。待审核任务保存在 SQLite 中，服务重启后会继续检查。

## 日志管理

WebUI 的实时日志分为程序、录制和上传类别。日志文件位于 `BILIUP_LOG_DIR`，默认为 `$BILIUP_HOME/logs/biliup.log`。

- `log_file_max_size_mb` 控制单个日志文件上限。
- 达到上限后轮转，保留最近 5 个备份。
- “清理日志”会清空当前日志并删除轮转备份。
- Cookie、访问令牌和上传签名会自动脱敏。

清理日志不可恢复。需要排查故障时应先下载或备份日志。

生产环境有三条相互独立的日志链路：

- 应用日志写入 `biliup.log`，由 `log_file_max_size_mb` 和 5 个备份控制。
- Uvicorn access log 默认关闭；使用 `--access-log` 启用后写入 stdout，不进入 `biliup.log`。
- 进程 stdout/stderr 由运行环境管理：Docker Compose 使用 `local` 驱动并保留最多 3 个 10 MB 文件，systemd 使用 journald。应用内的日志大小设置不会限制这部分日志。

## 直播历史

直播历史从 SQLite 分页读取，不扫描全部录像目录。`history_max_records` 控制保留数量，超限时移除最旧记录。

“清理历史”只删除以下数据库记录：

- 直播场次
- 历史记录关联的文件条目

磁盘上的录像文件不会被删除。录像文件删除由投稿后的后处理或人工操作控制。

## 备份

完整备份应包括：

```text
data.sqlite3
账号 JSON 文件
downloads/
config/
```

备份 SQLite 前先停止服务，或者使用 SQLite 在线备份工具，避免直接复制正在写入的数据库文件。

恢复时保持目录结构不变，并使用相同的 `BILIUP_HOME` 或路径环境变量启动。Alembic 会在启动时继续执行必要迁移。

## 故障处理顺序

1. 请求 `/healthz`，确认服务进程可访问。
2. 查看任务平台中的录制与投稿状态。
3. 在实时日志中选择对应类别。
4. 检查 FFmpeg 是否在 `PATH` 中、磁盘空间是否充足。
5. 上传问题先检查账号有效性、线路和并发数。
6. 保留数据库和录像，再重启服务验证；不要先删除数据。

开发环境可以运行：

```shell
uv run pytest -q
npm run lint
npm run build
```
