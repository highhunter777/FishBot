"""OpenAI 兼容后端（DeepSeek / Moonshot / Ollama / vLLM 都能用）。"""
from __future__ import annotations

import json

import httpx

from ..config import AgentCfg
from ..domain import AgentCaps, AgentEvent
from .port import AgentError


class OpenAiAdapter:
    name = "openai"
    caps = AgentCaps(streaming=True, tools=False, permission=False, session_reuse=True)

    def __init__(self, cfg: AgentCfg) -> None:
        self.cfg = cfg
        self.key = cfg.resolve_key()
        self._profile: dict[str, dict] = {}

    @staticmethod
    def _merge_system(messages: list[dict]) -> list[dict]:
        """把 system 设定并入最后一条用户消息。

        有些第三方网关（比如本机这个 4000 转发）会把 system 角色丢掉，
        导致人格完全不生效。合并进用户消息能绕过，代价是每轮多带一份设定。
        """
        systems = [m.get("content", "") for m in messages if m.get("role") == "system"]
        head = "\n\n".join(s for s in systems if s)
        if not head:
            return messages
        rest = [m for m in messages if m.get("role") != "system"]
        if not rest:
            return [{"role": "user", "content": head}]
        last = dict(rest[-1])
        last["content"] = f"【角色与要求】\n{head}\n\n【我的消息】\n{last.get('content', '')}"
        return rest[:-1] + [last]

    def _endpoint(self) -> str:
        base = self.cfg.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    async def open(self, session_key: str, profile: dict | None = None, meta: dict | None = None) -> str:
        self._profile[session_key] = profile or {}
        return session_key

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        messages: list[dict] | None = None,
        attachments: list | None = None,
    ):
        if not self.key:
            raise AgentError("未配置 API Key")
        payload_messages = messages or [{"role": "user", "content": text}]
        if self.cfg.system_in_user:
            payload_messages = self._merge_system(payload_messages)
        body = {
            "model": self.cfg.model,
            "messages": payload_messages,
            "temperature": self.cfg.temperature,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            **(self.cfg.headers or {}),
        }
        timeout = httpx.Timeout(self.cfg.timeout_s, connect=15.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", self._endpoint(), json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "ignore")[:400]
                        yield AgentEvent("error", text=f"模型返回 {resp.status_code}: {detail}")
                        return
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk in ("[DONE]", ""):
                            continue
                        try:
                            obj = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield AgentEvent("text_delta", text=piece)
                    yield AgentEvent("done")
        except httpx.TimeoutException:
            yield AgentEvent("error", text="模型响应超时")
        except httpx.HTTPError as exc:
            yield AgentEvent("error", text=f"网络错误：{type(exc).__name__}")

    async def approve(self, session_id: str, req_id: str, allow: bool) -> None:
        return None

    async def cancel(self, session_id: str) -> None:
        return None

    async def close(self, session_id: str) -> None:
        self._profile.pop(session_id, None)
