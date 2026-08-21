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
| 嫌 CLI 工具难上手 | **一条命令**：`pip install ./client` 然后 `memory-sync sync ./notes` |

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

## 工作原理

```
+------------+   HTTPS (Caddy, TLS 1.3)   +------------------+
|  设备 A    | --------------------------> |   memory-sync   |
|  (CLI +    |  拉取事件 (seq 游标)         |     server       |
|  .md 文件) | <-------------------------- |  FastAPI + PG16  |
+------------+                             |   + RLS + Caddy  |
+------------+                             +------------------+
|  设备 B    | -- 同一协议 --------------> (唯一事实来源)
|  (CLI +    |
|  .md 文件) |
+------------+
```

客户端每次同步分三步：

1. **拉取（Pull）**——获取 `seq` 游标之后的新事件，写入磁盘（冲突的服务端副本进 `conflicts/`）
2. **推送（Push）**——哈希扫描本地所有 Markdown 文件，只上传哈希变化的部分，并携带编辑所基于的 `base_version_id`
3. **收敛（Converge）**——服务端拒绝基于过期版本（stale base）的推送（409），客户端重新拉取，所有设备最终收敛到同一状态

服务端保存不可变的文件版本 + append-only 事件日志，完整历史随时可以回放或审计。

## 快速开始（5 分钟）

### 1. 启动服务器（任意 Linux VPS 或 Docker 机器）

```bash
git clone https://github.com/chenyanze66/memory-sync.git
cd memory-sync
cp .env.example .env
# 编辑 .env：设置 SYNC_DOMAIN、ACME_EMAIL，并替换所有 replace-with-* 的值
docker compose up -d
curl -fsS https://你的域名/readyz   # -> {"status": "ready"}
```

搞定——PostgreSQL、API、Caddy（自动 HTTPS）全部就绪。

### 2. 安装客户端（Windows / macOS / Linux）

```bash
pip install ./client          # 或：pipx install ./client
memory-sync register --server https://你的域名 --email you@example.com --display-name me
memory-sync sync ./notes      # 随时手动跑，或配置 cron / 计划任务
```

第二台设备上：

```bash
memory-sync login --server https://你的域名 --email you@example.com
memory-sync sync ./notes
```

两台机器的 `notes/` 文件夹从此保持一致。配置一个计划任务（Windows 任务计划程序 / `crontab`）后就是全自动。

### 命令一览

| 命令 | 作用 |
|---|---|
| `memory-sync register` | 创建账号（邀请码可选）并注册本设备 |
| `memory-sync login` | 在另一台设备登录并绑定新设备密钥 |
| `memory-sync status` | 查看账号、设备、上次同步时间 |
| `memory-sync sync <dir>` | 先拉取待处理事件，再推送本地变更 |

## 项目结构

```
memory-sync/
|-- server/          # FastAPI 同步服务（PostgreSQL + RLS + Ed25519 签名）
|   |-- api/         #   应用代码
|   `-- db/          #   表结构 + 应用角色初始化（首次启动时执行）
|-- client/          # Python CLI（仅依赖标准库 + cryptography）
|   `-- memory_sync_client/
|-- docs/            # 图示与截图
|-- docker-compose.yml
|-- Caddyfile
`-- .env.example
```

## 安全模型

- **传输**：仅 HTTPS（Caddy 自动续期 Let's Encrypt 证书），响应 zstd/gzip 压缩
- **密码**：Argon2id 哈希；不存在的账号也消耗相同的校验时间（防账号枚举）
- **令牌**：短期 access JWT（HS256）+ 轮换 refresh token（复用已吊销令牌会触发该账号全部令牌连坐吊销），令牌存在用户私有配置中
- **设备身份**：每台设备一个 Ed25519 密钥对；请求签名内容为规范化的 `METHOD\nPATH\nTS\nNONCE\nSHA256(body)`，带 nonce 防重放窗口
- **租户隔离**：PostgreSQL `FORCE RLS` + `NOBYPASSRLS` 应用角色，用户 ID 在每个事务内注入
- **每用户配额**：存储按账号限额（文件数 + 全部版本总字节数）、设备数也限额——保护开放注册的服务器不被滥用
- **限流**：认证接口按客户端 IP 限流。注意限流器是**进程内**实现——必须运行**单 API worker**（`docker compose up -d` 默认就是单副本）；多 worker 时限额会按 worker 各自计数
- **仓库无密钥**：所有配置通过 `.env`，依赖已用 pip-audit 扫过（0 已知漏洞）

## 路线图

- [x] v0.1 —— 同步核心（拉/推、冲突、墓碑、设备签名、RLS）
- [ ] 远程控制通道——手机向电脑上的 Agent 下发任务
- [ ] 版本历史 + 回滚命令
- [ ] 记忆语义检索（pgvector）
- [ ] Web UI / 共享空间

## 开发

```bash
cd server && python -m pytest tests/        # 服务端纯测试
cd client && python -m pytest tests/        # 客户端测试（离线假传输）
```

## 开源协议

[MIT](LICENSE) (c) 2026 Chen Yanze

---

<p align="center"><b>如果 memory-sync 帮你省了一次手动拷贝，就给个 star 吧。</b></p>
