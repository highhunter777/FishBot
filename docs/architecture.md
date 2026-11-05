# FishBot 架构定稿

> 定位：个人助手（单人自用）。多渠道接入。Agent 后端可替换。
> 本文是结论，不再列选项。前面几份调研文档仅作依据保留。

---

## 一句话

**单进程 asyncio 内核 + 双侧契约**：入口侧统一成 `InboundEvent`（QQ/小黑盒尽量走 OneBot 11，B站/Web 写薄 adapter），出口侧统一成 `AgentPort`（ACP 为主、OpenAI 兼容兜底）；中间只有 Router + Session + SQLite，**不引入消息队列、不上微服务**。

---

## 架构图

```
┌─ 入口（每个渠道一个 adapter task，独立重连、独立限流）────────────────┐
│  QQOfficialAdapter(botpy WS)   QQOneBotAdapter(NapCat)  ← 二选一     │
│  HeyboxAdapter(官方 bot WS)    BiliLiveAdapter(blivedm)             │
│  WebAdapter(FastAPI 页面)      TgAdapter(可选)                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼ InboundEvent(channel, conv_id, user_id, text, ...)
                     Router  白名单 / 指令分发 / 渠道节流 / 场景选后端
                           ▼
                     Session 按 (channel, conv_id, user_id) 取上下文
                           ▼
                  ┌────────┴────────┐
                  ▼                 ▼
          AcpAdapter           OpenAiAdapter
          （会动手的活儿）      （闲聊/快问快答）
                           ▼ AgentEvent 流
                     Renderer  按目标渠道 Caps 降级（markdown/长度/按钮）
                           ▼ OutboundAction
                     Channel.send()
```

---

## 五个决策（含被否方案）

### 1. 入口统一到 `InboundEvent`，QQ 首选 OneBot 11 语义

- QQ 两个实现同接口：**官方通道（默认）** 与 **NapCat（可选后备）**，配置一行切换
- 小黑盒走官方 WS，或经 `@onebots/adapter-heychat` 转 OneBot 后**直接复用 OneBot adapter**
- B站直播、Web 单独写薄 adapter（它们的输入形态根本不是"聊天消息"）

**被否**：为每个平台写一套业务逻辑。**被否**：只用官方通道（群里读不到全量消息、发不了卡片，将来想扩展会被卡死）。

### 2. 单进程，不上队列

个人向 QPS 个位数，asyncio 单进程足够。渠道各自一个 task，重连互不阻塞。

**被否**：Redis Stream / Celery。**什么时候才需要**：同时接 5 个以上群、或单条任务超 10 分钟需要断点续跑时才重构。

### 3. Agent 出口统一到 `AgentPort`，ACP 一等公民

- `AcpAdapter`：接本地会动手的 agent（Claude Code / Gemini CLI / Kimi CLI / 自研 ACP agent）
- `OpenAiAdapter`：普通对话、便宜、快
- Router 按指令路由：`/do ...` → ACP，其余 → OpenAI。**换后端 = 改配置一行**

**被否**：AstrBot 当核心（AGPL + 运行时绑定）。**被否**：只接 OpenAI 兼容（做不了"干活"）。

### 4. 存储只用 SQLite 单文件

WAL 模式，三张表：`message`、`session`、`task`。备份 = 复制一个文件。

**被否**：Postgres。个人向没有第二个写者，也没有并发写压力。

### 5. 部署 = 三个容器

`app`（桥接+agent）/ `napcat`（可选）/ `caddy`（可选，Web 页面与 HTTPS）。全部 `restart: always`。

---

## 目录结构

```
fishbot/
├─ app/
│  ├─ main.py                  # 启动：加载配置 → 起各 adapter task → 起 http
│  ├─ config.py                # pydantic-settings，读 .env + config.yaml
│  ├─ domain.py                # InboundEvent / OutboundAction / AgentEvent / Caps
│  ├─ router.py                # 白名单、指令、渠道节流、选后端
│  ├─ session.py               # 上下文读取、摘要压缩、/new
│  ├─ store.py                 # SQLite：message / session / task
│  ├─ render.py                # markdown 降级、分段、按钮降级
│  ├─ channel/
│  │  ├─ base.py               # ChannelPort
│  │  ├─ qq_official.py        # botpy WS（默认）
│  │  ├─ qq_onebot.py          # NapCat（可选）
│  │  ├─ heybox.py             # 黑盒语音官方 WS
│  │  ├─ bili_live.py          # blivedm OpenLiveClient（只做触发）
│  │  └─ web.py                # FastAPI + 一张 HTML
│  └─ agent/
│     ├─ port.py               # AgentPort + AgentEvent
│     ├─ acp_adapter.py        # 主：会动手
│     ├─ openai_adapter.py     # 备：会说话
│     └─ factory.py            # 按配置造
├─ config/
│  ├─ config.yaml              # 渠道开关、路由、agent 定义
│  └─ prompts/                 # 人格：private.md / group.md / public.md
├─ tests/
├─ docker-compose.yml
├─ requirements.txt
└─ .env.example
```

---

## 两个核心契约

```python
# app/domain.py
from dataclasses import dataclass, field
from typing import Literal

@dataclass(slots=True)
class Caps:
    max_len: int
    markdown: bool
    buttons: bool
    stream: bool
    media: bool
    proactive: bool          # 能否主动发起会话

@dataclass(slots=True)
class InboundEvent:
    channel: str             # qq | qq_onebot | heybox | bili_live | web
    conv_id: str             # 群/私聊/房间/页面会话 id
    user_id: str
    text: str
    is_private: bool
    attachments: list = field(default_factory=list)
    reply_to: str | None = None
    raw: dict = field(default_factory=dict)

@dataclass(slots=True)
class OutboundAction:
    kind: Literal["text", "markdown", "image", "file", "voice", "card", "approval"]
    text: str = ""
    media: str | None = None
    options: list[str] | None = None
```

