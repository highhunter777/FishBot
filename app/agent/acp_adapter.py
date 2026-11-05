"""ACP 后端：把本地会动手的 agent 接进来。

bridge 是 client，agent 是 server（stdio 子进程）。
权限请求会转成 AgentEvent("permission")，由 Router 在聊天里问 y/n。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import AsyncExitStack

from acp import PROTOCOL_VERSION
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    RequestPermissionResponse,
    TextContentBlock,
)
from acp.stdio import spawn_agent_process

from ..config import AgentCfg
from ..credentials import load_dsh_refs
from ..domain import AgentCaps, AgentEvent
from ..permission import Verdict, evaluate
from .port import AgentError

log = logging.getLogger(__name__)

PERMISSION_TIMEOUT_S = 180.0


class _ClientBridge:
    """ACP 的 Client 侧回调，转成 Adapter 内部事件。"""

    def __init__(self, adapter: "AcpAdapter") -> None:
        self.adapter = adapter

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        self.adapter._on_update(session_id, update)

    async def request_permission(self, session_id: str, tool_call, options, **kwargs):
        return await self.adapter._on_permission(session_id, tool_call, options)


class AcpAdapter:
    name = "acp"
    caps = AgentCaps(
        streaming=True,
        tools=True,
        permission=True,
        session_reuse=True,
        resumable=True,
        multimodal_in=False,
    )

    def __init__(self, cfg: AgentCfg) -> None:
        self.cfg = cfg
        self.command = self._resolve_command(list(cfg.command or ["claude-code-acp"]))
        self.cwd = os.path.abspath(cfg.cwd) if cfg.cwd else os.getcwd()
        os.makedirs(self.cwd, exist_ok=True)
        self._stack: AsyncExitStack | None = None
        self._conn = None
        self._sessions: dict[str, str] = {}
        self._fresh: set[str] = set()      # 新建的会话，首轮注入人格与长期记忆
        self._meta: dict[str, dict] = {}   # session_id -> 渠道上下文，用于权限判定
        self._queue: asyncio.Queue | None = None
        self._pending_perm: dict[str, tuple[asyncio.Future, str | None]] = {}

    @staticmethod
    def _resolve_command(cmd: list[str]) -> list[str]:
        """Windows 上 npx/npm 是 .cmd，必须经 cmd.exe 启动。"""
        if os.name != "nt" or not cmd:
            return cmd
        head = cmd[0].lower()
        if head.endswith((".exe", ".com")):
            return cmd
        if head in ("cmd", "powershell", "pwsh"):
            return cmd
        return ["cmd", "/c", *cmd]

    # ---------- 生命周期 ----------

    async def open(self, session_key: str, profile: dict | None = None, meta: dict | None = None) -> str:
        if session_key in self._sessions:
            self._meta[self._sessions[session_key]] = meta or {}
            return self._sessions[session_key]

        if self._conn is None:
            await self._spawn()

        # 先试恢复上次的会话（dsh 的 session 持久化在 ~/.dsh/sessions）
        resume_id = (meta or {}).get("resume_session_id")
        if resume_id:
            try:
                await self._conn.resume_session(str(resume_id), self.cwd)
                self._sessions[session_key] = str(resume_id)
                self._meta[str(resume_id)] = meta or {}
                log.info("ACP 会话已恢复：%s -> %s", session_key, resume_id)
                return str(resume_id)
            except Exception as exc:
                log.warning("恢复会话失败（%s），改为新建：%s", resume_id, exc)

        resp = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
        self._sessions[session_key] = resp.session_id
        self._meta[resp.session_id] = meta or {}
        self._fresh.add(resp.session_id)
        modes = getattr(resp, "modes", None)
        log.info("ACP 会话建立：%s -> %s（模式：%s）", session_key, resp.session_id, modes)
        return resp.session_id

    @staticmethod
    def _compose(messages: list[dict] | None, text: str) -> str:
        """ACP 只收一段文本，把人格与长期记忆拼在首轮前面。

        只取 system（人格 + 关于对方 + 摘要），不灌历史 —— 会话内历史由 ACP 自己维护，
        重复塞进去会翻倍烧 token，还会和它的上下文打架。
        """
        if not messages:
            return text
        systems = [m.get("content", "") for m in messages if m.get("role") == "system"]
        context = "\n\n".join(part for part in systems if part)
        if not context:
            return text
        return f"<context>\n{context}\n</context>\n\n{text}"

    async def _spawn(self) -> None:
        if not self.command:
            raise AgentError("未配置 agent 启动命令")
        stack = AsyncExitStack()
        env = {**os.environ}
        if self.cfg.dsh_refs:
            refs = load_dsh_refs()
            allow = self.cfg.dsh_refs
            if isinstance(allow, list):
                refs = {k: v for k, v in refs.items() if k in allow}
            env.update(refs)
        env.update(self.cfg.env)
        try:
            conn, _proc = await stack.enter_async_context(
                spawn_agent_process(
                    _ClientBridge(self),
                    self.command[0],
                    *self.command[1:],
                    cwd=self.cwd,
                    env=env,
                )
            )
            await conn.initialize(
                PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="fishbot", version="0.1.0"),
            )
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._conn = conn
        log.info("ACP agent 已启动：%s", " ".join(self.command))

    # ---------- 对话 ----------

    async def prompt(self, session_id: str, text: str, *, messages=None, attachments=None):
        if self._conn is None:
            raise AgentError("ACP 未连接")

        payload = text
        if session_id in self._fresh:
            self._fresh.discard(session_id)
            payload = self._compose(messages, text)
        else:
            # 恢复/复用的会话不会重新注入人格，但访客约束每轮都得带着
            guard = (self._meta.get(session_id) or {}).get("guard")
            if guard:
                payload = f"<context>\n{guard}\n</context>\n\n{text}"

        if attachments:
            paths = [
                str(item["path"])
                for item in attachments
                if item.get("kind") == "image" and item.get("path")
            ]
            if paths:
                payload = (
                    "（用户发来了图片，已经存在本地：" + "、".join(paths)
                    + "。需要时用 read 工具打开看一眼）\n\n" + payload
                )

        self._queue = asyncio.Queue()
        task = asyncio.create_task(self._run_prompt(session_id, payload))
        try:
            while True:
                getter = asyncio.create_task(self._queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    item = getter.result()
                    if item is None:
                        break
                    yield item
                    continue
                getter.cancel()
                while not self._queue.empty():
                    item = self._queue.get_nowait()
                    if item is None:
                        break
                    yield item
                break

            if task.done() and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    yield AgentEvent("error", text=f"agent 出错：{exc}")
        finally:
            if not task.done():
                task.cancel()
            self._queue = None
        yield AgentEvent("done")

    async def _run_prompt(self, session_id: str, text: str) -> None:
        try:
            await self._conn.prompt(session_id, [TextContentBlock(type="text", text=text)])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ACP prompt 失败：%s", exc)
            if self._queue is not None:
                self._queue.put_nowait(AgentEvent("error", text=f"agent 出错：{exc}"))
        finally:
            if self._queue is not None:
                self._queue.put_nowait(None)

    # ---------- 事件映射 ----------

    def _emit(self, event: AgentEvent) -> None:
        if self._queue is not None:
            self._queue.put_nowait(event)

    def _on_update(self, session_id: str, update) -> None:
        kind = getattr(update, "session_update", None)

        if kind == "agent_message_chunk":
            content = getattr(update, "content", None)
            text = getattr(content, "text", None)
            if text:
                self._emit(AgentEvent("text_delta", text=text))
            return

        if kind == "agent_thought_chunk":
            return

        if kind == "tool_call":
            self._emit(
                AgentEvent(
                    "tool_call",
                    data={
                        "title": getattr(update, "title", "") or "",
                        "kind": getattr(update, "kind", "") or "",
                        "status": getattr(update, "status", "") or "",
                    },
                )
            )
            return

        if kind == "tool_call_update":
            status = getattr(update, "status", "") or ""
            if status in ("failed", "error"):
                self._emit(AgentEvent("progress", text="这步没成。"))
            return

        if kind == "plan":
            self._emit(AgentEvent("progress", text="想一下怎么做。"))

    async def _on_permission(self, session_id: str, tool_call, options) -> RequestPermissionResponse:
        allow_id = None
        for option in options or []:
            oid = (getattr(option, "option_id", "") or "").lower()
            name = (getattr(option, "name", "") or "")
            if "allow" in oid or "允许" in name or "同意" in name:
                allow_id = option.option_id
                break
        if allow_id is None and options:
            allow_id = options[0].option_id

        denied = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        title = getattr(tool_call, "title", "") or "执行一个操作"
        kind = getattr(tool_call, "kind", "") or ""
        meta = self._meta.get(session_id, {})

        # 分级判定：只读放行、写操作问、群聊拒绝、只读模式拒绝
        decision = evaluate(
            channel=str(meta.get("channel", "")),
            is_private=bool(meta.get("is_private", False)),
            user_id=str(meta.get("user_id", "")),
            tool=f"{kind} {title}".strip(),
            args=getattr(tool_call, "raw_input", None),
            owner_ids={str(meta.get("user_id"))} if meta.get("is_owner") else set(),
            read_only=self.cfg.read_only,
        )
        log.info("工具权限 [%s] %s -> %s（%s）", kind or "-", title[:50], decision.verdict.value, decision.reason)

        if allow_id is None or decision.verdict is Verdict.DENY:
            return denied
        if decision.verdict is Verdict.ALLOW:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow_id)
            )

        req_id = uuid.uuid4().hex[:8]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_perm[req_id] = (future, allow_id)

        flagged = "（高危）" if decision.reason.startswith("高危") else ""
        self._emit(
            AgentEvent(
                "permission",
                req_id=req_id,
                data={
                    "title": f"{title}{flagged}",
                    "kind": kind,
                    "reason": decision.reason,
                    "options": [getattr(o, "name", "") for o in (options or [])],
                },
            )
        )

        try:
            allow = await asyncio.wait_for(future, timeout=PERMISSION_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            log.info("权限请求超时，默认拒绝：%s", req_id)
            allow = False
        finally:
            self._pending_perm.pop(req_id, None)

        log.info("权限结果 %s -> %s", req_id, "允许" if allow else "拒绝")
        if allow and allow_id:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow_id)
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # ---------- 控制 ----------

    async def approve(self, session_id: str, req_id: str, allow: bool) -> None:
        item = self._pending_perm.get(req_id)
        if item and not item[0].done():
            item[0].set_result(allow)

    async def cancel(self, session_id: str) -> None:
        if self._conn is not None:
            try:
                await self._conn.cancel(session_id)
            except Exception as exc:
                log.debug("取消失败：%s", exc)

    async def close(self, session_id: str) -> None:
        self._sessions = {k: v for k, v in self._sessions.items() if v != session_id}

    async def shutdown(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._conn = None
