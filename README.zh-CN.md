# memory-sync · 多端记忆同步（给 AI Agent 用）

[English](README.md) | **中文**

**让 AI Agent 的记忆文件在每一台设备上自动保持一致——自动、安全、完全自托管。**

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](client/pyproject.toml)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey)

> **在线演示：** [https://sync.chuanpiao.ltd](https://sync.chuanpiao.ltd) —— 开放注册，5 分钟内两台设备同步一个文件夹。

![memory-sync 演示 - 两台设备，一份记忆](docs/demo.gif)

![memory-sync hero](docs/hero.webp)

你的记忆以 Markdown 文件存在：`CURRENT.md`、`TERMS.md`、日记、长期知识。有了 memory-sync，你不再需要在工作电脑、家里电脑和手机之间手动拷贝——所有设备上的文件保持一致，**任何情况下都不会被静默覆盖**。

## 为什么选 memory-sync？

| 你的痛点 | memory-sync 的解法 |
|---|---|
| 记忆散落在多台设备、多个 Agent 之间 | **一键同步**：所有端收敛到同一份文件 |
| 重装系统后怕丢笔记 | **版本化历史**存在 PostgreSQL——每次变更都是一个事件 |
| 不信任会覆盖你编辑的云同步 | **冲突永不覆盖**：服务端副本落 `conflicts/<时间戳>/`，本地文件永远保留 |
| 不想把私人笔记交给 SaaS | **100% 自托管**：任意 2GB VPS 一条 `docker compose up` |
| 嫌 CLI 工具难上手 | **一个会听人话的 Skill**：放进 skills 目录，说一句"同步我的记忆"就行 |

## 功能特性

- **增量同步**——只传输变化的文件；客户端维护本地哈希快照，只推送内容真正变过的文件
- **先拉后推**——每次同步先拉取服务端事件，新编辑永远不会被旧的推送盲目覆盖
- **冲突安全设计**——基于 `base_version_id` 的乐观并发；真实冲突落 `conflicts/<UTC>/<path>`，绝不覆盖
- **删除也按事件处理**——tombstone（墓碑）传播到所有端；针对远端删除的本地编辑会被保留
- **Ed25519 设备签名**——每个请求由设备私钥签名，带 nonce + 时间戳防重放
- **账号隔离（RLS）**——PostgreSQL 行级安全：每个用户只能看到自己的文档
- **零第三方服务**——只有 PostgreSQL + FastAPI + Caddy，没有 Redis、没有消息队列、没有 SaaS
- **占用极小**——2 vCPU / 2 GiB 的 VPS 就能流畅运行（三个容器合计约 1 GiB）
- **zstd + gzip 压缩**——Caddy 对每个响应自动压缩
- **可选邀请码门槛**——`REGISTRATION_INVITE_CODE` 留空 = 开放注册；设置值则锁定服务器