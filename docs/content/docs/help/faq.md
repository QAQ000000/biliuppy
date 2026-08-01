+++
title = "FAQ"
description = "常见问题解答"
date = 2021-05-01T19:30:00+00:00
updated = 2025-08-01T19:30:00+00:00
draft = false
weight = 30
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "Answers to frequently asked questions."
toc = true
top = false
+++

## 如何登录B站？

在 WebUI 的用户管理中选择扫码登录。登录信息保存在统一数据目录，并可直接用于投稿模板。

## 如何使用账号密码登录？

由于目前使用账号密码登录大概率触发验证码，建议使用扫码登录。
如果仍需使用账号密码，在代码中调用：

```python
from biliup.plugins.bili_webup import BiliBili, Data

with BiliBili(Data()) as bili:
    bili.login_by_password("username", "password")
```

## 上传失败怎么办？

1. 检查 `cookies.json` 是否有效，可重新扫码登录
2. 检查网络连接，国外VPS建议选择 `kodo` 线路
3. 检查视频文件格式是否支持
4. 查看日志中的具体错误信息

## 如何选择上传线路？

国内VPS建议使用 `upos` 模式的 `bda2`（百度）线路。
国外VPS建议使用 `upos` 模式的 `ws`（网宿）或 `qn`（七牛）线路，或 `bupfetch` 模式的 `kodo` 线路。

## 为什么B站不能多P上传？

B站网页端根据用户权重限制分P数量。权重不够的用户切换到客户端提交接口即可解除限制。
> 用户等级大于3，且粉丝数>1000，web端投稿不限制分P数量。

## 如何设置定时录制？

在 WebUI 中为每个主播设置录制时间范围，或通过配置文件的 `segment_time` 参数设置分段时长。

## 如何录制弹幕？

在 WebUI 中开启对应主播的弹幕录制开关，或在配置文件中设置 `danmaku: true`。

支持的弹幕录制平台：Bilibili、斗鱼、虎牙、抖音。

## 录制的XML弹幕文件如何使用？

- 使用 [DanmakuFactory](https://github.com/hihkm/DanmakuFactory) 将XML弹幕文件转化为ASS字幕文件
- [AList](https://alist.nn.ci/zh/) 检测到同文件夹下的XML文件会自动挂载弹幕
- 使用 [弹弹play](https://www.dandanplay.com/) 可直接挂载XML弹幕文件观看

## Docker 部署后如何查看日志？

```bash
docker logs -f biliup
```

## 如何升级到最新版本？

```bash
# Python 版本
pip3 install --upgrade biliup

# Docker 部署
docker pull ghcr.io/biliup/caution:latest
docker restart biliup
```

## 如何开机自启？

参考 [安装指南](../guide/introduction/#linux下配置开机自启) 中的 systemd 配置。

## 支持的直播平台有哪些？

目前支持录制以下平台：
- Bilibili（B站）
- 斗鱼（Douyu）
- 虎牙（Huya）
- 抖音（Douyin）
- Twitch
- Youtube（YouTube Live）
- 以及更多...

## 如何配置文件上传？

上传功能需要登录B站，通过 `biliup login` 获取 `cookies.json`，并放入启动 biliup 的路径即可。

## 遇到问题如何反馈？

- 提交 [GitHub Issue](https://github.com/biliup/biliup/issues)
- 在 [Discussion](https://github.com/biliup/biliup/discussions) 中提问
