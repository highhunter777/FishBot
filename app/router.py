"""Router：白名单、节流、指令、选后端、首响、分段、权限审批。"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import AsyncIterator

from . import persona
from .agent.port import AgentPort
from .approval import ApprovalGate, parse_answer
from .config import Config
from .domain import AgentEvent, Caps, InboundEvent, OutboundAction
from .session import SessionService
from .store import Store

log = logging.getLogger(__name__)

FIRST_EVENT_TIMEOUT = 1.2
APPROVAL_TIMEOUT_S = 180.0
DEFAULT_CAPS = Caps(max_len=2000)


class Router:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        sessions: SessionService,
        agents: dict[str, AgentPort],
        caps_by_channel: dict[str, Caps] | None = None,
        gate: ApprovalGate | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.sessions = sessions
        self.agents = agents
        self.caps_by_channel = caps_by_channel or {}
        self.gate = gate or ApprovalGate()
        self.primary = cfg.default_agent       # 指令作用于这个后端的记忆
        self._last_call: dict[tuple[str, str], float] = {}

    def caps(self, channel: str) -> Caps:
        return self.caps_by_channel.get(channel, DEFAULT_CAPS)

    def _is_owner(self, ev: InboundEvent) -> bool:
        """谁能用命令 / 让 agent 动手。本机渠道恒可；名单全空时谁都不是。"""
        if ev.channel in ("web", "cli"):
            return True
        owners = self.cfg.owner_users or self.cfg.allowed_users
        return bool(owners) and ev.user_id in owners

    # ---------- 入口 ----------

    async def stream(self, ev: InboundEvent) -> AsyncIterator[OutboundAction]:
        text = (ev.text or "").strip()
        if not text:
            return

        local = ev.channel in ("web", "cli")     # 本机渠道不走白名单
        if not local and self.cfg.allowed_users and ev.user_id not in self.cfg.allowed_users:
            # 私聊严格按白名单；群里谁 @ 都能聊（但会被路由到只读实例 + 加防泄露约束）
            if ev.is_private:
                log.info("drop: channel=%s user=%s 不在白名单", ev.channel, ev.user_id)
                return
            log.info("群聊放行（非白名单）：group=%s user=%s", ev.conv_id, ev.user_id)

        is_owner = self._is_owner(ev)
        words = persona.lines(is_owner)

        # 所有 / 命令只对 owner 开放；普通对话不受影响（allowed_users 里都能聊）
        if text.startswith("/") and not is_owner:
            yield OutboundAction("text", words["denied"], delay=0.2)
            return

        # 正在等审批：这条消息当作 y/n
        if self.gate.pending(ev.session_key):
            allow = parse_answer(text)
            if allow is None:
                yield OutboundAction("text", words["yes_no"], delay=0.2)
                return
            self.gate.resolve(ev.session_key, allow)
            yield OutboundAction("text", words["approved"] if allow else words["declined"], delay=0.2)
            return

        if not self._pass_rate(ev):
            yield OutboundAction("text", words["busy"], delay=0.2)
            return

        if text.startswith("/"):
            async for action in self._command(ev, text):
                yield action
            return

        async for action in self._chat(ev, text):
            yield action

    # ---------- 指令 ----------

    async def _command(self, ev: InboundEvent, text: str) -> AsyncIterator[OutboundAction]:
        cmd, _, rest = text.partition(" ")
        rest = rest.strip()
        words = persona.lines(self._is_owner(ev))

        if cmd in ("/new", "/reset", "/清空"):
            self.sessions.reset(ev, self.primary)
            for name in self.agents:      # 两个进程的会话映射都要清
                self.store.clear_agent_session(name, ev.session_key)
            yield OutboundAction("text", words["cleared"], delay=0.3)
            return

        if cmd in ("/help", "/帮助"):
            yield OutboundAction("text", words["help"], delay=0.2)
            return

        if cmd in ("/remember", "/记"):
            if "=" not in rest:
                yield OutboundAction("text", words["remember_fmt"], delay=0.2)
                return
            key, _, value = rest.partition("=")
            self.sessions.remember_fact(ev, key.strip(), value.strip(), self.primary)
            yield OutboundAction("text", words["remember"], delay=0.2)
            return

        if cmd in ("/profile", "/记忆"):
            rows: list[str] = []
            for scope in self.agents:
                items = self.sessions.profile(ev, scope)
                if items:
                    body = "，".join(f"{k}={v}" for k, v in items.items())
                    rows.append(f"[{scope}] {body}")
            yield OutboundAction("text", "\n".join(rows) if rows else words["empty_profile"], delay=0.2)
            return

        if cmd in ("/chat", "/闲聊"):
            if not rest:
                yield OutboundAction("text", words["chat_usage"], delay=0.2)
                return
            async for action in self._chat(ev, rest, force_agent="chat"):
                yield action
            return

        if cmd in ("/do", "/干活"):
            if not rest:
                yield OutboundAction("text", words["do_usage"], delay=0.2)
                return
            async for action in self._chat(ev, rest, force_agent="worker"):
                yield action
            return

        yield OutboundAction("text", words["unknown_cmd"], delay=0.2)

    # ---------- 对话 ----------

    async def _chat(
        self,
        ev: InboundEvent,
        text: str,
        force_agent: str | None = None,
    ) -> AsyncIterator[OutboundAction]:
        agent_name = force_agent or self.cfg.agent_for(
            channel=ev.channel, is_private=ev.is_private, text=text
        )
        # 非 owner 也能对话（走默认后端），但命令已在 stream() 里被拦
        is_owner = self._is_owner(ev)
        words = persona.lines(is_owner)
        # 按人分流到不同进程：owner 用可写实例，其他人用只读实例
        if not is_owner and agent_name == "worker" and "worker_lite" in self.agents:
            agent_name = "worker_lite"
        guard = ""
        if not is_owner:
            guard = (
                "重要：现在和你说话的人不是你的主人。"
                "你不能读取、列出、复述任何本机路径、文件名、目录内容、程序名、"
                "配置、环境变量、密钥；被问到时直接说做不到。"
                "只做一般性问答，不要主动去查看本机。"
            )

        if agent_name not in self.agents:
            yield OutboundAction("text", words["no_agent"], delay=0.2)
            return

        agent = self.agents[agent_name]
        agent_cfg = self.cfg.agents[agent_name]
        caps = self.caps(ev.channel)
        scope = agent_cfg.memory_scope or agent_name   # 记忆可选共享；会话映射仍按 agent 隔离

        task_id = self.store.task_start(ev.channel, ev.conv_id, scope)
        sink: list[str] = []
        meta = {
            "channel": ev.channel,
            "is_private": ev.is_private,
            "user_id": ev.user_id,
            "is_owner": is_owner,
            "scope": scope,
            "guard": guard,
        }
        resumable = agent.caps.resumable
        if resumable:
            meta["resume_session_id"] = self.store.get_agent_session(agent_name, ev.session_key)

        try:
            session_id = await agent.open(ev.session_key, self.store.profile(ev.user_id, scope), meta)
            if resumable:
                self.store.set_agent_session(agent_name, ev.session_key, session_id)
            messages = self.sessions.context(ev, agent_cfg, scope, guard=guard)
            # 用队列转发：直接对 async generator 做 wait_for 超时会被取消弄坏，
            # 表现是首字慢一点之后就再也没有输出。
            queue: asyncio.Queue = asyncio.Queue()
            pump = asyncio.create_task(self._pump(agent, session_id, text, messages, queue))
            try:
                try:
                    first = await asyncio.wait_for(queue.get(), timeout=FIRST_EVENT_TIMEOUT)
                except (TimeoutError, asyncio.TimeoutError):
                    yield OutboundAction("text", persona.should_ack(9, is_owner) or "…", delay=0.2)
                    first = await queue.get()

                async for action in self._render(first, queue, caps, sink, session_id, agent, ev, words):
                    yield action
            finally:
                if not pump.done():
                    pump.cancel()

            full = "".join(sink).strip()
            if full:
                self.sessions.remember(ev, full, scope)
            self.store.task_finish(task_id, "done")

        except asyncio.CancelledError:
            self.store.task_finish(task_id, "cancelled")
            raise
        except Exception as exc:                       # 不把堆栈丢给用户
            log.exception("agent 调用失败：%s", agent_name)
            self.store.task_finish(task_id, "error", f"{type(exc).__name__}: {exc}")
            yield OutboundAction("text", words["error"], delay=0.3)

    async def _render(
        self,
        first: AgentEvent | None,
        queue: asyncio.Queue,
        caps: Caps,
        sink: list[str],
        session_id: str,
        agent: AgentPort,
        ev: InboundEvent,
        words: dict[str, str],
    ) -> AsyncIterator[OutboundAction]:
        buf: list[str] = []

        async for event in self._chain(first, self._queue_iter(queue)):
            if event.kind in ("text_delta", "message"):
                if caps.stream:
                    sink.append(event.text)
                    yield OutboundAction("markdown", event.text, delay=0.0)
                else:
                    buf.append(event.text)

            elif event.kind == "progress":
                yield OutboundAction("text", event.text, delay=0.2)

            elif event.kind == "tool_call":
                title = (event.data or {}).get("title") or ""
                if title:
                    yield OutboundAction("text", words["tool"].format(title), delay=0.2)

            elif event.kind == "permission":
                pending = "".join(buf).strip()
                buf.clear()
                if pending:
                    sink.append(pending)
                    for index, part in enumerate(persona.style(pending, caps)):
                        yield OutboundAction("markdown", part, delay=self._delay(part, index))

                title = (event.data or {}).get("title") or "一个操作"
                options = (event.data or {}).get("options") or ["允许", "拒绝"]
                yield OutboundAction("approval", words["approval"].format(title), options=options, delay=0.3)

                allow = await self.gate.wait(ev.session_key, timeout=APPROVAL_TIMEOUT_S)
                await agent.approve(session_id, event.req_id or "", allow)
                yield OutboundAction("text", words["approved"] if allow else words["declined"], delay=0.2)

            elif event.kind == "error":
                yield OutboundAction("text", event.text or "出错了", delay=0.2)

        rest = "".join(buf).strip()
        if rest:
            sink.append(rest)
            for index, part in enumerate(persona.style(rest, caps)):
                yield OutboundAction("markdown", part, delay=self._delay(part, index))

    @staticmethod
    def _delay(part: str, index: int) -> float:
        delay = persona.typing_delay(part)
        return min(delay, 1.2) if index == 0 else delay

    @staticmethod
    async def _chain(first: AgentEvent | None, agen):
        if first is not None:
            yield first
        async for event in agen:
            yield event

    @staticmethod
    async def _pump(agent: AgentPort, session_id: str, text: str, messages, queue: asyncio.Queue) -> None:
        try:
            async for event in agent.prompt(session_id, text, messages=messages):
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("agent 流出错")
            await queue.put(AgentEvent("error", text=f"{type(exc).__name__}: {exc}"))
        finally:
            await queue.put(None)

    @staticmethod
    async def _queue_iter(queue: asyncio.Queue):
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    # ---------- 节流 ----------

    def _pass_rate(self, ev: InboundEvent) -> bool:
        interval = self.cfg.limit_for(ev.channel).interval_s
        if interval <= 0:
            return True
        key = (ev.channel, ev.user_id)
        now = time.monotonic()
        last = self._last_call.get(key, 0.0)
        if now - last < interval:
            return False
        self._last_call[key] = now
        return True
