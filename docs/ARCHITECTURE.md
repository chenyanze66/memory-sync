# memory-sync 架构与流程图

## 1. 系统架构（三层）

```mermaid
flowchart TB
    U1[用户 A] -->|自然语言对话| A1[Agent A<br/>(memory-sync Skill)]
    U2[用户 B] -->|自然语言对话| A2[Agent B<br/>(memory-sync Skill)]
    A1 -->|调用 CLI 命令| C1[CLI 薄运行时<br/>transport + 本地状态]
    A2 -->|调用 CLI 命令| C2[CLI 薄运行时<br/>transport + 本地状态]
    C1 -->|HTTPS /v1/*| S[服务端<br/>FastAPI]
    C2 -->|HTTPS /v1/*| S
    S --> PG[(PostgreSQL 16<br/>RLS + 事件日志 + 版本)]
    S --> CAD[Caddy<br/>TLS 1.3 + zstd]
    CAD -->|自动续期| LE[Let's Encrypt]

    style A1 fill:#d4f0ff
    style A2 fill:#d4f0ff
    style C1 fill:#e6ffe6
    style C2 fill:#e6ffe6
    style S fill:#fff3d4
```

## 2. 一次同步周期（pull → push → pull）

```mermaid
sequenceDiagram
    participant A as 设备 A (CLI)
    participant S as 服务端
    participant B as 设备 B (CLI)

    Note over A,S: 1. 拉取
    A->>S: GET /v1/sync/pull (after_seq)
    S-->>A: 新事件列表 (content + version)
    A->>A: 应用事件；冲突副本落 conflicts/<UTC>/<path>

    Note over A,S: 2. 推送（只传变化）
    A->>A: 哈希扫描本地 .md 文件
    A->>S: POST /v1/sync/push (content_hash + base_version_id)
    S->>S: 校验 base → 乐观并发检测
    alt base 匹配 head
        S-->>A: accepted（推进 head，事件 seq+1）
    else base 过期
        S-->>A: 409 conflict（不覆盖）
    end

    Note over A,S: 3. 收尾拉取（推进本地 seq）
    A->>S: GET /v1/sync/pull
    S-->>A: 空/新事件 → seq 收敛

    Note over B,S: 另一台设备下次同步自动收敛
    B->>S: GET /v1/sync/pull (after_seq)
    S-->>B: 全部事件 → B 与 A 一致
```

## 3. 冲突处理流程

```mermaid
flowchart TD
    P[客户端 push 携带 base_version_id] --> C{服务端校验}
    C -->|base == 当前 head| OK[accepted：写入新版本<br/>推进 head，事件 seq+1]
    C -->|base 已过期 / 并发修改| CON[409 conflict]
    CON --> DIV[服务端副本落<br/>conflicts/<UTC时间戳>/<路径>]
    DIV --> LOCAL[本地文件保留不动]
    CON --> RES{用户/Agent resolve}
    RES -->|人工选择| R1[resolve 合并结果 → 新版本]
    RES -->|放弃本地| R2[直接采纳服务端版本]
    RES -->|放弃服务端| R3[再次 push 覆盖]

    style CON fill:#ffd4d4
    style DIV fill:#ffe6cc
```

## 4. 删除（tombstone）传播

```mermaid
flowchart LR
    DEL[设备 A 删除 TERMS.md] --> PUSH[push deleted=true<br/>content_hash=sha256('')]
    PUSH --> S[服务端写入 tombstone 版本<br/>事件 seq+1]
    S --> PULL[设备 B 下次 pull<br/>收到 deleted 事件]
    PULL --> APPLY[设备 B 本地删除该文件]
    PULL --> EDGE{设备 B 本地有未推送修改?}
    EDGE -->|是| KEEP[保留本地文件 → conflicts/]
    EDGE -->|否| OK2[正常删除，两端一致]
```

## 5. 认证与设备注册

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as Agent (Skill)
    participant S as 服务端

    U->>A: "开始用 memory-sync"
    A->>U: 引导：邮箱 / 显示名 / 密码(env 或隐藏输入)
    U->>A: 提供信息
    A->>S: POST /v1/auth/register (+X-Invite-Code 可选)
    S-->>A: access + refresh token
    A->>S: POST /v1/devices/register (Ed25519 公钥)
    S-->>A: device_id
    A->>S: 首次 sync
    Note over S: refresh 轮换 + 家族吊销：<br/>已吊销 token 再使用 → 该账号全部 token 连坐吊销
```

## 6. GitHub 流水线（发布 / 部署）

```mermaid
flowchart LR
    PUSH[push main] --> CI[workflow: tests<br/>client 54 + server 20 测试]
    TAG[打 tag v*] --> REL[workflow: release<br/>构建客户端 zip + checksums<br/>docker 构建检查 → 附加到 Release]
    MAN[手动触发] --> DEP[workflow: deploy<br/>打包 server/ → scp 到 ECS]
    DEP --> SSH[ssh 部署脚本]
    SSH --> BK[备份 memory-sync-2g.bak-时间戳]
    SSH --> OV[覆盖 server/ 代码<br/>保留 .env / compose / site]
    SSH --> ZIP[更新官网客户端 zip + checksums]
    SSH --> BUILD[docker compose build api<br/>ACR 基础镜像 + 阿里云 pip 源]
    SSH --> UP[docker compose up -d api]
    SSH --> HC[curl readyz 健康检查]
    HC -->|ready| DONE[DEPLOY_OK ✅]
