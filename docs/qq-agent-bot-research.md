# QQ 接入 AI Agent 机器人 研究报告

> 调研时间：2026-11。所有配额/版本数字来自官方文档与公开资料，腾讯侧策略变动频繁，落地前请以控制台与官方文档为准。

---

## 0. 结论先行

QQ 接 AI Agent **不是"一个问题"，而是两条完全不同的技术路线**。先把路线选对，再谈代码。

```
你要做的是哪种？
│
├─ 面向公众/长期运营/不能封号
│   └─ 路线 A：QQ 开放平台（官方机器人）
│        ├─ A1 零代码：Workbuddy / QClaw / OpenClaw 等 Claw 客户端扫码绑定（官方"龙虾 Bot"）
│        ├─ A2 半代码：官方插件（openclaw-qqbot / dsh-qqbot / Hermes gateway）
│        └─ A3 全代码：botpy / botgo / @tencent-connect/qqbot-connector 自写网关
│
└─ 自用/私域群/要完整 QQ 能力（读全部群消息、主动发言、发任意卡片）
    └─ 路线 B：协议端（NapCat / LLOneBot）+ OneBot 11 + Agent Bridge
         ├─ B1 现成平台：AstrBot / NoneBot2（配 MCP、插件）
         └─ B2 自写 bridge：OneBot 事件 → 本地 Agent（ACP / MCP）→ 回包
```

一句话选型：

| 你的诉求 | 选 |
|---|---|
| 想把已有的 coding agent（Claude Code / OpenClaw / dsh / CodeBuddy 系）搬到 QQ 上遥控 | **A1/A2，扫码即通，30 分钟内可用** |
| 做一个对外可用的 QQ 群助手（客服/问答/社群运营） | **A3，官方 API 自建网关** |
| 要机器人"自己看群里所有消息并主动接话"、读取完整上下文 | **B，NapCat，接受封号风险** |
| 只是自己/小圈子玩，追求功能完整度和开发自由度 | **B2，自写 bridge 最爽** |

---

## 1. 两条路线的本质差异

| 维度 | 路线 A 官方机器人 | 路线 B 协议端（NapCat 等） |
|---|---|---|
| 身份 | 独立 Bot 应用，**不是 QQ 号**，用户看到的是"机器人" | 一个**真实 QQ 账号**（无头 NTQQ 客户端） |
| 协议 | 官方 WebSocket Gateway / Webhook（Ed25519 签名） | OneBot 11（HTTP + 正向/反向 WebSocket） |
| 用户标识 | `group_openid` / `member_openid`（同一用户在不同群 openid 不同，**拿不到 QQ 号**） | 真实 QQ 号、群号，可 @、可查资料、可加好友 |
| 群消息可见性 | 默认**只推送 @机器人** 的消息 | 可读全群消息（需配置） |
| 主动发言 | 受限（每群每天接收上限、qpm 频控、可能需群主授权） | 无平台限制，但频率异常 = 风控 |
| 富媒体 | 官方模板化（文本/Markdown/图文卡片/富媒体上传） | 任意消息段，包括私有 JSON 卡片（需签名，易随版本失效） |
| 合规性 | 合规，在平台规则内 | 违反 QQ 用户协议，**存在封号风险** |
| 上线门槛 | 沙箱 → 提审 → 发布；新增机器人默认启用 IP 白名单 | 无审核，扫码即用 |
| 成本 | 需公网 IP（Webhook）或长连接（WS 可免公网） | 一台能跑 QQ 客户端的机器（Linux 无头 / Docker） |
| 稳定性 | 高，不随 QQ 版本崩 | 中，NTQQ 每次大版本更新可能 Hook 失效 |

**关键认知**：官方路线的"群场景"对主体类型敏感——企业主体默认开频道+群，个人主体默认仅频道，群能力需申请与审核；协议端路线则完全绕开这个门槛，代价是账号风险。这是社区里"QQ 机器人到底还能不能用"两派说法打架的根本原因。

---

## 2. 路线 A：官方开放平台

### 2.1 三种子形态

**A1 零代码（官方"龙虾 Bot"，2026 年主推）**

官方把"Agent 接入"做成了一站式通道：QQ 侧只做**消息壳**，智能能力由你的 Claw 服务提供。

1. 打开快速创建页 `q.qq.com/qqbot/openclaw/login.html`，QQ 登录 → 点"创建机器人"（秒建，机器人会主动给你发一条消息）
2. 在 Claw 客户端里绑定：设置 → 助理设置/远控通道 → QQ 机器人集成 → **手机 QQ 扫码**
3. 或手填 `AppID:AppSecret`

