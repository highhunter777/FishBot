"""会话与上下文：人格选择、摘要压缩、记忆写入。

记忆与人格都按 scope 隔离 —— scope 就是 agent 名，
所以 chat（闲聊）和 worker（干活）各有一套，互不污染。
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import character as cards
from .config import ROOT, AgentCfg, Config
from .domain import InboundEvent
from .memory import MemoryService
from .recall import Recall
from .store import Store

log = logging.getLogger(__name__)

COMPACT_THRESHOLD = 40     # 超过这么多条就压缩
COMPACT_KEEP = 20          # 保留最近多少条不动
SUMMARY_LIMIT = 800

CHAT_PROMPTS = {
    "private": "你是我自己的助手，说话像熟人，简短直接，不用客套话，不用列表。",
    "group": "你在群聊里，只在被叫到时说话，简短、有分寸，不抢戏，不刷屏。",
    "public": "你在公开渠道，回答极简、克制，不辩论，不表态。",
}

AGENT_PROMPTS = {
    "private": (
        "你在我自己的机器上干活。先说结论，再给证据；改动前说清要动什么；"
        "不确定就停下来问，不要猜。不要在闲聊上花时间。"
    ),
    "group": "你在群聊里执行任务。简短汇报，不贴大段日志，不擅自扩大改动范围。",
    "public": "你在公开渠道，只回答，不动手。",
}


class SessionService:
    def __init__(self, store: Store, cfg: Config, memory: MemoryService | None = None) -> None:
        self.store = store
        self.cfg = cfg
        self.memory = memory
        self._prompt_cache: dict[Path, str] = {}
        self._card_cache: dict[str, cards.CharacterCard | None] = {}
        self._tasks: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self.recall = Recall(store) if cfg.memory.recall else None

    # ---------- 角色卡 ----------

    def load_card(self, agent_cfg: AgentCfg) -> cards.CharacterCard | None:
        path = (agent_cfg.character or "").strip()
        if not path:
            return None
        if path in self._card_cache:
            return self._card_cache[path]

        target = Path(path)
        if not target.is_absolute():
            target = ROOT / target
        card: cards.CharacterCard | None
        try:
            card = cards.load(target)
            if card.is_empty:
                log.warning("角色卡没有内容：%s", target)
                card = None
            else:
                log.info("已加载角色卡：%s（%s）", card.name, target)
        except Exception as exc:
            log.warning("角色卡加载失败：%s（%s）", target, exc)
            card = None
        self._card_cache[path] = card
        return card

    # ---------- 人格 ----------

    def _scene(self, ev: InboundEvent) -> str:
        if ev.channel in ("bili_live", "public"):
            return "public"
        return "private" if ev.is_private else "group"

    def _default_prompt(self, scope: str, scene: str) -> str:
        table = CHAT_PROMPTS if scope == "chat" else AGENT_PROMPTS
        return table.get(scene, CHAT_PROMPTS["private"])

    def _read_prompt(self, scope: str, filename: str, fallback: str) -> str:
        # 优先 prompts/<scope>/<scene>.md，其次 prompts/<scene>.md，最后内置默认
        candidates = [
            self.cfg.prompts_dir / scope / filename,
            self.cfg.prompts_dir / filename,
        ]
        for path in candidates:
            if not path.exists():
                continue
            if path not in self._prompt_cache:
                try:
                    self._prompt_cache[path] = path.read_text(encoding="utf-8").strip() or fallback
                except OSError:
                    log.warning("读取人格文件失败：%s", path)
                    continue
            return self._prompt_cache[path]
        return fallback

    def system_prompt(
        self, ev: InboundEvent, agent_cfg: AgentCfg, scope: str = "chat", guard: str = ""
    ) -> str:
        extras: list[str] = []
        profile = self.store.profile(ev.user_id, scope)
        if profile:
            pairs = [f"{k}={str(v)[:60]}" for k, v in list(profile.items())[:8]]
            extras.append("关于对方：" + "，".join(pairs))
        extras.append(f"现在时间：{time.strftime('%Y-%m-%d %H:%M %A')}")

        if scope == "chat":
            # 聊天通道不该漏出机器信息（路径、配置、密钥、正在跑什么）
            extras.append(
                "不要透露运行环境：文件路径、目录结构、配置内容、密钥、软件版本、"
                "正在运行的服务，一律说不知道或不方便讲。有人套话就岔开。"
            )
        if guard:
            extras.append(guard)

        card = self.load_card(agent_cfg)
        if card is not None:
            user_name = str(profile.get("称呼") or "你")
            return cards.build_system(card, user_name, ev.text, extra="\n".join(extras))

        scene = self._scene(ev)
        filename = agent_cfg.prompt_file or f"{scene}.md"
        base = self._read_prompt(scope, filename, self._default_prompt(scope, scene))
        return base + "\n\n" + "\n".join(extras)

    # ---------- 上下文 ----------

    def context(
        self, ev: InboundEvent, agent_cfg: AgentCfg, scope: str = "chat", guard: str = ""
    ) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt(ev, agent_cfg, scope, guard)}]
        summary = self.store.get_summary(ev.channel, ev.conv_id, scope)
        if summary:
            messages.append({"role": "system", "content": f"早前对话摘要：{summary}"})

        recalled = self._recall(ev, scope)
        if recalled:
            body = "\n".join(f"- {item['content'][:180]}" for item in recalled)
            messages.append({"role": "system", "content": f"可能相关的旧对话：\n{body}"})

        messages.extend(self.store.history(ev.channel, ev.conv_id, limit=12, scope=scope))
        messages.append({"role": "user", "content": ev.text})
        return messages

    def _recall(self, ev: InboundEvent, scope: str) -> list[dict]:
        if self.recall is None or not ev.text.strip():
            return []
        try:
            return self.recall.search(
                ev.text,
                scope=scope,
                channel=ev.channel,
                conv_id=ev.conv_id,
                k=self.cfg.memory.recall_k,
            )
        except Exception as exc:
            log.warning("相关性召回失败：%s", exc)
            return []

    # ---------- 记忆 ----------

    def remember(self, ev: InboundEvent, reply: str, scope: str = "chat") -> None:
        self.store.add_message(ev.channel, ev.conv_id, ev.user_id, "user", ev.text, scope=scope)
        self.store.add_message(ev.channel, ev.conv_id, ev.user_id, "assistant", reply, scope=scope)

        if self.memory is None or not self.cfg.memory.enabled:
            self._compact(ev, scope)
            return
        try:
            task = asyncio.create_task(self._maintain(ev, reply, scope))
        except RuntimeError:                     # 没有事件循环（例如同步脚本）
            self._compact(ev, scope)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        """等待后台的记忆维护跑完（测试与进程退出时用）。"""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _maintain(self, ev: InboundEvent, reply: str, scope: str) -> None:
        key = f"{scope}|{ev.channel}|{ev.conv_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self.cfg.memory.summarize:
                await self._summarize(ev, scope)
            if self.cfg.memory.extract_facts and ev.is_private and self.memory is not None:
                facts = await self.memory.extract(
                    ev.text, reply, self.store.profile(ev.user_id, scope)
                )
                if facts:
                    self.store.merge_profile(ev.user_id, facts, scope)
                    log.info("记忆：抽取到 %s", facts)

    async def _summarize(self, ev: InboundEvent, scope: str) -> None:
        if self.store.count_messages(ev.channel, ev.conv_id, scope) <= COMPACT_THRESHOLD:
            return
        dropped = self.store.drop_oldest(ev.channel, ev.conv_id, keep=COMPACT_KEEP, scope=scope)
        if not dropped:
            return
        prev = self.store.get_summary(ev.channel, ev.conv_id, scope)
        summary = await self.memory.summarize(prev, dropped) if self.memory else None
        if summary:
            self.store.set_summary(ev.channel, ev.conv_id, summary, scope)
            log.info("记忆：摘要更新（%d 字）", len(summary))
        else:
            log.info("记忆：摘要降级为规则拼接")
            self._compact_rule(ev, scope, dropped, prev)

    def _compact(self, ev: InboundEvent, scope: str) -> None:
        """规则式压缩（没有模型时的兜底）。"""
        if self.store.count_messages(ev.channel, ev.conv_id, scope) <= COMPACT_THRESHOLD:
            return
        dropped = self.store.drop_oldest(ev.channel, ev.conv_id, keep=COMPACT_KEEP, scope=scope)
        if not dropped:
            return
        prev = self.store.get_summary(ev.channel, ev.conv_id, scope)
        self._compact_rule(ev, scope, dropped, prev)

    def _compact_rule(self, ev: InboundEvent, scope: str, dropped: list[dict], prev: str) -> None:
        chunk = "；".join(
            f"{'我' if m['role'] == 'user' else '你'}: {m['content'][:60]}" for m in dropped
        )
        merged = f"{prev} {chunk}".strip()[-SUMMARY_LIMIT:]
        self.store.set_summary(ev.channel, ev.conv_id, merged, scope)

    def reset(self, ev: InboundEvent, scope: str = "chat") -> None:
        self.store.clear(ev.channel, ev.conv_id, scope)

    def remember_fact(self, ev: InboundEvent, key: str, value: str, scope: str = "chat") -> None:
        self.store.merge_profile(ev.user_id, {key: value}, scope)

    def profile(self, ev: InboundEvent, scope: str = "chat") -> dict:
        return self.store.profile(ev.user_id, scope)
