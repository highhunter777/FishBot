# 生产向 · 路线 B 架构推荐（Agent 后端可替换）

> 前提校正：路线 B（NapCat / OneBot 11）本质是**非官方协议**，与"对外公开生产"存在天然冲突。本文的"生产向"指：**私域/内部长期运行、有 SLA 要求、账号资产化运营**。若要对外经营，结论是走官方通道，但下面的架构 80% 可直接复用（见 §7 双通道）。

---

## 1. 推荐结论

一句话：**不要选一个"框架"当核心，选三个可替换的契约。**

| 层 | 推荐 | 为什么可换 |
|---|---|---|
| 通道层 | **NapCat**（Docker，pin 版本），备选 LLOneBot / Lagrange | 三者都吐 OneBot 11，bridge 只认协议不认实现 |
| 接线层 | **自写 bridge**（Python + FastAPI + Redis），或 NoneBot2（MIT）做事件层 | 只做事件翻译、会话、限流，不含 AI 逻辑 |
| Agent 层 | **自建 `AgentPort` 契约 + ACP 一等公民 + OpenAI 兼容兜底** | 换后端只加一个 adapter，业务代码零改动 |

**不建议**把 AstrBot 当核心：它能换模型（OpenAI 兼容 / Anthropic / Gemini / Ollama），但它的 Agent 运行时、插件体系、WebUI 是自己的，把"运行时"换掉等于重写；且 **AGPL-3.0**，对外提供网络服务时有传染性义务。它可以当"参考实现"和"临时验证工具"。

**推荐不建议**直接用 `qq-ai-bot` 当前提：它是 ACP 接线层的很好参考（MIT，带部署矩阵），但"生产向"要的账号池、幂等、限流、灰度它不一定覆盖，抄结构比抄代码划算。

主推语言：**Python 3.12**。理由：ACP 有官方 Python SDK（`pip install agent-client-protocol`，`import acp`）、MCP 官方 Python SDK、AstrBot/NoneBot2 生态都在 Python，运维心智统一。
若团队是前端栈，TypeScript 同样可行（`@agentclientprotocol/sdk` + OneBot WS 也就几百行）。

---

## 2. 推荐拓扑

```
                    ┌─────────────── 可换：账号池 ───────────────┐
   QQ 用户 ─────▶   NapCat#1   NapCat#2   NapCat#3   (Docker, 一账号一容器)
                    └──────┬─────────┬─────────┬───────────────┘
                           │ OneBot 11 WS (token) + 事件上报
                           ▼
                    ┌──────────────────────────────────────────┐
                    │  Channel Adapter（唯一 OneBot 依赖点）    │
                    │  鉴权 / 事件规范化 / 幂等 / 白名单        │
                    └──────────────────┬───────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │  Router（配置驱动，可换后端的关键）       │
                    │  按 群/用户/命令 → 选 agent、人设、限流   │
                    └──────────────────┬───────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │  Session & Job（Postgres + Redis Stream）│
                    │  会话/上下文/任务队列/取消/重试/死信      │
                    └──────────────────┬───────────────────────┘
                                       ▼
        ┌──────────────────────────────┴──────────────────────────────┐
        ▼                        ▼                        ▼
  AcpAgentAdapter         OpenAiAgentAdapter        HttpAgentAdapter
  (Claude Code / Gemini    (云端模型 / 自建推理)      (自研 agent 服务)
   CLI / Kimi CLI / 自研)
        └──────────────────────────────┬──────────────────────────────┘
                                       ▼
                        统一 AgentEvent 流 → 回复整形 → OneBot action
```

关键点：**只有 Channel Adapter 认识 OneBot，只有各自 Adapter 认识后端协议，中间全是自有模型。** 这样两头都能换。

---

## 3. AgentPort 契约（回答"后端可能会换"）

这是整个设计里唯一不能将就的部分。要求：能同时表达"纯模型对话"和"会动手的 agent"。

### 3.1 统一事件模型