已覆盖的 Agent 框架（截至 2026-08）：Workbuddy（CodeBuddy 系）、QClaw、OpenClaw、Hermes Agent、DeepSeek Harness (dsh)、LightVela（云端托管）、腾讯云 OpenClaw（Lighthouse），以及通用 NPM SDK `@tencent-connect/qqbot-connector`（控制台打印二维码，扫码后回传 appId/appSecret，Node ≥ 18）。

限制：**单个 QQ 账号最多创建 5 个**龙虾 Bot。

**A2 半代码（官方插件）**

```bash
# OpenClaw
openclaw onboard
openclaw plugins install @tencent-connect/openclaw-qqbot@latest
openclaw channels add --channel qqbot --token "AppID:AppSecret"
openclaw gateway restart

# DeepSeek Harness
npx @deepseek-ai/dsh plugin --profile qqbot add @tencent-connect/dsh-qqbot
npx @deepseek-ai/dsh --profile qqbot
```

插件版本节奏很快（1.7.0 → 2.0.3，2026-04 至 2026-08），且与宿主版本强耦合（例如 OpenClaw 2026.7.1-2 之后 `formatInboundEnvelope` 改名 `formatAgentEnvelope`，插件需 ≥2.0.1）。**锁版本**是必须的。

**A3 全代码（自建网关）**

- Python：`pip install qq-botpy`，继承 `botpy.Client`，`intents` 订阅事件，`client.run(appid=..., token=...)`（新版用 AppID + AppSecret 鉴权）
- Go：`botgo`
- 自研：HTTP + 自己实现 Gateway 握手（OpCode 10 Hello → OpCode 2 Identify → 心跳）

关键 intent：群 @ 消息 `GROUP_AT_MESSAGE_CREATE` 属于 `GROUP_AND_C2C_EVENT (1<<25 = 33554432)`，**不订阅就收不到任何群消息**。

### 2.2 官方能力矩阵

| 能力 | 私聊 C2C | 群聊 |
|---|---|---|
| 基础对话 | 支持 | 支持（默认需 @） |
| 流式回复 | 支持（**仅私聊生效**） | 不支持 |
| 全量消息（自主接话） | — | 需双向授权：群内机器人资料页开"获取群内全部消息" + 开"机器人主动在群聊内发言"，同时插件侧 `requireMention: false` |
| 主动推送 | 支持 | 支持，需群主授权 |
| 被拉进群 | — | 群主可直接添加（需新版手机 QQ；沙箱环境不支持加群） |
| 撤回群成员消息 | — | 需机器人被设为群管理员 |
| 多模态 | 文本/语音/图片/视频/文件 | 同左 |
| 引用消息解析 | 支持 | 支持 |
| 命令审批按钮 | 允许一次/始终允许/拒绝 | 同左 |
| 按群隔离人设 | — | 支持（不同群路由到不同 Agent） |

### 2.3 官方配额（务必按最保守值设计）

群消息接口（`POST /v2/groups/{group_openid}/messages`）：

- 被动回复（回复某条 msg_id）：**有效期 5 分钟，每条消息最多回复 5 次**
- 主动消息频控：Bot 维度 —— 企业认证/个人实名认证 **60 qpm**，未认证 **30 qpm**
- 单关系维度（接收方）：**20 qpm**，每个群**每天最多接收 1000 条**
- 接口频率上限 100 QPS
- 群消息**不支持流式参数**

频道场景另有更严的主动推送限制（历史上每频道每天 20 条、每天仅 2 个子频道），做频道 bots 时要单独核。

其他硬门槛：
- 新增机器人**默认启用 IP 白名单**，不填公网 IP 无法提审上线（沙箱不受影响）
- 沙箱环境只收到沙箱名单内的频道/群/私聊事件，且 API 只能操作沙箱数据
- 平台默认域名：正式 `https://api.sgroup.qq.com`，沙箱 `https://sandbox.api.sgroup.qq.com`，取 token `https://bots.qq.com/app/getAppAccessToken`

### 2.4 官方配置中枢（OpenClaw 为例，其他框架同构）

```json
{
  "channels": {
    "qqbot": {
      "accounts": { "default": { "defaultRequireMention": true } },
      "groups": { "<group_openid>": { "requireMention": false } },
      "streaming": { "mode": "partial" },
      "allowFrom": ["<user_openid>"]
    }
  },
  "agents": { "list": [{ "id": "fish", "name": "钓鱼助手", "workspace": "./ws" }] },
  "bindings": [{ "agentId": "fish", "match": { "group": "<group_openid>" } }]
}
```

