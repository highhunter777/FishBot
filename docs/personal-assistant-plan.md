# 个人助手向方案（单人自用，功能优先）

> 上两份文档是"对外服务"的思路，本份把工程假设整体降一档：**单用户、没有 SLA、没有多租户、挂了重启就行**。

---

## 1. 先做一个判断：你可能根本不需要 NapCat

个人助手和对外服务最大的区别是：**你只需要私聊，不需要读取群里的全部消息。**

| 你的用法 | 需要什么通道 |
|---|---|
| 私聊问问题、让它干活、发图片/语音/文件 | **官方通道就够**（沙箱阶段自己就是测试成员，能一直用） |
| 群里 @它 才回 | 官方通道够 |
| 要它读群里所有消息、自己判断该不该接话 | 才必须 NapCat |
| 要发任意卡片、改群名片、批量管理群 | 才必须 NapCat |
| 想让它像个"真人小号"在群里存在 | 才必须 NapCat |

官方通道被诟病的三件事，对个人助手基本不成立：
- "群消息收不全" —— 你只要 @ 就够了
- "要企业认证" —— 个人主体可以申请，沙箱自用不受影响
- "主动消息配额" —— 一天几十条足够个人用，且回复走被动消息不吃配额

而官方通道对个人助手有三个白送的好处：**不封号、原生 Markdown、命令审批按钮**。

所以：

```
想 30 分钟用上、且不折腾             → 档位 0：官方龙虾 Bot + 现成 Claw 客户端扫码
想要完整控制、要它干活、装在自己机器上 → 档位 2：自写单文件 bridge + 本地 agent
想要插件生态、定时任务、知识库面板     → 档位 1：AstrBot + NapCat（或官方适配器）
```

---

## 2. 三档方案对比

| | 档位 0 零代码 | 档位 1 现成平台 | 档位 2 自建单文件 |
|---|---|---|---|
| 具体 | `q.qq.com/qqbot/openclaw/login.html` 建 Bot → Workbuddy/OpenClaw/QClaw 扫码 | AstrBot（Docker）+ NapCat 或官方适配器 | NapCat（Docker）+ 一个 `bridge.py` |
| 上手 | 10 分钟 | 半天 | 1~2 天 |
| 通道 | 官方（合规、不封号） | 官方或协议端 | 协议端（有封号风险，用小号） |
| 多模态 | 文本/图片/语音/文件全支持 | 支持，语音要配 ASR | 自己接，图片需多模态模型 |
| 定时任务 | 靠宿主 agent 的 cron/skill | 内置 | 自己加 APScheduler |
| 换 agent 后端 | 换客户端即可 | 换 provider，但运行时绑定 | **完全自由**（AgentPort） |
| 改动成本 | 几乎为 0 | 中（AGPL 注意） | 高（但代码都是自己的） |
| 适合 | 想立刻用 | 想要个面板 | 想长期折腾/有特殊需求 |

**我的推荐：档位 0 先用一周，把"你到底要它干什么"摸清楚，再决定要不要下到档位 2。**
大多数人的真实需求（问问题、改代码、查资料、提醒、发文件）档位 0 就吃下了；只有"要读全群消息 / 要跑本地脚本拿到完整 shell / 要自定义消息形态"才值得自建。

---

## 3. 个人向架构（比生产版砍掉 70%）

```
手机 QQ ──▶ NapCat（小号，Docker，6099 WebUI）──OneBot11 WS(3001)──▶ bridge.py
                                                                      ├─ 私聊白名单（只有你能用）
                                                                      ├─ SQLite：会话记忆 + 任务
                                                                      ├─ 指令路由（/new /img /sh /remind）
                                                                      └─ AgentPort
                                                                           ├─ ACP（本地 agent，能动手）
                                                                           └─ OpenAI 兼容（闲聊、便宜、快）
```

保留的 6 件必需品（其余全部砍掉）：

