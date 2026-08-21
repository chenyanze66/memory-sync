---
name: memory-sync
description: 多端 AI 记忆同步——注册/登录/同步 Markdown 记忆文件夹。记忆文件（CURRENT.md、TERMS.md、日记等）在多台设备间自动保持一致，自托管、冲突永不覆盖。底层是本地 CLI 运行时。
whenToUse: 用户提到"同步记忆/记忆同步/memory-sync/多设备记忆/把我的笔记同步"或要求管理多端 Markdown 记忆时使用。注册、登录、查看同步状态、执行同步、排查同步问题都走这里。
metadata:
  version: 0.1.0
  requires: python3.10+ cryptography
---

# memory-sync

把用户的 Markdown 记忆文件夹（笔记、日记、Agent 记忆库）在多台设备之间自动同步。
本 Skill 是交互面：底层命令由本地 CLI 运行时 `memory-sync` 执行（Skill 只负责教你怎么调、结果怎么展示）。

## 前置：确保 CLI 运行时可用

首次使用（或命令报 `command not found`）时，先确认/安装运行时：

- 检查：`memory-sync --help` 或 `memory-sync status`
- 若不存在，提示用户安装（Windows 也可直接解压官网包 https://sync.chuanpiao.ltd/downloads/memory-sync-windows.zip）：
  ```bash
  pip install ./client   # 在仓库根目录（或 pip install memory-sync-client）
  ```
  依赖：Python 3.10+，`cryptography`（pip 会自动装）。

## 可用命令

| 命令 | 作用 |
|---|---|
| `memory-sync status` | 查看账号、设备、上次同步时间（未登录会提示） |
| `memory-sync register --server <URL> --email <邮箱> --display-name <名称>` | 创建账号并注册本设备（邀请码可选） |
| `memory-sync login --server <URL> --email <邮箱>` | 在另一台设备登录并绑定新设备密钥 |
| `memory-sync sync <目录>` | 拉取待处理事件，再推送本地变更 |

## 关键约定（必须遵守）

1. **密码绝不用 `--password` 传参**（会暴露在进程列表里）。优先让用户设置环境变量 `MEMORY_SYNC_PASSWORD`，否则走交互式提示（getpass）。命令行里也不要回显密码。
2. **`--config`、`--server` 必须放在子命令之后**，例如 `memory-sync sync --config C:\...\config.json --sync-root ./notes`；放在子命令前会被 argparse 静默忽略（历史坑）。
3. **`--server` 默认**：https://sync.chuanpiao.ltd（开放注册的公共演示服务器）。用户自建服务器时换成自己的域名。
4. 同步输出是两行摘要：
   - `pull: N applied, M conflicted (next_seq=K)`
   - `push: N pushed, U unchanged, C conflicted`
   向用户汇报时翻译成中文：拉取应用 N 个、冲突 M 个；推送 N 个、未变化 U 个、冲突 C 个。
5. **冲突不覆盖**：冲突的服务端副本会存到 `<同步根>/conflicts/<时间戳>/<路径>`，本地文件永远保留。向用户说明"服务端版本已存到 conflicts 目录，本地文件没动"。
6. 删除也是同步事件（tombstone），所有设备会收敛。

## 引导式 Onboarding（用户第一次用）

用户说"开始用 memory-sync"或"帮我配置记忆同步"时，按顺序引导：

1. 确认同步目录（默认建议：用户常用的记忆目录，如 `~/notes` 或 `C:\Users\<用户>\notes`）
2. 收集注册信息：`--email`、`--display-name`（对话内收集，非敏感）
3. 密码：提示用户设置 `MEMORY_SYNC_PASSWORD` 环境变量（不要让密码进对话/命令行）
4. 执行 `memory-sync register --server ... --email ... --display-name ...`
5. 首次同步：`memory-sync sync <目录>`
6. 汇报结果，并建议配置计划任务（Windows 任务计划程序 / crontab）实现全自动

## 常见问题

- **`error: not registered`**：先 register 或 login。
- **`HTTP 401 invalid access token`**：客户端会自动 refresh 一次；若 refresh 失败提示用户重新登录。
- **`HTTP 413 user storage quota exceeded`**：用户存储配额（文件数/总字节数）超限，提示清理或联系管理员。
- **`HTTP 429 too many authentication attempts`**：认证限流（10 次/5 分钟/IP），等几分钟再试。
- **`conflict:` 输出**：见上面第 5 条，属于预期行为，不是错误。