```python
# domain/agent.py
from dataclasses import dataclass
from typing import Literal, AsyncIterator, Protocol

EventKind = Literal[
    "text_delta",        # 流式增量文本
    "message",           # 完整一段回复（非流式后端用）
    "progress",          # 进度/心跳（"正在查资料…"）
    "tool_call",         # 要调用工具（可展示给用户）
    "tool_result",       # 工具结果（可选展示）
    "permission",        # 需要用户审批（危险操作）
    "done",              # 结束，带 usage/耗时
    "error",
]

@dataclass(slots=True)
class AgentEvent:
    kind: EventKind
    text: str = ""
    data: dict | None = None     # 工具名/参数/用量/错误码
    req_id: str | None = None    # permission 用，回传审批结果

@dataclass(frozen=True, slots=True)
class Capabilities:
    streaming: bool          # 是否支持 text_delta
    tools: bool              # 是否会发起工具调用
    permission: bool         # 是否需要人工审批
    session_reuse: bool      # 能否复用会话（长上下文）
    multimodal_in: bool      # 能否吃图片/语音
    max_input_chars: int

class AgentPort(Protocol):
    name: str
    caps: Capabilities

    async def open(self, session_key: str, profile: dict) -> str: ...
    async def prompt(self, session_id: str, text: str, attachments: list | None = None) -> AsyncIterator[AgentEvent]: ...
    async def approve(self, session_id: str, req_id: str, allow: bool) -> None: ...
    async def cancel(self, session_id: str) -> None: ...
    async def close(self, session_id: str) -> None: ...
```

Router 侧只按 `caps` 做分支：
- `caps.streaming` → 走"边生成边发送"；否则等 `message` 一次性发
- `caps.permission` → 渲染 QQ 按钮/纯文本确认（"回复 y 允许"）
- 无 `tools` 的后端 → 直接当聊天机器人用，不做工具审批 UI

**这就是"换后端不动业务代码"的全部秘密**：业务读的是能力位，不是具体实现。

### 3.2 三个 Adapter 的实现要点

| Adapter | 协议 | 什么时候用 | 注意 |
|---|---|---|---|
| `AcpAgentAdapter` | ACP（stdio JSON-RPC，agent 是 server） | 要"会干活的 agent"：Claude Code / Gemini CLI / Kimi CLI / 自研 ACP agent | ACP 原生有 `session/request_permission` 与流式 chunk，直接映射到本事件模型；会话初始化时可下发 MCP server 列表，能力扩展走 MCP |
| `OpenAiAgentAdapter` | OpenAI 兼容 HTTP | 纯对话、成本敏感、需要自己控上下文 | 无真流式工具审批；把 tool_calls 当 `tool_call` 事件透出即可 |
| `HttpAgentAdapter` | 自研 HTTP/SSE | 已有内部 agent 平台（如腾讯云 ADP 类） | 只写一次，后面所有自研变更收敛在这里 |

**ACP 优先的硬理由**：它是目前唯一一个"本地 agent 通用标准"，同时标准化了会话、流式、工具调用、**权限审批**四件事。有 ACP 就同时兼容 25+ 个 coding agent，换后端从"改代码"降级为"改配置里的一行命令"。ACP 的会话初始化还会把 MCP server 列表传给 agent，所以工具能力也不需要改代码。

配置示例（换后端 = 改一行）：

```yaml
agents:
  default:
    adapter: acp
    command: ["claude-code-acp"]        # 换成 gemini / kimi / 自研 ACP agent
    workspace: /srv/agent-workspace
    timeout_s: 240
  cheap:
    adapter: openai
    base_url: https://api.deepseek.com
    model: deepseek-chat
    timeout_s: 60

routes:
  - match: { group: "123456789" }        # 某业务群用强 agent
    agent: default
  - match: { group: "*" }                # 其余闲聊走便宜模型
    agent: cheap
```

### 3.3 契约测试（换后端不翻车的保险）

给 `AgentPort` 写一套 conformance test，每个 adapter 必须全过：

