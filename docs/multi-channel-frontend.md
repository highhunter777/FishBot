# 多渠道前端（B站 / 小黑盒 等）接入方案

> 前提：个人助手向，单人自用。目标是把同一个 agent 挂到多个平台，而不是每个平台写一套逻辑。

---

## 1. 先分清两类需求，选型完全不同

| | 助手入口 | 运营触点 |
|---|---|---|
| 是什么 | 你会主动去问 AI 的地方 | 你的内容/社区需要被触达的地方 |
| 典型 | QQ 私聊、Telegram、Web 聊天页 | B站评论区、B站直播间、小黑盒社区帖 |
| 交互形态 | 双向对话、长文本、文件 | 单向回复、短文本、高频、公开可见 |
| 内容安全 | 只影响你自己 | **公开可见，说错话是社交事故** |
| 推荐度 | 高 | 中，且要单独设计人格与边界 |

**关键判断**：B站和小黑盒作为"你问 AI 的入口"价值很低（你不会在 B站私信里问技术问题），它们的价值在**内容侧**——自动回评论、直播弹幕互动、发动态。这更像"运营机器人"，别和助手混成一个东西。

如果你的真实想法是"我在哪都能找到我的 agent"，那答案不是接 B站，而是：**Telegram + Web 页面 + QQ**。B站/小黑盒属于"额外触点"，按需再加。

---

## 2. 各渠道可行性矩阵（2026-11 现状）

| 渠道 | 官方开放能力 | 个人可做吗 | 门槛 | 风险 |
|---|---|---|---|---|
| **QQ** | 机器人开放平台（群/私聊/频道） | 可以，沙箱自用 | 低 | 无（官方）；协议端有封号风险 |
| **Telegram** | Bot API 全量（消息/按钮/文件/语音/webhook/长轮询） | **完全开放，零审核** | 极低 | 无 |
| **飞书/钉钉** | 自建应用 + 长连接 | 免费团队即可 | 低 | 无 |
| **Discord** | Bot API 全量 | 完全开放 | 极低 | 无 |
| **Web 聊天页** | 自己写，复用同一个 agent 服务 | 是 | 无 | 无 |
| **B站 直播弹幕** | 直播开放平台（wss 长连） | **个人开发者可入驻** | 中（1-3 工作日审核） | 低（官方通道） |
| **B站 私信/评论** | 内容开放平台需企业资质；私信需邮件报备 | 官方通道基本不行 | 高 | 走 Cookie 非官方 → 风控/验证码 |
| **小黑盒（黑盒语音）** | 官方机器人平台：开发者认证 + token + WS | **可以** | 中（3 工作日审核） | 低（官方通道） |
| **小黑盒 社区（帖子/评论）** | 无公开开放平台 | 只能 Cookie + hkey 签名逆向 | 高 | 签名随前端变化失效，封号 |
| **微信** | 个人号无官方通道；公众号/企业微信有 | 企业微信可自建 | 中 | 个人号方案等于协议端，风险更高 |

结论：**"官方通道 + 个人可注册"的只有 Telegram / Discord / 飞书 / B站直播 / 黑盒语音 / QQ。** 其余都是逆向。

---

## 3. B站：三条路，只有一条适合个人

### 路 1：直播开放平台（推荐，个人可做）

- 入驻 `open-live.bilibili.com` → 选个人开发者 → 身份证 → 1~3 工作日审核，开发密钥邮件发送
- 需要四件套：`access_key_id` / `access_key_secret` / `app_id`（项目 ID）/ `room_owner_auth_code`（主播身份码）
- 接入：wss 长连，收弹幕、礼物、上舰、醒目留言、进房、点赞、开播/下播
- 注意：
  - **个人开发者只能连自己的直播间**
  - 长连**超过 7 天会被静默**（连接还在但不收消息），必须定期重连
  - 触发风控也会静默，会先收到"结束推送"cmd
  - 发弹幕/互动能力属于"互动玩法"，需在创作者服务中心单独建项目，权限另申
- 现成库：`blivedm`（`OpenLiveClient` 走官方，`BLiveClient` 走 web 端零申请）

### 路 2：Cookie 非官方（能做私信/评论，但不稳）

- `SESSDATA` Cookie 调 `api.vc.bilibili.com/web_im/v1/web_im/send_msg` 可发私信；`bilibili-API-collect` 有完整接口清单
- 现成库：`bilibili-api-python`
- 风险：随时可能弹验证码、被限流、账号异常；接口字段随前端改版变
- 适合：**只读**（监听自己私信、抓评论）做提醒，不做主动批量发送