```python
# app/channel/base.py
class ChannelPort(Protocol):
    name: str
    caps: Caps
    async def run(self) -> AsyncIterator[InboundEvent]: ...
    async def send(self, conv_id: str, action: OutboundAction,
                   reply_to: str | None = None) -> None: ...
```

```python
# app/agent/port.py
class AgentPort(Protocol):
    name: str
    caps: AgentCaps                      # streaming / tools / permission / session_reuse

    async def open(self, session_key: str, profile: dict) -> str: ...
    def prompt(self, session_id: str, text: str,
               attachments: list | None = None) -> AsyncIterator[AgentEvent]: ...
    async def approve(self, session_id: str, req_id: str, allow: bool) -> None: ...
    async def cancel(self, session_id: str) -> None: ...
    async def close(self, session_id: str) -> None: ...
```

`AgentEvent.kind`：`text_delta | message | progress | tool_call | permission | done | error`。

---

## 关键数据流（一条消息的完整路径）

1. adapter 收到消息 → 造 `InboundEvent` → 交给 Router
2. Router：白名单（陌生人丢弃）→ 渠道节流（直播弹幕 1 条/3s）→ 指令判定
3. 先回 `ack`（"收到，处理中…"），避免用户以为没响应
4. Session 取最近 12 轮 + 摘要 → 拼 prompt
5. 选后端（`/do` → ACP，其余 → OpenAI）→ 拿 `AgentEvent` 流
6. 累积文本；遇到 `permission` 事件 → 发 `approval` 动作并挂起等回复
7. 结束后 `Renderer` 按目标渠道 Caps 降级 → 分段落发（段间 300~800ms 抖动）
8. 写 `message` 表；task 表记录耗时与成本

---

## 扩展点对照表（这是本文最重要的部分）

| 变化 | 改哪里 | 改动量 |
|---|---|---|
| 加一个聊天类渠道（飞书/Discord/小黑盒转 OneBot） | `app/channel/` 新增 1 文件 + `config.yaml` 加一行 | 约 200 行 |
| 加一个非聊天渠道（B站弹幕/邮件/定时） | 同上，注意 `caps` 要给对 | 约 100 行 |
| 换 agent 后端（Claude Code → Gemini CLI → 自研） | `config.yaml` 里 `command` 改一行 | 0 行代码 |
| 换模型/降价 | `config.yaml` 里 `model` 改一行 | 0 行代码 |
| 换 QQ 通道（官方 ↔ NapCat） | `config.yaml` 里 `qq.mode` 改一行 | 0 行代码 |
| 换存储（SQLite → Postgres） | 只改 `store.py` 一个文件 | 1 个文件 |
| 加队列（将来量大时） | 新增 `jobs.py`，把 `router → agent` 之间改成生产/消费 | 1 个新模块 |

**判断架构好坏的唯一标准：上面这张表里，每一行的改动是否被限制在一个文件里。** 现在这个架构做到了。

---

## 配置样例

```yaml
channels:
  qq:
    mode: official          # official | onebot | off
  heybox:
    enabled: false
  bili_live:
    enabled: false
  web:
    enabled: true

agents:
  worker:                   # 会动手
    adapter: acp
    command: ["claude-code-acp"]
    workspace: /srv/workspace
    timeout_s: 300
  chat:                     # 会说话
    adapter: openai
    base_url: https://api.deepseek.com
    model: deepseek-chat
    timeout_s: 60

routes:
  - match: { prefix: "/do" }
    agent: worker
  - match: { channel: "bili_live" }
    agent: chat             # 弹幕只用便宜模型且极短
  - match: { default: true }
    agent: chat

limits:
  bili_live: { per_user: "1/3s", max_len: 20 }
  group:     { per_user: "1/5s", max_len: 1500 }
```

---

## 明确不做

- 不做多租户、不做账号池、不做灰度
- 不做 Prometheus/Grafana，日志打文件 + 每日心跳自检就够
- 不做流式（QQ/小黑盒/弹幕都不适合分片刷屏；Web 页保留 SSE 即可）
- 不做插件系统（网关阶段加新渠道就是加个文件）
- 不接微信个人号（风险最高、收益最低）

---

## 演进触发条件

只有出现下面任一情况才升级架构：

| 现象 | 升级动作 |
|---|---|
| 单群日活消息 > 2000 | `router → agent` 之间加 Redis Stream |
| 单任务经常 > 10 分钟且要断点续跑 | task 表加状态机 + 定时扫描 |
| 渠道数 > 6 且互相拖累 | 渠道拆独立进程，中间走内部 HTTP |
| 需要对外开放 | 通道切官方 API + 加限流/审计（回到生产版文档） |

---

## 落地顺序

1. `domain.py` + `store.py` + `render.py`（骨架半天）
2. `OpenAiAdapter` + Web 渠道 → 端到端跑通（半天）
3. QQ 官方渠道接入 → 手机上能用（半天）
4. `AcpAdapter` + `/do` 指令 + 审批（1 天）
5. 按需加小黑盒 / B站直播
6. 加每日心跳自检 + `restart: always`，丢到常开机器