1. 正常 prompt 产出至少一个 `text_delta` 或 `message`，最后 `done`
2. 未知输入不挂、返回可读错误
3. `cancel` 后 5s 内必须停止产出
4. 并发 4 个 session 不串话（session 隔离）
5. 工具调用场景：有 `caps.tools` 时必须透出 `tool_call`
6. 权限场景：有 `caps.permission` 时必须能阻塞并接受 `approve`
7. 超时行为符合 `timeout_s`

CI 里跑这套，换后端就是"跑一遍测试"的事。

---

## 4. 生产化必做清单（按重要性）

**通道与账号**
1. **账号池化**：一账号一 NapCat 容器；账号元数据（QQ号、用途、绑定群、状态）入库；掉线/风控时从池里切换，业务无感
2. **版本锁**：镜像用 digest 不用 `latest`；NapCat 升级先在灰度群验证（NTQQ 大版本更新常导致 Hook 失效）
3. **健康探针**：NapCat WS 连接状态 + 事件心跳 + `get_status` 定期探活；异常告警到人

**消息可靠性**
4. **幂等**：`message_id` 去重表（TTL 24h）；OneBot 重投不重复回复
5. **回复整形**：单条长度上限分段、段间 300~800ms 随机延迟、超频合并、禁止链接/重复内容刷屏
6. **限流**：per 群 / per 用户 / 全局三级令牌桶；群级并发上限（一个群同时只跑 1 个 agent 任务，其余排队）
7. **任务化**：agent 调用是长任务 → 进 Redis Stream，worker 独立进程；事件循环不被阻塞
8. **首响与降级**：3s 内必回 ack（"收到，处理中…"）；`timeout_s` 到了先回半成品 + 继续后台跑；后端挂了回固定话术并告警

**安全**
9. **CQ 码转义**：用户输入里的 `[CQ:` 必须转义，否则可注入伪造 @/图片/文件段
10. **白名单**：群白名单 + 用户白名单 + 命令级权限；非白名单只允许闲聊（不给工具权限）
11. **权限审批**：高危工具（发消息、写文件、执行命令）一律走 `permission` 事件，QQ 侧用按钮/文本确认
12. **内容安全**：出向文本过敏感词与违规检测（协议端被封的直接原因往往是内容而非频率）

**可观测**
13. **trace_id 贯穿**：`QQ message_id → job_id → agent session_id → 回复 message_id`，一条链路可查
14. 指标：消息量、首响 P95、agent 耗时 P95、失败率、限流触发次数、掉线次数
15. 审计日志：谁在哪个群让 agent 做了什么（工具调用全记录）

---

## 5. 代码骨架

```
fishbot/
├─ app/
│  ├─ main.py                  # FastAPI 生命周期：连 OneBot、起 worker
│  ├─ channel/
│  │  ├─ onebot_ws.py          # 唯一 OneBot 依赖点（可换 LLOneBot）
│  │  ├─ normalize.py          # 事件 → 自有 Event 模型
│  │  └─ sender.py             # 分片/延迟/重试/整形
│  ├─ router.py                # 配置驱动路由 + 白名单 + 限流
│  ├─ sessions.py              # 会话/上下文/幂等
│  ├─ jobs.py                  # Redis Stream 生产/消费
│  ├─ agent/
│  │  ├─ port.py               # AgentPort + AgentEvent + Capabilities
│  │  ├─ acp_adapter.py
│  │  ├─ openai_adapter.py
│  │  └─ factory.py            # 按配置造 adapter
│  └─ observability.py
├─ tests/test_agent_conformance.py
├─ config/agents.yaml
└─ docker-compose.yml          # napcat / bridge / worker / redis / postgres
```

`acp_adapter.py` 的关键映射（示意，具体 API 随 SDK 版本调整）：