### 路 3：内容开放平台（放弃）

`openhome.bilibili.com` 官方入驻文档写明"**暂未开通个人开发者的申请入驻**"，需营业执照 + 公函盖章；且私信/消息类能力要单独邮件报备 + 运营对接。个人做不了。

---

## 4. 小黑盒：官方有机器人平台，而且能转 OneBot

这是本次调研最实用的发现：**黑盒语音有完整的官方机器人开放平台**，不是只能逆向。

- 申请：`open.xiaoheihe.cn/zh_cn/chat_robot/home` → 立即使用 → 开发者认证（3 个工作日）
- 建号：`bot.xiaoheihe.cn` → 新建机器人（默认已给"查看频道"+"发送消息"权限）→ 拿 token → 复制邀请链接把机器人拉进房间
- 协议（官方）：
  - WS 长连：`wss://chat.xiaoheihe.cn/chatroom/ws/connect`，query 带 `x_os_type=bot` / `chat_os_type=bot` / `chat_version=1.30.0`，header 带 `token`
  - 发私聊：`POST https://chat.xiaoheihe.cn/chatroom/v3/msg/user`
  - 发频道消息/卡片：`POST https://chat.xiaoheihe.cn/chatroom/v2/channel_msg/send`
- SDK / 生态：
  - `heybox-bot`（npm，TypeScript，装饰器写命令：`@bot.command('/hello')`）
  - `HeyBox.Net`（C#）
  - **`@onebots/adapter-heychat`：把黑盒语音转成 OneBot v11 事件与 API** ← 这条最关键
- 场景定位：**黑盒语音是"开黑房/语音房"**，不是帖子社区。适合"开黑房里有个 AI 陪聊/查攻略"，不适合当内容运营。

因为 `adapter-heychat` 能把黑盒语音吐成 OneBot 11，**你已有的 QQ bridge 可以零改动接小黑盒**——只要把 WS 地址和 token 换掉。这一点直接决定了架构选型（见 §5）。

社区侧（帖子/评论/私信）没有开放平台，只能 Cookie + `hkey` 签名（字符集映射+交错+MD5、写接口再加 `_rnd` HMAC-SHA256），算法随前端变，属于"能玩但别依赖"。

---

## 5. 统一架构：渠道无关，能力显式降级

```
        ┌──────────── ChannelPort（每个渠道一个 adapter）────────────┐
        │ QQAdapter(OneBot)   TgAdapter   WebAdapter                │
        │ BiliLiveAdapter     HeyboxAdapter(→OneBot)                │
        └───────────────────────────┬───────────────────────────────┘
                                    ▼ 统一 InboundEvent
                    Router（白名单 / 指令 / 路由到哪个 agent）
                                    ▼
                    Session（按 channel+conversation 隔离）
                                    ▼
                             AgentPort（可换后端）
                                    ▼ 统一 OutboundAction
                    Renderer（按目标渠道 Caps 自动降级）→ 各 adapter 发送
```

### 5.1 两个契约

```python
@dataclass
class InboundEvent:
    channel: str          # "qq" | "tg" | "bili_live" | "heybox" | "web"
    conv_id: str          # 渠道内会话标识（群号/私聊 uid/房间号）
    user_id: str
    text: str
    attachments: list
    is_private: bool
    raw: dict

@dataclass
class OutboundAction:
    kind: Literal["text", "markdown", "image", "file", "voice", "card", "approval"]
    text: str = ""
    media: str | None = None
    options: list[str] | None = None   # approval 的按钮
```

### 5.2 能力位决定降级规则

```python
CAPS = {
  "qq":      dict(max_len=1500, markdown=False, buttons=True,  stream=False, media=True,  proactive=False),
  "tg":      dict(max_len=4000, markdown=True,  buttons=True,  stream=True,  media=True,  proactive=True),
  "web":     dict(max_len=10**6, markdown=True, buttons=True,  stream=True,  media=True,  proactive=True),
  "bili_live":dict(max_len=20,  markdown=False, buttons=False, stream=False, media=False, proactive=False),
  "heybox":  dict(max_len=1000, markdown=False, buttons=False, stream=False, media=True,  proactive=False),
}
```

Renderer 只做三件事：**超长截断/分段、markdown 转纯文本、按钮降级为"回复 y/n"**。

这条设计能救命的例子：B站直播弹幕单条只有 20 字左右，且不能 markdown、不能主动发——如果业务代码里直接拼字符串，你会写出一堆 `if channel == 'bili'`。有了 Caps，业务只管产出 `OutboundAction`。

