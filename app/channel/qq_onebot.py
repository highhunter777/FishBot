"""QQ 小号渠道：NapCat / LLOneBot 的 OneBot 11 反向 WebSocket（我们作为客户端）。

配置在 NapCat WebUI 里开「WebSocket 服务器」，端口 3001，设 token，
这里填 ws://127.0.0.1:3001 与同一个 token。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator

import websockets

from ..domain import Caps, InboundEvent, OutboundAction
from ..files import MAX_BYTES, day_dir, download
from ..persona import POKE_REPLIES

log = logging.getLogger(__name__)

SEEN_LIMIT = 500
SEND_TIMEOUT = 20.0
POKE_LIMIT = 3              # 同一会话连回几次后进入冷却
POKE_COOLDOWN_S = 5.0       # 到上限后冷却多久
POKE_BACK_RATE = 0.35       # 回戳对方的概率，其余情况回一句话
GROUP_CTX_LIMIT = 20        # 每个群保留最近多少条被动上下文

# QQ 自带小黄脸的常用 id -> 名字；不在表里就只标记“QQ表情”
FACE_NAMES = {
    "0": "惊讶", "1": "撇嘴", "2": "色", "3": "发呆", "4": "得意",
    "5": "流泪", "6": "害羞", "7": "闭嘴", "8": "睡", "9": "大哭",
    "10": "尴尬", "11": "发怒", "12": "调皮", "13": "呲牙", "14": "微笑",
    "15": "难过", "16": "酷", "17": "冷汗", "18": "抓狂", "19": "吐",
    "20": "偷笑", "21": "可爱", "22": "白眼", "23": "傲慢", "24": "饥饿",
    "25": "困", "26": "惊恐", "27": "流汗", "28": "憨笑", "29": "大兵",
    "30": "奋斗", "31": "咒骂", "32": "疑问", "33": "嘘", "34": "晕",
    "39": "再见", "41": "抠鼻", "42": "鼓掌", "44": "坏笑", "49": "委屈",
    "50": "快哭了", "52": "亲亲", "54": "可怜", "63": "玫瑰", "66": "爱心",
    "67": "心碎", "79": "强", "80": "弱", "81": "握手", "82": "胜利",
}

_CLOSED = object()     # 读循环结束的哨兵


class OneBotChannel:
    name = "qq_onebot"
    caps = Caps(max_len=1500, markdown=False, buttons=False, stream=False, media=True, proactive=True)

    def __init__(
        self,
        ws_url: str,
        access_token: str = "",
        self_id: str | None = None,
        group_requires_at: bool = True,
        allowed_users: set[str] | None = None,
        media_root: str = "",
        transcriber: Callable[[str], str] | None = None,
        ocr: Callable[[str], str] | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.self_id = str(self_id) if self_id else None
        self.group_requires_at = group_requires_at
        self.allowed_users = allowed_users or set()
        self.media_root = media_root            # 图片/语音落到这里（通常是 agent 工作目录）
        self.transcriber = transcriber          # 语音转写：同步函数，放线程里跑
        self.ocr = ocr                          # 图片文字识别：同步函数，放线程里跑
        self._ws = None
        self._inbox: asyncio.Queue | None = None
        self._write_lock = asyncio.Lock()
        self._last_sent: dict[str, str] = {}   # conv_id -> 最近一条自己发的 message_id
        self.connected = False          # 巡检用：WS 是否处于已连接状态
        self._fails = 0                 # 连续重连失败次数
        self._pending: dict[str, asyncio.Future] = {}
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._bg: set[asyncio.Task] = set()     # 后台回戳等轻任务
        self._poke_count: dict[str, int] = {}   # 本轮已回戳次数
        self._poke_until: dict[str, float] = {} # 冷却结束时间
        self._group_ctx: dict[str, list[dict]] = {}   # 群聊被动上下文

    # ---------- 连接 ----------

    async def run(self) -> AsyncIterator[InboundEvent]:
        backoff = 1.0
        while True:
            try:
                headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=20,
                    max_size=16 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._inbox = asyncio.Queue()
                    reader = asyncio.create_task(self._reader(ws))
                    backoff = 1.0
                    self.connected = True
                    self._fails = 0
                    log.info("onebot 已连接：%s", self.ws_url)

                    # 先起读循环，否则 get_login_info 会等不到 echo
                    await self._probe_self()

                    while True:
                        event = await self._inbox.get()
                        if event is _CLOSED:
                            break
                        yield event
                    reader.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fails += 1
                # 降噪：前 3 次每次都报，之后每 10 次报一条
                if self._fails <= 3 or self._fails % 10 == 0:
                    log.warning(
                        "onebot 断开（第 %d 次）：%s，%.0fs 后重连", self._fails, exc, backoff
                    )
            finally:
                self._ws = None
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 20.0)     # 本地 WS，重试便宜，别等太久

    async def prepare(self, event: InboundEvent) -> InboundEvent:
        """下载附件/转写语音。放在 handler 任务里跑，别堵住读循环和后面的文字消息。"""
        if event.attachments and self.media_root:
            return await self._hydrate(event)
        return event

    async def _hydrate(self, event: InboundEvent) -> InboundEvent:
        """把图片段从 url 落到本地供 agent 直接 read；语音段落盘后转成文字。"""
        folder = day_dir(self.media_root, "inbox")
        items: list[dict] = []
        voice_notes: list[str] = []
        ocr_notes: list[str] = []
        for raw in event.attachments:
            entry = dict(raw)
            kind = entry.get("kind")
            if kind == "image":
                path = entry.get("path")
                if path and not Path(str(path)).is_file():
                    path = None     # 容器内路径宿主机看不到
                if not path and entry.get("url"):
                    path = await download(str(entry["url"]), folder, ".jpg", MAX_BYTES)
                if path:
                    entry["path"] = path
                    # 表情包不跑 OCR；普通图片先认字，识别到就直接塞进会话
                    if self.ocr is not None and not entry.get("sticker"):
                        text = await asyncio.to_thread(self.ocr, str(path))
                        if text:
                            entry["ocr"] = text
                            ocr_notes.append(text)
            elif kind == "voice":
                path = entry.get("path")
                if path and not Path(str(path)).is_file():
                    path = None     # NapCat 在容器里，/app/... 这种路径宿主机看不到
                if not path and entry.get("url"):
                    path = await download(str(entry["url"]), folder, ".amr", MAX_BYTES)
                    if path:
                        entry["path"] = path
                if path:
                    text = await self._transcribe(str(path))
                    voice_notes.append(f"[语音] {text}" if text else "[语音]")
            items.append(entry)

        text = event.text
        notes = voice_notes + [f"[图片文字] {t}" for t in ocr_notes]
        if notes:
            note = "\n".join(notes)
            text = f"{text}\n{note}" if text else note
        return replace(event, text=text, attachments=items)

    async def _transcribe(self, path: str) -> str:
        if self.transcriber is None:
            return ""
        try:
            return await asyncio.to_thread(self.transcriber, path)
        except Exception as exc:
            log.warning("语音转写失败：%s", exc)
            return ""

    async def _reader(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if payload.get("echo"):
                    self._resolve(payload)
                    continue
                post_type = payload.get("post_type")
                if post_type == "notice":
                    self._spawn_notice(payload)
                    continue
                if post_type != "message":
                    continue
                event = self._to_event(payload)
                if event is None or self._inbox is None:
                    continue
                self._inbox.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("onebot 读循环结束：%s", exc)
        finally:
            if self._inbox is not None:
                self._inbox.put_nowait(_CLOSED)

    async def _probe_self(self) -> None:
        try:
            resp = await self._call("get_login_info", {})
            data = resp.get("data") or {}
            if data.get("user_id"):
                self.self_id = str(data["user_id"])
                log.info("onebot 登录账号：%s", self.self_id)
        except Exception as exc:
            log.debug("获取登录信息失败：%s", exc)

    # ---------- 事件 ----------

    def _dedup(self, key: str) -> bool:
        now = time.time()
        if key in self._seen:
            return True
        self._seen[key] = now
        while len(self._seen) > SEEN_LIMIT:
            self._seen.popitem(last=False)
        return False

    def _to_event(self, payload: dict) -> InboundEvent | None:
        if payload.get("post_type") != "message":
            return None

        user_id = str(payload.get("user_id", ""))
        if not user_id:
            return None
        if self.self_id and user_id == self.self_id:
            return None

        msg_type = payload.get("message_type")
        segments = payload.get("message")
        if not isinstance(segments, list):
            segments = []

        text_parts: list[str] = []
        at_self = False
        attachments: list = []
        for seg in segments:
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == "text":
                text_parts.append(str(data.get("text", "")))
            elif seg_type == "at":
                if self.self_id and str(data.get("qq")) == self.self_id:
                    at_self = True
            elif seg_type == "face":
                name = str(data.get("name") or FACE_NAMES.get(str(data.get("id", "")), ""))
                text_parts.append(f"[对方发来QQ表情：{name}]" if name else "[对方发来QQ表情]")
            elif seg_type in ("mface", "marketface"):
                summary = str(data.get("summary") or "").strip()
                text_parts.append(f"[对方发来表情包：{summary}]" if summary else "[对方发来表情包]")
                if data.get("url"):
                    attachments.append(
                        {
                            "kind": "image",
                            "url": data.get("url"),
                            "file": data.get("file"),
                            "sticker": True,
                            "summary": summary,
                        }
                    )
            elif seg_type == "image":
                sticker = str(data.get("sub_type") or "") == "1" or str(data.get("summary") or "") == "[动画表情]"
                attachments.append(
                    {
                        "kind": "image",
                        "url": data.get("url"),
                        "file": data.get("file"),
                        "path": data.get("path"),
                        "sticker": sticker,
                        "summary": data.get("summary"),
                    }
                )
                if sticker:
                    text_parts.append("[对方发来表情包]")
            elif seg_type == "record":
                attachments.append(
                    {
                        "kind": "voice",
                        "url": data.get("url"),
                        "file": data.get("file"),
                        "path": data.get("path"),
                    }
                )

        text = "".join(text_parts).strip()
        if not text and attachments:
            # 只发图/表情、不带字时也给模型一个可回应的落点
            if any(item.get("sticker") for item in attachments):
                text = "[对方发来表情包]"
            elif any(item.get("kind") == "image" for item in attachments):
                text = "[对方发来图片]"
        if not text and not attachments:
            return None

        is_private = msg_type == "private"
        if msg_type == "group":
            conv_id = f"g:{payload.get('group_id')}"
        elif msg_type == "private":
            conv_id = f"u:{user_id}"
        else:
            return None

        message_id = str(payload.get("message_id", ""))
        if message_id and self._dedup(f"{conv_id}:{message_id}"):
            log.debug("重复消息，丢弃：%s", message_id)
            return None

        recent = None
        if not is_private:
            # 没被 @ 的群消息也记进缓冲，等下次被叫到时给模型看前文
            recent = list(self._group_ctx.get(conv_id, []))
            self._remember_group(conv_id, payload, text)
            if self.group_requires_at and not at_self:
                return None

        # 空白名单时只服务私聊，群聊一律不理，避免小号在群里被陌生人调用
        if not self.allowed_users and not is_private:
            log.info("白名单为空，忽略群消息：group=%s user=%s", payload.get("group_id"), user_id)
            return None

        raw = {
            "message_id": message_id,
            "time": payload.get("time"),
            "group_id": payload.get("group_id"),
        }
        if recent:
            raw["recent"] = recent

        return InboundEvent(
            channel=self.name,
            conv_id=conv_id,
            user_id=user_id,
            text=text,
            is_private=is_private,
            attachments=attachments,
            reply_to=message_id or None,
            raw=raw,
        )

    def _remember_group(self, conv_id: str, payload: dict, text: str) -> None:
        if not text:
            return
        sender = payload.get("sender") or {}
        name = str(sender.get("card") or sender.get("nickname") or payload.get("user_id") or "群友")
        buf = self._group_ctx.setdefault(conv_id, [])
        buf.append({"name": name[:20], "text": text[:120], "time": payload.get("time")})
        if len(buf) > GROUP_CTX_LIMIT:
            del buf[: len(buf) - GROUP_CTX_LIMIT]

    def _spawn_notice(self, payload: dict) -> None:
        """notice 放后台处理：读循环要留着收 echo，不能在这里 await 发送。"""
        task = asyncio.create_task(self._on_notice(payload))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _on_notice(self, payload: dict) -> None:
        """被戳一戳时随机回一句话。"""
        if payload.get("notice_type") != "notify" or payload.get("sub_type") != "poke":
            return

        user_id = str(payload.get("user_id") or "")
        target_id = str(payload.get("target_id") or "")
        if not user_id or (self.self_id and user_id == self.self_id):
            return
        if self.self_id and target_id and target_id != self.self_id:
            return          # 戳的不是我

        group_id = payload.get("group_id")
        conv_id = f"g:{group_id}" if group_id else f"u:{user_id}"
        if self._dedup(f"poke:{conv_id}:{user_id}:{payload.get('time')}"):
            return

        now = time.monotonic()
        if now < self._poke_until.get(conv_id, 0.0):
            return
        count = self._poke_count.get(conv_id, 0) + 1
        if count >= POKE_LIMIT:
            self._poke_count[conv_id] = 0
            self._poke_until[conv_id] = now + POKE_COOLDOWN_S
        else:
            self._poke_count[conv_id] = count

        # 要么直接戳回去，要么随机回一句话
        if random.random() < POKE_BACK_RATE:
            try:
                if await self.poke(conv_id, user_id):
                    log.info("戳回去：%s %s", conv_id, user_id)
                    return
            except Exception as exc:
                log.warning("戳回去失败：%s", exc)

        try:
            await self.send(
                conv_id,
                OutboundAction("text", random.choice(POKE_REPLIES), at_user=user_id if group_id else ""),
            )
            log.info("回应戳一戳：%s %s", conv_id, user_id)
        except Exception as exc:
            log.warning("回应戳一戳失败：%s", exc)

    # ---------- 发送 ----------

    async def send(self, conv_id: str, action: OutboundAction, reply_to: str | None = None) -> None:
        kind, _, target = conv_id.partition(":")
        if not target:
            log.warning("非法 conv_id：%s", conv_id)
            return

        # 群里先 @ 人，再引用，最后才是正文
        message: list[dict] = []
        if kind == "g" and action.at_user:
            message.append({"type": "at", "data": {"qq": action.at_user}})
        if reply_to:
            message.append({"type": "reply", "data": {"id": reply_to}})

        if action.kind == "voice" and action.media:
            message.append({"type": "record", "data": {"file": self._media_ref(action.media)}})
            if action.text:
                message.append({"type": "text", "data": {"text": action.text}})
        elif action.kind in ("image", "file") and action.media:
            message.append({"type": "image", "data": {"file": self._media_ref(action.media)}})
            if action.text:
                message.append({"type": "text", "data": {"text": action.text}})
        elif action.text:
            message.append({"type": "text", "data": {"text": action.text}})

        if all(seg["type"] in ("at", "reply") for seg in message):
            return          # 只有 @ 和引用、没有正文，不发

        if kind == "g":
            resp = await self._call("send_group_msg", {"group_id": int(target), "message": message})
        else:
            resp = await self._call("send_private_msg", {"user_id": int(target), "message": message})

        message_id = (resp.get("data") or {}).get("message_id")
        if message_id:
            self._last_sent[conv_id] = str(message_id)

    @staticmethod
    def _media_ref(media: str) -> str:
        """本地文件转 base64：NapCat 在容器里，够不到宿主机的路径。"""
        if media.startswith(("base64://", "http://", "https://", "file://")):
            return media
        path = Path(media)
        if path.is_file():
            return "base64://" + base64.b64encode(path.read_bytes()).decode()
        return media

    async def recall(self, conv_id: str) -> bool:
        """撤回这个会话里最后一条自己发的消息。"""
        message_id = self._last_sent.get(conv_id)
        if not message_id:
            return False
        resp = await self._call("delete_msg", {"message_id": message_id})
        if resp.get("retcode") in (0, None):
            self._last_sent.pop(conv_id, None)
            return True
        return False

    async def poke(self, conv_id: str, user_id: str) -> bool:
        """戳一戳。"""
        kind, _, target = conv_id.partition(":")
        if not target or not user_id:
            return False
        params = {"user_id": int(user_id)}
        if kind == "g":
            params["group_id"] = int(target)
        resp = await self._call("send_poke", params)
        return resp.get("retcode") in (0, None)

    async def _call(self, action: str, params: dict) -> dict:
        ws = self._ws
        if ws is None:
            raise RuntimeError("onebot 未连接")

        echo = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            async with self._write_lock:
                await ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
            resp = await asyncio.wait_for(future, timeout=SEND_TIMEOUT)
        finally:
            self._pending.pop(echo, None)

        retcode = resp.get("retcode")
        if retcode not in (0, None):
            log.warning("onebot %s 失败 retcode=%s msg=%s", action, retcode, resp.get("message") or resp.get("wording"))
        return resp

    def _resolve(self, payload: dict) -> None:
        future = self._pending.get(payload["echo"])
        if future and not future.done():
            future.set_result(payload)