1. **私聊白名单**：只认你自己的 QQ 号，别人发什么都不理
2. **会话记忆**：SQLite 存最近 N 轮 + 摘要压缩，`/new` 清空
3. **首响 ack**：3 秒内回"收到"，长任务跑完再发结果（QQ 不是流式聊天框，分片刷屏很难看）
4. **回复合并**：攒够一段再一次发，超长自动分段，段间 500ms 抖动
5. **断线重连 + 开机自启**：`restart: always` + 简单 reconnect 循环
6. **危险操作确认**：`/sh`、写文件这类，先回"要执行 X，回复 y 确认"

砍掉的：账号池、多租户、Redis、Postgres、Prometheus、灰度、契约测试、IP 白名单、幂等表（单人场景 OneBot 极少重投，真重了也就多发一句）。

---

## 4. 六个真实痛点与解法

| 痛点 | 解法 |
|---|---|
| 长回复被拆成七八条，刷屏 | 后端返回完整文本后再发；>1500 字按段落切成 2~3 条，不要按字符硬切 |
| 语音消息听不懂 | 官方通道：宿主 agent 自带 ASR；协议端：下载语音文件 → Whisper / 腾讯云 ASR → 文本 |
| 图片看不懂 | 用多模态模型（官方通道传 URL；协议端取 `file` 路径转 base64） |
| 家里没有常开机器 | 旧笔记本 / NAS / 云轻量（2C2G 足够），NapCat 镜像 amd64+arm64 都有 |
| 小号掉线后不知道 | 加一个"每天 9 点给自己发条心跳"的定时任务，收不到就说明挂了 |
| 电脑不在身边但想让它干活 | 这才是自建的核心价值：ACP 接本地 agent，手机发指令 → 电脑执行 → 回结果 |

---

## 5. 单文件骨架（可直接跑）

`bridge.py` + `requirements.txt`（`websockets`, `httpx`），约 100 行，够用：

```python
import asyncio, json, os, random, sqlite3, time
import httpx, websockets

WS   = os.getenv("NAPCAT_WS", "ws://127.0.0.1:3001")
TOK  = os.getenv("NAPCAT_TOKEN", "")
ME   = {int(x) for x in os.getenv("OWNER_QQ", "").split(",") if x}   # 只有这些 QQ 能用
LLM  = os.getenv("LLM_URL", "https://api.deepseek.com/chat/completions")
KEY  = os.getenv("LLM_KEY", "")
MODEL= os.getenv("LLM_MODEL", "deepseek-chat")

db = sqlite3.connect("assistant.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS msg(qq INT, role TEXT, content TEXT, ts INT)")
db.commit()

def history(qq: int, limit: int = 12):
    rows = db.execute("SELECT role,content FROM msg WHERE qq=? ORDER BY ts DESC LIMIT ?", (qq, limit)).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]

def remember(qq: int, role: str, content: str):
    db.execute("INSERT INTO msg VALUES(?,?,?,?)", (qq, role, content, int(time.time())))
    db.execute("DELETE FROM msg WHERE qq=? AND ts < ?", (qq, int(time.time()) - 7 * 86400))
    db.commit()

async def ask(qq: int, text: str) -> str:
    msgs = [{"role": "system", "content": "你是我的私人助手，回答简洁、直接、给可执行结论。"}] + history(qq) + [{"role": "user", "content": text}]
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(LLM, headers={"Authorization": f"Bearer {KEY}"}, json={"model": MODEL, "messages": msgs})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

def split(text: str, size: int = 1200) -> list[str]:
    parts, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > size and buf:
            parts.append(buf); buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    return parts + ([buf] if buf else [])

async def send(ws, qq: int, text: str, group: int | None = None):
    for i, part in enumerate(split(text)):
        if i: await asyncio.sleep(0.5 + random.random() * 0.4)
        action = "send_group_msg" if group else "send_private_msg"
        key = "group_id" if group else "user_id"
        await ws.send(json.dumps({"action": action, "params": {key: group or qq, "message": part}}))

async def handle(ws, ev: dict):
    if ev.get("post_type") != "message":
        return
    qq = int(ev.get("user_id", 0))
    if qq not in ME:                       # 白名单：陌生人一律不理
        return
    text = (ev.get("raw_message") or "").strip()
    group = int(ev["group_id"]) if ev.get("message_type") == "group" else None
    if group and "at" not in str(ev.get("message")):   # 群里只认 @
        return
    if text.startswith("[CQ:at"):                       # 去掉 @ 前缀
        text = text.split("]", 1)[-1].strip()
    if not text:
        return

    if text in ("/new", "/reset"):
        db.execute("DELETE FROM msg WHERE qq=?", (qq,)); db.commit()
        await send(ws, qq, "上下文已清空", group); return

    await send(ws, qq, "收到，处理中…", group)           # 首响 ack
    try:
        reply = await ask(qq, text)
        remember(qq, "user", text); remember(qq, "assistant", reply)
    except Exception as e:
        reply = f"出错了：{type(e).__name__}: {e}"
    await send(ws, qq, reply, group)

async def main():
    headers = {"Authorization": f"Bearer {TOK}"} if TOK else {}
    async for ws in websockets.connect(WS, additional_headers=headers, ping_interval=30):
        try:
            async for raw in ws:
                ev = json.loads(raw)
                asyncio.create_task(handle(ws, ev))
        except Exception as e:
            print("reconnect:", e)
            await asyncio.sleep(3)

asyncio.run(main())
```

