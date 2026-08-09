+++
title = "常见问题"
description = "登录、录制、投稿、日志和升级问题。"
date = 2026-08-02T08:00:00+00:00
updated = 2026-08-09T08:00:00+00:00
draft = false
weight = 10
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "常见问题与处理建议。"
toc = true
top = false
+++

## 如何登录 B 站？

在 WebUI 的“投稿管理”中打开用户管理，选择扫码登录。账号信息保存为 `BILIUP_DATA_DIR` 下的 JSON 文件，可直接用于投稿模板。

不建议使用账号密码登录，容易触发验证码和风控。

## 如何确认账号是否有效？

用户管理会读取 B 站账号资料并显示头像和昵称。显示“Cookie 已失效”时，应删除该账号并重新扫码登录。不要在日志、Issue 或聊天中发送账号 JSON、Cookie、访问令牌。

## 上传失败怎么办？

按以下顺序检查：

1. 在用户管理确认账号有效。
2. 在投稿模板确认分区、标题、标签和转载来源等必填字段。
3. 查看“上传日志”中的线路、HTTP 状态和投稿返回值。
4. 切换 `lines` 后重新发起手动上传。
5. 降低或适当提高 `threads`，通常从 `3` 开始测试。
6. 确认录像文件仍存在且格式受 B 站支持。

投稿失败不会执行默认删除操作，录像会保留用于重试。

## 切换上传线路后重新上传会生效吗？

使用 `bili_web` 时会生效。手动上传任务在创建时读取最新模板和全局配置，保存新线路后重新上传会使用新线路；已经运行中的任务不会中途切换。`bili_browser` 使用创作中心浏览器协议，`lines` 和 `threads` 不控制其线路或速度。

## 如何选择上传线路？

优先使用 `AUTO`。需要手动测试时，国内网络可比较 `bda2`、`bldsa`、`qn`、`tx`，海外网络可比较 `txa`、`qn`、`alia`。线路速度随地区、运营商和时段变化，应以日志中的实际平均速度为准。

## Web 上传快，但程序上传慢怎么办？

浏览器和程序可能命中不同 UPOS 节点。使用 `bili_web` 时先比较线路，再调整 `threads`。如果创作中心上传明显更快，可以把投稿模板的 `uploader` 改为 `bili_browser`；该模式使用 Playwright 调用创作中心新版上传协议。还应检查磁盘读取速度、代理和安全软件。

## 浏览器高速上传是怎么工作的？

`bili_browser` 使用 Playwright 的可见 Chromium 上传视频，程序等待并捕获全部分 P 的上传结果，再按选择顺序使用 Python Web API 提交标题、简介、标签等稿件信息。只有 API 明确拒绝时才回退页面提交；超时或连接中断后不会盲目二次投稿。

## Linux 报“Browser upload requires a display”怎么办？

浏览器高速上传必须以可见浏览器模式运行，纯命令行服务器没有显示设备。Xvfb 提供虚拟屏幕，不需要安装 GNOME、KDE 等完整桌面环境。安装并使用虚拟显示启动：

```shell
sudo apt install -y xauth xvfb
uv run playwright install --with-deps chromium
xvfb-run -a -s "-screen 0 1920x1080x24" \
  uv run biliup server --host 0.0.0.0 --port 19159
```

Playwright Chromium 必须由实际运行服务的系统账号安装。使用 systemd、宝塔面板或 Supervisor 时，应把 `xvfb-run` 写入真正的启动命令并重启服务；只安装 Xvfb 或只设置 `DISPLAY=:99` 不会自动创建可用的显示服务。

## 多 P 会乱序或漏传吗？

投稿预览中的分 P 顺序就是用户选择顺序。浏览器上传器会等待全部文件上传完成，并把捕获到的每个 `filename`、`biz_id/cid` 按该顺序交给投稿 API；无法取得完整分 P 信息时才回退页面提交。

## 任务显示“结果未知”时能直接重试吗？

不建议。结果未知表示最终投稿请求可能已经被 B 站接收，只是程序没有拿到确定响应。系统不会自动重试，以免产生重复稿件；先在创作中心检查稿件列表，确认没有生成后再手动重试。

## 创作中心出现“批量操作”浮层怎么办？

它不是必填项，浏览器上传器会自动点击浮层内部的“取消”后继续。不要使用 F12 删除 DOM；若页面改版导致无法自动关闭，请保留脱敏日志和失败截图。

## 如何设置录制分段？

在全局设置或主播覆写中配置：

- `segment_time`：最长分段时长，格式 `HH:MM:SS`
- `file_size`：最大分段大小，单位 Byte

同时设置时，先达到限制的一项结束当前分片。`segment_time` 不是定时录制时间范围；主播允许录制的时间范围应在主播编辑页设置。

## 如何录制弹幕？

在主播的 Bilibili 配置覆写中开启 `bilibili_danmaku`。其他平台使用各自的弹幕开关，例如 `douyu_danmaku`、`huya_danmaku`、`douyin_danmaku`。

弹幕以同名 XML 文件保存，不会自动烧录进视频。可以使用 DanmakuFactory 转为 ASS，或使用支持 XML 弹幕的播放器加载。

## 清理直播历史会删除录像吗？

不会。清理功能只删除 SQLite 中的直播历史和关联文件条目，不删除磁盘录像。投稿后的自动删除由后处理配置控制。

## 为什么日志页面没有内容？

确认：

1. 服务已经产生对应类别的日志。
2. `BILIUP_LOG_DIR` 指向当前服务使用的日志目录。
3. 浏览器连接的是同一个服务端口。
4. 日志没有刚刚被手动清理。

日志页面按程序、录制和上传分类，同一条日志不会重复归入多个类别。

## 如何限制日志和历史大小？

全局设置中配置：

- `log_file_max_size_mb`：单个日志文件上限，保留 5 个轮转备份
- `history_max_records`：直播历史数据库记录上限

全局设置还提供“清理日志”；直播历史页面提供“清理历史”。

## 数据库在哪里？

源码默认位于项目的 `data/data.sqlite3`。使用 `--home`、`BILIUP_HOME` 或 `BILIUP_DATABASE` 时，以指定路径为准。Docker Compose 默认映射为主机的 `./data/data.sqlite3`。

## 如何升级？

先停止服务并备份运行目录。源码更新后执行：

```shell
uv sync --extra dev
npm ci
npm run build
uv run pytest -q
```

Docker：

```shell
docker compose down
docker compose build --pull
docker compose up -d
```

数据库会在启动时自动迁移，不要删除旧数据库。

## 支持哪些平台？

常用直播平台包括 Bilibili、斗鱼、虎牙、抖音、Twitch 和 YouTube。实际支持范围以 `biliup/platforms` 中已注册的平台插件及 WebUI 可创建的 URL 为准。

## 遇到问题如何反馈？

提交问题时提供：

- 版本和启动方式
- 操作系统与 Python、FFmpeg 版本
- 问题发生时间和最小复现步骤
- 已脱敏的相关日志

不要提交 Cookie、账号 JSON、访问令牌、上传签名或私人录像链接。
