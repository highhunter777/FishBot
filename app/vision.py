"""视觉描述：复用 chat 的 OpenAI 兼容接口，给入站图片生成一句中文摘要。

只做一次轻量描述，结果按内容哈希缓存到 media 表；
接口不支持图片或网络失败时静默降级，不影响主聊天链路。
"""
from __future__ import annotations

import logging
import re

import httpx

from .config import Config
from .files import data_url

log = logging.getLogger(__name__)

STICKER_PROMPT = (
    "这是一个聊天表情包。用一句不超过 20 字的中文概括它表达的情绪或含义，"
    "例如「无语」「得意」「疑惑」「委屈」。不要描述画风和构图。只输出这一句。"
)
IMAGE_PROMPT = (
    "描述这张聊天图片的内容和用途，不超过 60 字；图中有文字时优先转述关键文字。"
    "只输出这一句，不要解释。"
)
ANIMATION_PROMPT = (
    "这是一张动图的关键帧拼图，帧按从左到右、从上到下的时间顺序排列。"
    "用一句不超过 40 字的中文概括发生了什么动作或变化。只输出这一句。"
)
STICKER_ANIMATION_PROMPT = (
    "这是一个聊天动图表情包的关键帧拼图，帧按时间顺序排列。"
    "用一句不超过 20 字的中文概括它的动作和情绪，例如「疯狂点头」「无语走开」。"
    "只输出这一句。"
)

PROMPTS = {
    "image": IMAGE_PROMPT,
    "sticker": STICKER_PROMPT,
    "animation": ANIMATION_PROMPT,
    "sticker_animation": STICKER_ANIMATION_PROMPT,
}


class Captioner:
    """视觉描述器：`await captioner(path, kind)`，kind 决定提示词。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg.media
        self.agent = cfg.agents.get(cfg.media.caption_agent) or cfg.agents[next(iter(cfg.agents))]
        self.key = self.agent.resolve_key()
        self._broken = False
        self._warned = False

    def _endpoint(self) -> str:
        base = self.agent.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _clean(self, text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        text = text.strip("\"'“”‘’")
        return text[: self.cfg.caption_max_chars]

    async def __call__(self, path: str, kind: str = "image") -> str:
        if self._broken or not self.key:
            return ""
        url = data_url(path)
        if not url:
            return ""

        prompt = PROMPTS.get(kind) or IMAGE_PROMPT
        body = {
            "model": self.agent.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "stream": False,
            "max_tokens": 2000,    # 推理型模型会先花 token 思考，给足余量（实测约 950）
        }
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            **(self.agent.headers or {}),
        }
        timeout = httpx.Timeout(self.cfg.caption_timeout_s, connect=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._endpoint(), json=body, headers=headers)
                if resp.status_code in (400, 401, 403, 404, 415, 422):
                    self._broken = True
                    log.warning("视觉描述接口不可用（%s），本次运行停用描述", resp.status_code)
                    return ""
                if resp.status_code >= 400:
                    log.warning("视觉描述失败：%s", resp.status_code)
                    return ""
                data = resp.json()
        except httpx.TimeoutException:
            log.warning("视觉描述超时（%s）", self.cfg.caption_timeout_s)
            return ""
        except (httpx.HTTPError, ValueError) as exc:
            if not self._warned:
                self._warned = True
                log.warning("视觉描述失败：%s", type(exc).__name__)
            return ""

        choices = data.get("choices") or []
        message = (choices[0] if choices else {}).get("message") or {}
        return self._clean(message.get("content") or "")