跑起来：

```bash
# 1) NapCat（小号扫码）
docker run -d --name napcat --restart=always \
  -p 3000:3000 -p 3001:3001 -p 6099:6099 \
  -e NAPCAT_GID=$(id -g) -e NAPCAT_UID=$(id -u) \
  mlikiowa/napcat-docker:latest
# 打开 http://<ip>:6099/webui ，默认密码 napcat（立刻改），扫码登录

# 2) 在 WebUI 里开「WebSocket 服务器」，端口 3001，设一个 token

# 3) 跑 bridge
pip install websockets httpx
NAPCAT_TOKEN=你的token OWNER_QQ=你的QQ号 LLM_KEY=sk-xxx python bridge.py
```

后续按需加：`/img` 图片理解、`/sh` 本地命令（务必加确认）、APScheduler 定时提醒、ACP adapter 接本地 agent。

---

## 6. 一周路径

| 天 | 干什么 |
|---|---|
| D0 | 档位 0：官方龙虾 Bot + 你已有的 AI 客户端扫码，先用起来，记录"我要它干什么" |
| D1 | 判断是否需要自建（是否要读全群、要跑本地命令、要自定义消息） |
| D2 | 需要则起 NapCat + 小号，跑通 echo |
| D3 | 跑上面的 `bridge.py`，接通 LLM，确认白名单生效 |
| D4 | 加记忆压缩（超 20 轮做摘要）、`/new`、分段策略调优 |
| D5 | 加一个真正想要的功能（定时提醒 / 图片理解 / 本地命令） |
| D6-7 | 丢到常开机器上，配 `systemd` 或 `restart: always`，加每日心跳自检 |

---

## 7. 自用安全三条（别省）

1. **小号 + 白名单**：`OWNER_QQ` 只填自己；群里只认 @；不开任何"陌生人可用"入口
2. **命令确认**：任何能执行 shell / 写文件 / 发消息的能力，先回一句"要执行 X，回复 y"
3. **别把 token 写进代码**：`.env` + `restart: always`，`AppSecret` 别截图

---

## 参考

- 官方龙虾 Bot 快速创建：`q.qq.com/qqbot/openclaw/login.html`
- 官方 Agent 接入文档：https://bot.q.qq.com/wiki/agent-qqbot/
- NapCat：https://napneko.github.io/
- ACP Python SDK：https://agentclientprotocol.github.io/python-sdk/quickstart/
- AstrBot：https://astrbot.app/
