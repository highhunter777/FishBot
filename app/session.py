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
from . import stickers
from .config import ROOT, AgentCfg, Config
from .domain import InboundEvent
from .memory import MemoryService
from .recall import Recall
from .store import Store

log = logging.getLogger(__name__)

COMPACT_THRESHOLD = 40     # 超过这么多条就压缩
COMPACT_KEEP = 20          # 保留最近多少条不动
SUMMARY_LIMIT = 800

MEDIA_CTX_TTL_S = 600      # 最近图片的有效期
MEDIA_CTX_LIMIT = 3        # 每个会话留几张

# 出现这些词才值得花一次模型调用去归纳反馈
FEEDBACK_HINTS = (
    "不是", "别", "不要", "以后", "记住", "语气", "格式", "太", "又",
    "说了", "错了", "不喜欢", "换成", "重新",
)

CHAT_PROMPTS = {
    "private": "你是我自己的助手，说话像熟人，自然表达，按内容自己选格式。",
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
        self._media_ctx: dict[str, list[dict]] = {}
        self.recall = Recall(store, cfg) if cfg.memory.recall else None

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
            pairs = [f"{k}={str(v)[:60]}" for k, v in list(profile.items())[:12]]
            extras.append("关于对方：" + "，".join(pairs))
        if ev.is_private:
            rules = self.store.feedback_rules(ev.user_id, scope)
            if rules:
                extras.append(
                    "用户纠正过你，后续务必遵守：\n" + "\n".join(f"- {r}" for r in rules[:5])
                )
        extras.append(f"现在时间：{time.strftime('%Y-%m-%d %H:%M %A')}")
        extras.append(
            "表情：闲聊时可以自然地带 1 个 Unicode emoji（如 😊 🐟 🥺），也可以不用；"
            "看语气自己决定，别每句都堆。汇报、报错、给步骤时不用。"
        )

        recent = ev.raw.get("recent") if isinstance(ev.raw, dict) else None
        if recent and not ev.is_private:
            lines = [
                f"{str(item.get('name') or '群友')[:20]}：{str(item.get('text') or '')[:100]}"
                for item in recent[-12:]
            ]
            extras.append(
                "群聊最近发言（只用来理解上下文，不要逐条回应）：\n" + "\n".join(lines)[:1200]
            )

        has_current_image = any(
            item.get("kind") == "image" and item.get("path") for item in ev.attachments
        )
        media = self._recent_media(ev)
        if media and not has_current_image:
            lines = []
            for item in media:
                note = str(item.get("caption") or "")
                ocr = str(item.get("ocr") or "").replace("\n", " ")[:80]
                detail = note or (f"文字：{ocr}" if ocr else "")
                lines.append(f"- {item['path']}" + (f"（{detail}）" if detail else ""))
            extras.append(
                "最近发来的图片（用户说“刚才那张/这个”时可以直接 read 打开）：\n"
                + "\n".join(lines)
            )

        hint = stickers.prompt_hint()
        if hint:
            extras.append(hint)

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

    def _media_key(self, ev: InboundEvent) -> str:
        return f"{ev.channel}:{ev.conv_id}"

    def _remember_media(self, ev: InboundEvent) -> None:
        items = [
            {
                "path": str(item["path"]),
                "caption": str(item.get("caption") or ""),
                "ocr": str(item.get("ocr") or ""),
            }
            for item in ev.attachments
            if item.get("kind") == "image" and item.get("path")
        ]
        if not items:
            return
        key = self._media_key(ev)
        now = time.monotonic()
        buf = [item for item in self._media_ctx.get(key, []) if now - item["ts"] <= MEDIA_CTX_TTL_S]
        for item in items:
            if not any(saved["path"] == item["path"] for saved in buf):
                buf.append({**item, "ts": now})
        self._media_ctx[key] = buf[-MEDIA_CTX_LIMIT:]

    def _recent_media(self, ev: InboundEvent) -> list[dict]:
        key = self._media_key(ev)
        now = time.monotonic()
        buf = [item for item in self._media_ctx.get(key, []) if now - item["ts"] <= MEDIA_CTX_TTL_S]
        if buf:
            self._media_ctx[key] = buf
        return buf[-MEDIA_CTX_LIMIT:]

    def context(
        self, ev: InboundEvent, agent_cfg: AgentCfg, scope: str = "chat", guard: str = ""
    ) -> list[dict]:
        self._remember_media(ev)
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

    def _spawn(self, coro) -> bool:
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:                     # 没有事件循环（例如同步脚本）
            return False
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def remember(self, ev: InboundEvent, reply: str, scope: str = "chat") -> None:
        prev_reply = self.store.last_assistant(ev.channel, ev.conv_id, scope)
        user_mid = self.store.add_message(ev.channel, ev.conv_id, ev.user_id, "user", ev.text, scope=scope)
        self.store.add_message(ev.channel, ev.conv_id, ev.user_id, "assistant", reply, scope=scope)

        if self.cfg.memory.embedding:
            self._spawn(self._index([(user_mid, ev.text)]))

        if self.memory is None or not self.cfg.memory.enabled:
            self._compact(ev, scope)
            return
        if not self._spawn(self._maintain(ev, reply, scope, prev_reply)):
            self._compact(ev, scope)

    async def _index(self, rows: list[tuple[int, str]]) -> None:
        rows = [(mid, text) for mid, text in rows if str(text or "").strip()]
        if not rows:
            return
        from . import embed

        try:
            vecs = await asyncio.to_thread(
                embed.encode, [text for _, text in rows], self.cfg.memory.embedding_model
            )
            if vecs is None:
                return
            for (mid, _), vec in zip(rows, vecs):
                self.store.set_message_vector(mid, vec.tobytes())
        except Exception as exc:
            log.warning("记忆：向量写入失败 %s", exc)

    async def backfill_vectors(self, limit: int = 3000, batch: int = 32) -> None:
        """启动时给旧消息补向量；没有存量就只预热模型。"""
        if not self.cfg.memory.embedding:
            return
        rows = self.store.messages_without_vectors(limit)
        if not rows:
            from . import embed

            try:
                await asyncio.to_thread(embed.encode, ["预热"], self.cfg.memory.embedding_model)
            except Exception:
                pass
            return
        for i in range(0, len(rows), batch):
            await self._index([(r["id"], r["content"]) for r in rows[i : i + batch]])
        log.info("记忆：补算向量 %d 条", len(rows))

    async def flush(self) -> None:
        """等待后台的记忆维护跑完（测试与进程退出时用）。"""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _maintain(
        self, ev: InboundEvent, reply: str, scope: str, prev_reply: str = ""
    ) -> None:
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

                # 只有像纠正/约束的话才额外花一次调用，避免每条消息都跑
                if prev_reply and any(hint in ev.text for hint in FEEDBACK_HINTS):
                    rules = self.store.feedback_rules(ev.user_id, scope)
                    rule = await self.memory.feedback(ev.text, prev_reply, rules)
                    if rule and self.store.add_feedback(ev.user_id, rule, scope):
                        log.info("反馈：学到 %s", rule)

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