### 5.3 部署形态

每个渠道一个独立 worker 进程（渠道 SDK 阻塞方式、重连策略、风控节奏完全不同，混在一个进程里互相拖累），共用中间的 Session/Agent 服务（内部 HTTP）。个人向直接用 `docker-compose` 起 3~5 个容器即可。

---

## 6. 落地顺序（按性价比）

| 顺序 | 渠道 | 工作量 | 为什么排这里 |
|---|---|---|---|
| 1 | **Telegram** | 半天 | Bot API 最友好，长轮询不需要公网 IP，inline keyboard 直接能当审批按钮。**做渠道层的最佳练兵场** |
| 2 | **Web 聊天页** | 半天 | 复用同一个 agent 服务，手机上也能用，不受任何平台限制 |
| 3 | **小黑盒（黑盒语音）** | 1 天 | 官方支持，且 `@onebots/adapter-heychat` 能转 OneBot → QQ bridge 复用 |
| 4 | **B站直播弹幕** | 1~2 天 | 个人可入驻，只连自己直播间；注意 7 天静默要重连 |
| 5 | B站私信/评论 | 2 天 | 只能 Cookie，只做"只读提醒"，不做批量发送 |
| 6 | 小黑盒社区 | 不建议 | 签名逆向，收益低（社区不是对话场景） |

如果只想要"多一个入口"，做完 1、2 就够了，收益占全部的 80%。

---

## 7. 代码骨架（渠道适配层）

```python
# channel/base.py
class ChannelPort(Protocol):
    name: str
    caps: dict
    async def run(self) -> AsyncIterator[InboundEvent]: ...
    async def send(self, conv_id: str, action: OutboundAction, reply_to: str | None = None) -> None: ...

# channel/renderer.py
def render(action: OutboundAction, caps: dict) -> list[str]:
    text = action.text
    if action.kind == "markdown" and not caps["markdown"]:
        text = strip_markdown(text)           # 去掉 ** ` # 等
    if action.kind == "approval" and not caps["buttons"]:
        text = f"{text}\n回复 y 允许，n 拒绝"
    return chunk(text, caps["max_len"])       # 按段落切，不按字符硬切

# channel/tg.py（最简，长轮询）
async def run(self):
    async with httpx.AsyncClient() as c:
        offset = 0
        while True:
            r = await c.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 50})
            for u in r.json()["result"]:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if "text" not in msg: continue
                yield InboundEvent("tg", str(msg["chat"]["id"]), str(msg["from"]["id"]),
                                   msg["text"], [], msg["chat"]["type"] == "private", u)
```

B站直播 adapter 用 `blivedm` 的 `OpenLiveClient`，把 `_on_danmaku` 回调转成 `InboundEvent`，注意 **`msg` 只有 20 字**，回弹幕要极短（"已记录"级别），真正结果发到 QQ/Telegram 上。这是很多人的错觉——**直播弹幕不适合承载 AI 回答，只适合做触发**。

---

## 8. 三条经验

1. **渠道越多，越不要把业务写进渠道**。渠道层只做"翻译"，一行业务逻辑都不该有。
2. **每个渠道的"输入形态"差异比输出更大**：QQ 是 @、Telegram 是命令+按钮、直播是弹幕风暴、Web 是自由文本。Router 要按渠道做节流（直播弹幕必须限流，否则一秒十问）。
3. **公开渠道要单独设人格**：B站评论/直播弹幕所有人可见，语气与边界必须和私聊不同，最好用独立的 system prompt 和更严的拒绝策略。

---

## 参考

- B站直播开放平台快速开始：https://open-live.bilibili.com/document/849b924b-b421-8586-3e5e-765a72ec3840
- B站开放平台入驻（写明暂未开放个人）：https://openhome.bilibili.com/doc/4/cbdcee3b-f57e-5c7b-cf27-83892fb811c4
- B站直播能力接入指南（需报备）：https://bilibili.apifox.cn/doc-7499516
- B站私信接口（非官方整理）：https://github.com/schell/bilibili-API-collect
- blivedm：https://github.com/xfgryujk/blivedm
- 黑盒语音机器人帮助文档：https://apifox.com/apidoc/shared/43256fe4-9a8c-4f22-949a-74a3f8b431f5
- 黑盒语音机器人开发平台：https://bot.xiaoheihe.cn/protocol/bot
- heybox-bot（npm）：https://www.npmjs.com/package/heybox-bot
- OneBot 适配器（黑盒语音）：https://socket.dev/npm/package/@onebots/adapter-heychat