优先级链：单群 `groups.{openID}` → 通配 `groups."*"` → 账户级 `defaultRequireMention` → 默认 `true`。

常用指令：`/bot-group-always on|off`、`/bot-streaming on|off`、`/bot-version`、`/bot-upgrade`、`/bot-ping`、`/bot-logs`、`/new`（清上下文）。指令后加 `?` 查用法。

---

## 3. 路线 B：协议端 + OneBot 11

### 3.1 组件分工

```
QQ 用户 ──▶ NapCat（无头 NTQQ，协议端）──OneBot 11──▶ Bridge（你写/现成框架）──▶ LLM API 或 本地 Agent
                    只做"手脚"                              只做"接线 + 会话"            才是"大脑"
```

- **协议端**：NapCat（NTQQ Hook，最活跃）、LLOneBot、Lagrange（纯 .NET，不需真客户端，签名易过不去）。go-cqhttp 已停维护、新号难登录，**不要再用**。
- **Docker 部署**（NapCat 官方镜像，支持 amd64/arm64）：

```bash
docker run -d --name napcat --restart=always \
  -e NAPCAT_GID=$(id -g) -e NAPCAT_UID=$(id -u) \
  -p 3000:3000 -p 3001:3001 -p 6099:6099 \
  mlikiowa/napcat-docker:latest
```

端口含义：**6099 = WebUI（扫码登录 + 配置）**，3000 = OneBot HTTP，3001 = OneBot WebSocket。WebUI 默认口令 `napcat`，**首次登录后立刻改**。

- **连接方向**：让"谁主动连"决定谁是 client。Bridge 主动连 NapCat → 在 NapCat 配"WebSocket 服务器"，Bridge 侧连 `ws://napcat:3001` 并带同一个 `access_token`。**token 两边必须一致**，这是最高频的"连不上"原因。

### 3.2 Bridge 的两种接法

**B1 直连 LLM API**（简单、便宜、无工具权限）

```
OneBot 事件 → 过滤（@ / 关键词 / 群白名单）→ 拼上下文 → LLM API → 回复段 → send_group_msg
```

**B2 接本地 Agent**（能干活、有工具权限，推荐给"AI Agent"语义）

这里有两个互补的协议，别混：

| 协议 | 方向 | 用途 |
|---|---|---|
| **ACP**（Agent Client Protocol） | Client(编辑器/你的 bridge) ↔ **Agent 作为 server**，通常 stdio JSON-RPC | 建立会话、投递 prompt、接收流式 chunk、处理工具调用与**权限审批** |
| **MCP**（Model Context Protocol） | **Agent 作为 client** → 工具/数据源 | 给 Agent 扩展能力（12306、数据库、文件系统…） |

所以 bridge 的职责是：把 OneBot 事件翻译成 ACP 的 `session/prompt`，把 ACP 的流式输出与 `tool_call/permission_request` 翻译回 QQ 文本/按钮。参考实现：`happysnaker/qq-ai-bot`（OneBot 11 + ACP，自托管脚手架，MIT，带 Docker demo 与部署矩阵）。

**B3 反向思路：把 QQ 变成 Agent 的工具**

`qq-onebot-mcp` 这类项目把 NapCat 包成 MCP server，让 MCP 宿主（Claude/Cursor/自研 agent）能"收发 QQ 消息"。典型安全设计：
- 私聊（白名单用户）→ 进入 agent，拥有完整工具权限
- 群聊 @机器人（白名单群）→ 走纯 LLM API 回答，**不进 agent、不碰本机**

这个"双通道隔离"很值得抄：**不可信来源不给工具权限**。

### 3.3 风控与账号安全（路线 B 的命门）

社区一致的结论：**换框架不解决封号，因为 NapCat / LLOneBot / Lagrange 都是占用 PC 端协议的同一家人**。能做的是降低触发概率：

1. **绝不使用主号**。专用小号，做好"封了不心疼"的心理建设
2. **养号**：新号先正常用 1~2 周（挂在线、聊天、实名认证），未实名号风控阈值极低
3. **网络**：常用设备 + 固定家庭宽带 IP，避免校园网/动态 IP 频繁变动
4. **行为**：回复加 2~5 秒随机延迟；不发重复内容、不刷链接、不群发卡片；控制接入群数量
5. **上线节奏**：先 1 群 1 人跑通，再逐步扩
6. 保持 NapCat 版本跟随官方更新，Hook 失效时通常第一时间修复

---

## 4. 现成项目速查

