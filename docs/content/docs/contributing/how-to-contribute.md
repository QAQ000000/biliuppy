+++
title = "How to Contribute"
description = "Contribute to biliup, improve documentation, or submit to showcase."
date = 2021-05-01T18:10:00+00:00
updated = 2025-08-01T18:10:00+00:00
draft = false
weight = 410
sort_by = "weight"
template = "docs/page.html"

[extra]
lead = "Contribute to biliup, improve documentation, or submit to showcase."
toc = true
top = false
+++

## 贡献代码

biliup 项目采用 Python + FastAPI + Next.js 架构，欢迎贡献代码。

### 项目结构

- `biliup/` - Python API、数据库、调度、录制和上传
- `app/` - Next.js 前端（WebUI）
- `tests/` - 离线自动化测试

### 开发流程

1. Fork 项目到自己的仓库
2. 创建特性分支：`git checkout -b feat/your-feature`
3. 提交变更：`git commit -m "feat: add your feature"`
4. 推送到分支：`git push origin feat/your-feature`
5. 提交 Pull Request

### 提交规范

请遵循 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) 规范：

- `feat:` 新功能
- `fix:` 修复bug
- `docs:` 文档更新
- `refactor:` 重构
- `chore:` 构建过程或辅助工具的变动

## 报告问题

- [Bug report](https://github.com/biliup/biliup/issues/new?template=bug-report.yaml)
- [Feature request](https://github.com/biliup/biliup/discussions/new?category=ideas)

## 改进文档

文档位于项目 `docs/` 目录下，使用 Zola 静态站点生成器构建。
欢迎提交 PR 改进文档内容。
