+++
title = "直播录制和自动投稿"

[extra]
lead = '<b>biliup Python Edition</b> 提供直播检测、FFmpeg 录制、任务管理和 B 站自动投稿。'
url = "/docs/getting-started/quick-start/"
url_button = "快速开始"
repo_version = "Python 1.1.7"
repo_license = "Open-source MIT License."
repo_url = "https://github.com/QAQ000000/biliuppy"

[[extra.menu.main]]
name = "文档"
section = "docs"
url = "/docs/getting-started/introduction/"
weight = 10

[[extra.list]]
title = "纯 Python 后端"
content = "FastAPI、SQLAlchemy 和 Alembic 负责 API、任务调度与 SQLite 数据迁移，不依赖 Rust 运行时。"

[[extra.list]]
title = "自动录制"
content = "检测开播和下播，按时长或大小分段，支持 Bilibili、斗鱼、虎牙、抖音、Twitch 和 YouTube 等平台。"

[[extra.list]]
title = "B 站投稿"
content = "扫码登录、投稿模板、手动重传、上传线路切换、分片并发、失败重试和进度日志。"

[[extra.list]]
title = "WebUI 管理"
content = "集中管理主播、投稿模板、录像、任务、历史、运行状态和分类日志。"

[[extra.list]]
title = "数据可升级"
content = "沿用已有 SQLite 数据库，启动时自动迁移；录像、日志、账号和缓存目录均可独立指定。"

[[extra.list]]
title = "运维可控"
content = "提供日志轮转与清理、历史数量上限与清理，失败时保留录像文件。"
+++
