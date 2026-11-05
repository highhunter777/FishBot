"""QQ 小号渠道：NapCat / LLOneBot 的 OneBot 11 反向 WebSocket（我们作为客户端）。

配置在 NapCat WebUI 里开「WebSocket 服务器」，端口 3001，设 token，
这里填 ws://127.0.0.1:3001 与同一个 token。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from typing import AsyncIterator

import websockets

from ..domain import Caps, InboundEvent, OutboundAction
from ..files import day_dir, download

log = logging.getLogger(__name__)

SEEN_LIMIT = 500
SEND_TIMEOUT = 20.0

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
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.self_id = str(self_id) if self_id else None
        self.group_requires_at = group_requires_at
        self.allowed_users = allowed_users or set()
        self.media_root = media_root            # 图片/语音落到这里（通常是 agent 工作目录）
        self.transcriber = transcriber          # 语音转写：同步函数，放线程里跑
        self._ws = None
        self._inbox: asyncio.Queue | None = None
        self._write_lock = asyncio.Lock()
        self._last_sent: dict[str, str] = {}   # conv_id -> 最近一条自己发的 message_id
        self.connected = False          # 巡检用：WS 是否处于已连接状态
        self._fails = 0                 # 连续重连失败次数
        self._pending: dict[str, asyncio.Future] = {}
        self._seen: OrderedDict[str, float] = OrderedDict()

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
                        if event.attachments and self.media_root:
                            event = await self._hydrate(event)      # 下载 + 语音转写
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
            backoff = min(backoff * 2, 120.0)

    async def _hydrate(self, event: InboundEvent) -> InboundEvent:
        """把图片段从 url 落到本地供 agent 直接 read；语音段落盘后转成文字。"""
        folder = day_dir(self.media_root, "inbox")
        items: list[dict] = []
        voice_notes: list[str] = []
        for raw in event.attachments:
            entry = dict(raw)
            kind = entry.get("kind")
            if kind == "image" and entry.get("url") and not entry.get("path"):
                path = await download(str(entry["url"]), folder, ".jpg")
                if path:
                    entry["path"] = path
            elif kind == "voice":
                path = entry.get("path")
                if not path and entry.get("url"):
                    path = await download(str(entry["url"]), folder, ".amr")
                    if path:
                        entry["path"] = path
                if path:
                    text = await self._transcribe(str(path))
                    voice_notes.append(f"[语音] {text}" if text else "[语音]")
            items.append(entry)

        text = event.text
        if voice_notes:
            note = "\n".join(voice_notes)
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
                if payload.get("post_type") != "message":
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
            elif seg_type == "image":
                attachments.append({"kind": "image", "url": data.get("url"), "file": data.get("file")})
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
        if not text and not attachments:
            return None

        is_private = msg_type == "private"
        if not is_private and self.group_requires_at and not at_self:
            return None

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

        # 空白名单时只服务私聊，群聊一律不理，避免小号在群里被陌生人调用
        if not self.allowed_users and not is_private:
            log.info("白名单为空，忽略群消息：group=%s user=%s", payload.get("group_id"), user_id)
            return None

        return InboundEvent(
            channel=self.name,
            conv_id=conv_id,
            user_id=user_id,
            text=text,
            is_private=is_private,
            attachments=attachments,
            reply_to=message_id or None,
            raw={"message_id": message_id, "time": payload.get("time"), "group_id": payload.get("group_id")},
        )

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

        if action.kind in ("image", "file") and action.media:
            message.append({"type": "image", "data": {"file": action.media}})
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