| 项目 | 定位 | 适合 |
|---|---|---|
| `tencent-connect/openclaw-qqbot` | 官方维护的 OpenClaw QQ 通道插件 | 已有 OpenClaw，最快落地 |
| `tencent-connect/dsh-qqbot` | DeepSeek Harness 的 QQ 插件 | dsh 用户 |
| `@tencent-connect/qqbot-connector` | 通用扫码绑定 SDK（Node ≥18） | 自研 A3 网关时省掉绑号流程 |
| `AstrBot`（AGPL-3.0, Python） | 一站式 Agent 聊天平台，17+ IM 适配器、1000+ 插件、原生 MCP/Skills、Sub-Agent、Web 管理台 | 要一个"能长期维护的工作台"，不想自己写调度 |
| `NoneBot2` | 轻量异步 bot 框架，OneBot v11 生态最成熟 | 想要代码可控、插件自己写 |
| `happysnaker/qq-ai-bot` | OneBot/NapCat ↔ 本地 Agent（ACP）接线层 + 部署矩阵 | 明确要"QQ 当前端、本地 Agent 当后端" |
| `huliangwei/qq-onebot-mcp` | QQ ↔ MCP 宿主桥，零依赖 Node | 想让现成 agent 直接读写 QQ |
| `botpy` / `botgo` | 官方 SDK | A3 自建网关 |

---

## 5. 推荐架构（若目标是"QQ 里的 AI Agent 员工"）

### 5.1 生产向（合规、对外）

```
手机 QQ ──▶ QQ 开放平台（WS Gateway）
                 │  GROUP_AT_MESSAGE_CREATE / C2C_MESSAGE_CREATE
                 ▼
        你的网关（botpy / botgo）
                 │  鉴权、去重(msg_id+msg_seq)、限流、群白名单
                 ▼
        会话层（per group_openid+member_openid 的 session）
                 │  上下文裁剪、人设、指令路由
                 ▼
        Agent 执行层（LLM API 或 ACP agent）
                 │  工具调用、权限审批 → 转成 QQ 按钮/文本确认
                 ▼
        回复层（被动回复优先，5 分钟内；主动消息走配额池）
```

要点：
1. **被动回复优先**：只要在 5 分钟内用 msg_id 回复，就不吃主动消息配额
2. **超时降级**：Agent 任务超过 4 分钟，先回"处理中"，再在额度内主动推送结果
3. **去重**：同 `msg_id` 可能重复推送，必须用 `msg_id + msg_seq` 去重
4. **openid 不能反查 QQ 号**：所有用户档案、权限、黑名单都建在 openid 上
5. **IP 白名单 + AppSecret 加密存储**，3 个月轮换

### 5.2 私域向（功能完整、接受风险）

```
小号 QQ ──▶ NapCat(Docker, 6099 WebUI) ──OneBot11 WS(3001, token)──▶ Bridge
                                                                      ├─ 群白名单 / @触发 / 关键词
                                                                      ├─ 会话隔离（群/私聊各自 session）
                                                                      ├─ 进度回传（"收到，开始处理…"→ 结果）
                                                                      └─ Agent（ACP stdio / MCP 工具）
```

### 5.3 最小可跑骨架（OneBot WS 版，Python，约 60 行）

```python
# pip install websockets httpx
import asyncio, json, os, httpx, websockets

NAPCAT_WS = os.getenv("NAPCAT_WS", "ws://127.0.0.1:3001")
TOKEN     = os.getenv("NAPCAT_TOKEN", "")
LLM_URL   = os.getenv("LLM_URL", "https://api.deepseek.com/chat/completions")
LLM_KEY   = os.getenv("LLM_KEY", "")
GROUP_ALLOW = set(filter(None, os.getenv("GROUP_ALLOW", "").split(",")))  # 群号白名单

async def ask_llm(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(LLM_URL, headers={"Authorization": f"Bearer {LLM_KEY}"},
                         json={"model": "deepseek-chat",
                               "messages": [{"role": "user", "content": prompt}]})
        return r.json()["choices"][0]["message"]["content"]

async def handle(ws, ev: dict):
    if ev.get("post_type") != "message":
        return
    mt = ev.get("message_type")
    if mt == "group":
        if str(ev["group_id"]) not in GROUP_ALLOW:
            return
        if not ev.get("self_id") or f"[CQ:at,qq={ev['self_id']}]" not in ev.get("raw_message", ""):
            return
    text = ev.get("raw_message", "").split("]", 1)[-1].strip() if mt == "group" else ev.get("raw_message", "").strip()
    if not text:
        return
    reply = await ask_llm(text)
    action = "send_group_msg" if mt == "group" else "send_private_msg"
    key = "group_id" if mt == "group" else "user_id"
    await ws.send(json.dumps({"action": action, "params": {key: ev[key], "message": reply}}))

async def main():
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    async for ws in websockets.connect(NAPCAT_WS, additional_headers=headers):
        try:
            async for raw in ws:
                ev = json.loads(raw)
                asyncio.create_task(handle(ws, ev))
        except Exception as e:
            print("reconnect:", e)
            await asyncio.sleep(3)

asyncio.run(main())
```