```python
class AcpAgentAdapter:
    name = "acp"
    caps = Capabilities(streaming=True, tools=True, permission=True,
                        session_reuse=True, multimodal_in=False, max_input_chars=20000)

    async def prompt(self, session_id, text, attachments=None):
        async for upd in self._sessions[session_id].prompt(text):
            # ACP 的 session/update → 统一事件
            if upd.kind == "agent_message_chunk":
                yield AgentEvent("text_delta", text=upd.text)
            elif upd.kind == "tool_call":
                yield AgentEvent("tool_call", data={"name": upd.title, "args": upd.raw})
            elif upd.kind == "request_permission":
                yield AgentEvent("permission", req_id=upd.req_id, data={"what": upd.title})
        yield AgentEvent("done")

    async def approve(self, session_id, req_id, allow):
        await self._sessions[session_id].respond_permission(req_id, allow)
```

worker 侧只依赖 `AgentPort`：

```python
async def run_job(port: AgentPort, job):
    buf = ""
    async for ev in port.prompt(job.session_id, job.text):
        if ev.kind == "text_delta":
            buf += ev.text
            if len(buf) >= 400:                       # 达到阈值就发一段
                await sender.send(job.reply_target, buf); buf = ""
        elif ev.kind == "permission":
            await sender.ask_approval(job, ev)         # 挂起，等用户回复 y/n
        elif ev.kind == "progress":
            await sender.send(job.reply_target, ev.text)
    if buf:
        await sender.send(job.reply_target, buf)
```

---

## 6. 三条不可忽视的硬风险

1. **封号是账号风险，不是技术风险**。工程侧能做的是账号池 + 养号 + 固定 IP + 频率整形 + 内容过滤，做不到"不封"。生产向要把"封号"写进 SOP：备用号预注册、群迁移话术、数据不存 QQ 侧。
2. **许可证**。AstrBot 是 AGPL-3.0（对外提供网络服务需开源整个修改后的程序）；NoneBot2 / qq-ai-bot 是 MIT。选型前先过一遍法务。
3. **不要做"对外经营"**。协议端做收费服务，是被封号 + 违反平台协议的双重风险。对外就用官方通道。

---

## 7. 更进一步：双通道同源（最稳的生产架构）

把 `AgentPort` 和业务逻辑做成通道无关，前面挂两个 Channel Adapter：

```
官方通道（botpy / WS）─┐
                        ├─▶ 统一 Router ─▶ Session/Job ─▶ AgentPort ─▶ 各 agent 后端
协议端（NapCat/OneBot）─┘
```

收益：
- 政策收紧或账号被封 → 切官方通道，业务代码零改动
- 官方通道做对外/公开群，协议端做内部/需读全量消息的场景
- 同一套 agent、同一套限流、同一套观测

成本：多写一个 Channel Adapter（约 300~500 行）和一张 openid ↔ 内部用户 的映射表。**这是我认为生产向最值得的一笔投入。**

---

## 8. 落地顺序（建议 2 周内跑通）

| 天 | 交付 |
|---|---|
| D1-2 | NapCat Docker + 小号扫码 + bridge 连通，echo 回包 |
| D3-4 | `AgentPort` + `OpenAiAgentAdapter`，会话与上下文，群白名单 |
| D5-6 | `AcpAgentAdapter` + 权限审批 UI（纯文本 y/n 或按钮） |
| D7-8 | 幂等、限流、分片整形、首响 ack、超时降级 |
| D9-10 | Redis Stream 任务化、worker 隔离、指标与告警 |
| D11-12 | 契约测试、灰度群验证、账号池与健康探针 |
| D13-14 | 官方通道 Adapter 预留接口（先不实现），SOP 与应急脚本 |

---

## 9. 参考资料

- ACP Python SDK Quickstart：https://agentclientprotocol.github.io/python-sdk/quickstart/
- ACP PyPI：https://pypi.org/project/agent-client-protocol/
- ACP 权限模型与 MCP 集成：https://www.morphllm.com/agent-client-protocol
- NapCat 文档：https://napneko.github.io/
- qq-ai-bot（OneBot ↔ ACP 接线层参考，MIT）：https://github.com/happysnaker/qq-ai-bot
- AstrBot（AGPL-3.0，参考其调度与面板设计）：https://astrbot.app/
- NoneBot2（MIT，事件层可选）：https://nonebot.dev/
