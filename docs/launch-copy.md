# memory-sync 发布文案（复制即用）

> 仓库：https://github.com/chenyanze66/memory-sync
> Live demo：https://sync.chuanpiao.ltd（开放注册，5 分钟跑通双端同步）
> 状态：v0.1，MIT，客户端 54/服务端 20 测试通过（含 parametrize 展开），CI 绿

---

## 1. 掘金 / 知乎（中文长文）

**标题**：我给自己写了个 AI 记忆同步工具：多端 Markdown 记忆自动一致，冲突永不覆盖，2G 服务器就能跑

正文：

> 背景：我每天用 AI Agent 记录想法、维护长期记忆（CURRENT.md、TERMS.md 这类 Markdown），但工作电脑、家里电脑、手机三处各写各的，手动拷贝到崩溃。
>
> 市面上的方案：网盘（全量传、冲突自己处理）、Git（门槛高）、SaaS 记忆服务（数据给别人、要钱、不透明）。都不对味。
>
> 于是我写了一个**自托管的记忆同步服务**：PostgreSQL + FastAPI + Caddy，一条 `docker compose up` 起服务，客户端 `pip install ./client` 后 `memory-sync sync ./notes` 即可。
>
> **设计重点**（也是和普通网盘的本质区别）：
> - **增量**：客户端维护哈希快照，只传变化的文件
> - **先拉后推**：绝不拿旧数据覆盖新编辑
> - **冲突永不静默覆盖**：服务端副本落 `conflicts/<时间戳>/`，本地文件永远保留
> - **删除也是事件**：tombstone 同步到所有端，误删可从服务端事件日志追溯（rollback 命令在路线图中）
> - **安全默认**：HTTPS、Argon2id、Ed25519 设备签名、PostgreSQL RLS 账号隔离（每个用户只能看自己的数据）
> - **小资源**：三容器合计约 1 GiB，2C2G 的服务器就能跑，我自己的实例就在 2 GiB 阿里云上
>
> 5 分钟试玩：https://sync.chuanpiao.ltd（开放注册）
>
> 开源：https://github.com/chenyanze66/memory-sync （MIT，欢迎 star / issue / PR）
>
> 下一步计划：手机远程控制电脑 Agent（指令通道）、版本回滚命令、pgvector 语义检索。

---

## 2. V2EX（中文短贴）

**标题**：开源了一个给 AI Agent 用的多端记忆同步工具，一条 docker compose 自部署

内容：

- 痛点：多设备多 Agent 的记忆文件（Markdown）各写各的，手动拷贝崩溃
- 方案：自托管同步服务（FastAPI + PostgreSQL + Caddy），客户端 CLI，增量 + 冲突不覆盖 + 设备签名
- 亮点：`docker compose up` 5 分钟跑通；2G 服务器够用；开放注册 demo：sync.chuanpiao.ltd
- 仓库：https://github.com/chenyanze66/memory-sync （MIT）
- 欢迎 star / 提 issue；想一起做手机控制通道的可以聊聊

---

## 3. Hacker News（English）

**Title**: Show HN: Memory-sync – self-hosted multi-device sync for AI agent memory files

Body:

> Agents keep their state in Markdown (CURRENT.md, TERMS.md, daily notes). I kept editing the same files on 3 machines and syncing by hand.
>
> memory-sync is a self-hosted sync service: FastAPI + PostgreSQL 16 (RLS) + Caddy, plus a Python CLI. `docker compose up` runs the whole stack on a 2 GB VPS; `memory-sync sync ./notes` keeps folders identical everywhere.
>
> What makes it different from a file sync / netdisk:
> - Incremental: client keeps a hash snapshot, only changed files are transferred
> - Pull-then-push: never overwrite fresh local edits with stale pushes
> - Conflicts never overwrite: server copies go to conflicts/<timestamp>/, local files always win
> - Deletes are events (tombstones), version history is replayable from the event log
> - Ed25519 per-device request signing, nonce anti-replay, PostgreSQL FORCE RLS tenant isolation
> - ~1 GiB total footprint for all containers
>
> Live demo (open registration): https://sync.chuanpiao.ltd
> Repo: https://github.com/chenyanze66/memory-sync (MIT)
>
> Roadmap: phone->PC agent command channel, rollback commands, pgvector semantic search.
> Happy to answer questions / take feedback.

---

## 4. Reddit r/selfhosted + r/LocalLLaMA（English）

**Title**: I built a self-hosted sync for AI agent memory files (Markdown) – docker compose up on any 2GB VPS

Body:

> If you use AI agents that keep memory in Markdown (CURRENT.md, long-term notes), you probably hit the same problem: the same files live on 3 machines and stay in sync by hand.
>
> I built memory-sync: FastAPI + PostgreSQL 16 + Caddy server, Python CLI client.
> - incremental sync by content hash
> - pull-then-push so fresh local edits are never clobbered
> - conflicts are diverted to conflicts/<timestamp>/ instead of overwriting
> - Ed25519 device signing + RLS account isolation
> - runs in ~1 GiB of RAM
>
> 5-min setup: `docker compose up -d` on the server, `pip install ./client` + `memory-sync sync ./notes` on each device.
> Try the live demo (open registration): https://sync.chuanpiao.ltd
> Repo: https://github.com/chenyanze66/memory-sync
>
> Next up: phone->PC command channel so I can tell my home agent to do stuff while away. Feedback welcome!

---

## 5. 阮一峰周刊自荐（中文）

**标题**：memory-sync：多端 AI 记忆同步（自托管）

一句话：给 AI Agent 用的 Markdown 记忆文件多端自动同步，增量传输、冲突永不覆盖、Ed25519 设备签名，`docker compose up` 5 分钟自部署，2G 服务器即可。
链接：https://github.com/chenyanze66/memory-sync （MIT，live demo: sync.chuanpiao.ltd）

---

## 发布 checklist
- [ ] README demo.gif 已替换为真实录制
- [ ] GitHub release v0.1.0（tag + release notes）
- [ ] 发 V2EX（周四/周五流量好）
- [ ] 发掘金
- [ ] 发 HN（北京时间凌晨 0-2 点 = 美东中午，最佳窗口）
- [ ] 发 Reddit r/selfhosted + r/LocalLLaMA
- [ ] 邮件自荐阮一峰周刊（周五 12:00 前）
- [ ] 拉 3-5 个朋友先点 star