生产化必须补：消息去重、per-会话上下文、并发上限、回复延迟抖动、异常兜底、日志与指标。

---

## 6. 踩坑清单（按出现频率排序）

1. **收不到群消息**：官方 —— intents 未含 `1<<25`；协议端 —— 用了正向 WS 但没订事件 / 群不在白名单
2. **连不上 NapCat**：正向/反向 WS 选反了；`access_token` 两边不一致（第一高频）
3. **WebUI 502/无法访问**：端口未放行、`NAPCAT_GID/UID` 未设导致权限问题
4. **回复报错 "消息已过期"**：超过 5 分钟被动回复窗口，或同一 msg_id 回过 5 次
5. **回复被风控**：频率超 qpm，或发链接/重复内容
6. **拉不进群**：沙箱环境不支持加群；需先提审上线，或等灰度策略
7. **流式不生效**：官方群消息**不支持**流式，只有私聊可以
8. **Agent 插件突然崩**：宿主升级后 API 改名（见 2.1），必须锁版本 + 关掉内置版插件避免冲突
9. **拿不到 QQ 号**：官方只给 openid，别按 QQ 号设计数据表
10. **token 突然 401**：`getAppAccessToken` 返回的凭证约 7200s 过期，未做自动刷新
11. **NapCat 掉线**：账号被风控标记，换号比换框架有效

---

## 7. 合规与安全红线

- 官方路线也要守：不诱导分享、不采集用户隐私、不发送违规内容；AppID/AppSecret 不上 Git、不截图外传
- 协议端路线：违反 QQ 用户协议，随时可能封号；**不要用主号**、不要做对外收费服务
- Agent 侧最小权限：不可信来源（群聊/陌生私聊）走"纯 LLM、无工具"通道，只有白名单用户才进入有工具权限的 Agent
- 高危工具（发消息、删文件、执行命令）加人工审批，QQ 侧可通过按钮做"允许一次/始终允许/拒绝"
- 保留交互日志，便于事后审计与平台整改

---

## 8. 建议的落地节奏

| 阶段 | 动作 | 产出 |
|---|---|---|
| D0 | 明确用途（对外 or 自用）与能力边界 | 路线决策（A 或 B） |
| D1 | 官方：创建机器人 → 沙箱 → 填沙箱群/测试号 | 拿到 AppID/AppSecret，沙箱能收到 @ 消息 |
| D1' | 协议端：Docker 起 NapCat → WebUI 扫码小号 → 配 WS + token | `ws://…:3001` 事件能打通 |
| D2 | 最小 echo 回包，验证收发链路与去重 | 一条消息进出闭环 |
| D3 | 接 LLM，做 per-会话上下文与群白名单 | 能对话 |
| D4 | 接 Agent（ACP/MCP），做进度回传与超时降级 | 能"干活" |
| D5 | 限流、日志、指标、异常告警 | 可长期运行 |
| D6 | 官方：IP 白名单 + 提审 + 发布；协议端：扩群灰度 | 上线 |

---

## 9. 参考

- QQ 机器人官方文档 · Agent 接入：https://bot.q.qq.com/wiki/agent-qqbot/
- 官方文档总入口：https://bot.q.qq.com/wiki/ ；接入指南：https://bot.qq.com/
- 群 @ 消息事件：https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html
- 发送群聊消息（配额）：https://bot.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html
- WebSocket 方式：https://bot.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html
- botpy：https://github.com/tencent-connect/botpy
- OpenClaw QQ 插件：https://github.com/tencent-connect/openclaw-qqbot
- qq-ai-bot（OneBot + ACP 脚手架）：https://github.com/happysnaker/qq-ai-bot
- qq-onebot-mcp：https://lobehub.com/mcp/huliangwei-qq-onebot-mcp
- NapCat 文档：https://napneko.github.io/
- AstrBot：https://astrbot.app/
- ACP vs MCP：https://agent.space/blog/agent-client-protocol-vs-mcp
