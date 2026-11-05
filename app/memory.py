"""记忆维护：LLM 摘要 + 自动事实抽取。

两个动作都是"事后"的：不阻塞回复，在后台跑，失败就退回老办法。
用的是 chat 那套模型（便宜、快），不占用 agent 的会话。
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from .config import AgentCfg, Config

log = logging.getLogger(__name__)

SUMMARY_MAX = 600
FACT_MAX_KEYS = 12

SUMMARIZE_PROMPT = """把下面的对话压缩成事实记录，300 字以内，中文。

必须保留：谁要做什么、结论是什么、决定了什么、还有什么没做完、明确说过的偏好。
丢掉：寒暄、重复、过程性废话、情绪。
只输出压缩后的正文，不要加标题、不要解释。

已有摘要（可合并，也可改写）：
{prev}

新对话：
{dialogue}
"""

EXTRACT_PROMPT = """从这轮对话里，提取关于「用户本人」的稳定信息。

只提取长期有效的：称呼/姓名、身份职业、长期偏好、正在做的项目、明确说过的规矩。
不要提取：临时状态（今天累）、一次性问题、助手自己说的话、推测。
最多 3 条。

只输出 JSON 对象，键是中文短语，值是字符串；没有就输出 {{}}。
不要输出 markdown 代码块，不要解释。

已有画像：
{profile}

用户说：{user}
助手答：{reply}
"""


class MemoryService:
    """摘要与抽取。没有可用模型时静默降级。"""

    def __init__(self, cfg: Config, agent_name: str | None = None) -> None:
        self.cfg = cfg
        name = agent_name or (cfg.memory.agent if cfg.memory else "chat")
        self.agent: AgentCfg | None = cfg.agents.get(name)
        self.key = self.agent.resolve_key() if self.agent else ""
        if self.agent and not self.key:
            log.info("memory: %s 没有 key，记忆维护降级为规则式", name)

    @property
    def ready(self) -> bool:
        return bool(self.agent and self.key)

    def _endpoint(self) -> str:
        base = (self.agent.base_url if self.agent else "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    async def _complete(self, system: str, user: str, max_tokens: int = 500) -> str | None:
        if not self.ready:
            return None
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if self.agent.system_in_user:          # 有些网关丢 system 角色
            merged = f"【要求】\n{system}\n\n【内容】\n{user}"
            messages = [{"role": "user", "content": merged}]

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                resp = await client.post(
                    self._endpoint(),
                    headers={"Authorization": f"Bearer {self.key}", **(self.agent.headers or {})},
                    json={
                        "model": self.agent.model,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code >= 400:
                    log.warning("memory: 模型返回 %s", resp.status_code)
                    return None
                data = resp.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            log.warning("memory: 调用失败 %s: %s", type(exc).__name__, exc)
            return None

    # ---------- 摘要 ----------

    async def summarize(self, prev: str, messages: list[dict]) -> str | None:
        if not self.ready or not messages:
            return None
        lines = []
        for m in messages:
            who = "我" if m.get("role") == "user" else "你"
            content = (m.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{who}: {content[:200]}")
        dialogue = "\n".join(lines)
        if not dialogue:
            return None

        text = await self._complete(
            "你是一个会话压缩器。输出精炼的事实记录，不写废话。",
            SUMMARIZE_PROMPT.format(prev=prev or "（无）", dialogue=dialogue),
        )
        if not text:
            return None
        text = re.sub(r"^\s*(摘要[:：])?\s*", "", text).strip()
        return text[:SUMMARY_MAX]

    # ---------- 事实抽取 ----------

    async def extract(self, user_text: str, reply: str, profile: dict) -> dict | None:
        if not self.ready or not user_text.strip():
            return None

        text = await self._complete(
            "你是一个信息抽取器，只输出 JSON。",
            EXTRACT_PROMPT.format(
                profile=json.dumps(profile, ensure_ascii=False) if profile else "（空）",
                user=user_text[:500],
                reply=(reply or "")[:300],
            ),
            max_tokens=300,
        )
        if not text:
            return None

        data = _parse_json(text)
        if not data:
            return None

        clean: dict[str, str] = {}
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, (str, int, float)):
                continue
            k, v = key.strip(), str(value).strip()
            if not k or not v or len(k) > 20 or len(v) > 80:
                continue
            clean[k] = v
            if len(clean) >= FACT_MAX_KEYS:
                break
        return clean or None


def _parse_json(text: str) -> dict | None:
    """容忍模型加代码块或前后废话。"""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
