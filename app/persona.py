"""类人化：节奏、分段、去 AI 味。

这一层不产生内容，只决定「怎么发」。
"""
from __future__ import annotations

import random
import re

from .domain import Caps

CPS = 5.0                 # 中文阅读/打字速度，字/秒
DELAY_CAP = 4.0           # 单条最大停顿
MIN_DELAY = 0.6

# 机械口吻的黑名单，后处理兜底
BANNED_PHRASES = (
    "首先，", "首先,", "其次，", "最后，", "综上所述", "总的来说", "总之，",
    "希望对你有所帮助", "希望对你有帮助", "作为一个AI", "作为一个 AI",
    "根据您的问题", "需要注意的是，", "以下是", "好的，", "当然可以",
)
BANNED_PREFIX = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s+", re.M)

ACK_MAX_ETA = 1.2         # 预计超过这个秒数才值得先吭一声

# 系统台词：对主人一套（女仆口吻），对别人一套（中性，绝不称主人）
OWNER_LINES = {
    "ack": ("让我看看喵", "稍等喵", "嗯…我查一下喵", "……", "马上喵"),
    "new": "都清干净了喵。",
    "remember": "记好了喵。",
    "remember_fmt": "格式是这样喵：/remember 称呼=老王",
    "do_usage": "要做什么呀主人？比如 /do 帮我看看这个报错喵",
    "chat_usage": "想聊什么喵？直接说就行。",
    "help": (
        "主人想让我做什么呀喵？\n\n"
        "想聊天——直接说就好\n"
        "想让我动手——/do 后面接要做的事\n"
        "嫌我记太多——/new 清一清\n"
        "想让我记着——/remember 键=值\n"
        "看我记了啥——/profile"
    ),
    "denied": "这个得主人来喵。",
    "unknown_cmd": "没这个指令喵，/help 看看？",
    "yes_no": "回 y 或者 n 就好喵。",
    "approved": "好，继续喵。",
    "declined": "行，不动了喵。",
    "tool": "在弄{}",
    "approval": "要动这个：{}",
    "busy": "等一下下喵，还在忙。",
    "error": "这一步卡住了…等下再试试喵。",
    "no_agent": "这个我暂时没接上喵。",
    "empty_profile": "还没记什么呢喵。",
    "cleared": "清了喵。",
}

GUEST_LINES = {
    "ack": ("我看一下喵", "稍等喵", "……", "嗯，我查查喵"),
    "new": "已经清空了喵。",
    "remember": "记下了喵。",
    "remember_fmt": "格式是这样喵：/remember 键=值",
    "do_usage": "/do 后面接想让我做的事喵",
    "chat_usage": "想聊什么喵？",
    "help": "我能陪你聊天喵。想让我动手做事，得主人来。",
    "denied": "这个我做不了喵，跟我说说话就好。",
    "unknown_cmd": "没这个指令喵。",
    "yes_no": "回 y 或者 n 就好喵。",
    "approved": "好，继续喵。",
    "declined": "行，不动了喵。",
    "tool": "在弄{}",
    "approval": "要动这个：{}",
    "busy": "等一下下喵，还在忙。",
    "error": "这一步卡住了…等下再试试喵。",
    "no_agent": "这个我暂时没接上喵。",
    "empty_profile": "还没记什么呢喵。",
    "cleared": "清了喵。",
}


def lines(is_owner: bool) -> dict[str, str]:
    return OWNER_LINES if is_owner else GUEST_LINES


def typing_delay(text: str, cps: float = CPS, cap: float = DELAY_CAP) -> float:
    """按字数估算"打这段字"的时间，带抖动。"""
    base = len(text) / max(cps, 1)
    return min(MIN_DELAY + base * 0.35, cap) + random.uniform(0, 0.6)


def should_ack(eta_s: float, is_owner: bool = True) -> str | None:
    if eta_s <= ACK_MAX_ETA:
        return None
    return random.choice(lines(is_owner)["ack"])


def de_ai(text: str) -> str:
    """去掉最扎眼的 AI 腔。只做低风险的机械替换，不改变语义。"""
    for phrase in BANNED_PHRASES:
        text = text.replace(phrase, "")
    text = BANNED_PREFIX.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def split_like_human(text: str, max_len: int = 1500, soft_len: int = 300) -> list[str]:
    """按意思块切分，而不是按字符硬切。

    - 短回答（<= soft_len 且不超 max_len）原样一条
    - 长回答优先在空行处切，其次在句末切
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= min(soft_len, max_len):
        return [text]

    blocks: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= max_len:
            buf = candidate
            continue
        if buf:
            blocks.append(buf)
        if len(para) <= max_len:
            buf = para
            continue
        # 单段超长：按句末切
        buf = ""
        for sent in re.split(r"(?<=[。！？!?；;])", para):
            if not sent:
                continue
            if len(buf) + len(sent) <= max_len:
                buf += sent
            else:
                if buf:
                    blocks.append(buf)
                buf = sent
    if buf:
        blocks.append(buf)
    return blocks or [text[:max_len]]


def style(text: str, caps: Caps) -> list[str]:
    """清洗 + 切分 + 给出每段的人为延迟。"""
    cleaned = de_ai(text)
    parts = split_like_human(cleaned, max_len=caps.max_len)
    return parts


def delays_for(parts: list[str]) -> list[float]:
    return [typing_delay(p) for p in parts]
