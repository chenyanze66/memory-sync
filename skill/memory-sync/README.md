# memory-sync Skill

把 `memory-sync` 的能力以 **Skill 形式**装进你的 Agent（DSH / Claude 等），零安装体验：
放进 Agent 的 skills 目录即可，之后直接对话说"同步一下我的记忆"。

## 安装（DSH）

```bash
# 把本目录拷到 DSH 用户 skills 目录
mkdir -p ~/.dsh/skills
cp -r skill/memory-sync ~/.dsh/skills/memory-sync
# 或（Windows PowerShell）
# New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills"
# Copy-Item skill/memory-sync "$env:USERPROFILE\.dsh\skills\memory-sync" -Recurse
```

重启 DSH 会话后，直接说：

- "开始用 memory-sync" → Agent 引导注册
- "同步我的记忆" → Agent 执行同步并汇报

## 其他 Agent（Claude 等）

把 `SKILL.md` 内容复制进目标平台的 skill/plugin 定义，底层命令不变（`memory-sync` CLI）。

## 架构（三层）

```
Skill（交互面）  告诉 Agent：有哪些命令、怎么调、结果怎么展示
CLI（薄运行时）  传输 + 本地状态（本目录对应仓库的 client/）
服务端（大脑）   合并 / 版本 / 配额 / 认证（server/）
```

CLI 是运行时依赖：Skill 首跑自动 `pip install ./client`（或提示安装），用户无需手动装软件。
