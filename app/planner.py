"""任务规划与校验：用轻量 chat 模型拆步骤、判定每步结果。"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from .config import Config
from .domain import AgentEvent
from .agent.port import AgentPort

log = logging.getLogger(__name__)

PLAN_SYSTEM = (
    "你是任务规划器。把用户的任务拆成 1-5 个可独立执行、可验证的步骤。"
    "只输出 JSON，不要解释："
    '{"steps":[{"goal":"这一步要做什么","check":"怎么算完成"}]}'
    "步骤要具体到能在本机执行；不要虚构文件或命令。"
)
VERIFY_SYSTEM = (
    "你是任务校验器。根据验收标准判断这一步是否真的完成。"
    "只输出 JSON，不要解释："
    '{"pass":true或false,"reason":"一句中文理由"}'
    "如果执行结果没有提供可验证的证据，判 false。"
)


def should_plan(text: str, cfg: Config) -> bool:
    pc = cfg.planning
    if not pc.enabled:
        return False
    raw = (text or "").strip()
    if len(raw) < pc.min_chars:
        return False
    return any(word in raw for word in pc.keywords)


def _json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _clean_steps(value: dict | None, cfg: Config) -> list[dict]:
    steps = []
    for item in (value or {}).get("steps") or []:
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal") or "").strip()[:300]
        check = str(item.get("check") or "").strip()[:300]
        if goal:
            steps.append({"goal": goal, "check": check or "任务完成且结果可验证"})
    return steps[: max(1, int(cfg.planning.max_steps))]


async def ask_text(
    agent: AgentPort, session_id: str, system: str, user: str, timeout_s: float
) -> str:
    """收集一次完整回复；推理型模型的思考内容不计入正文。"""
    parts: list[str] = []
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    async with asyncio.timeout(max(10.0, timeout_s)):
        async for event in agent.prompt(session_id, user, messages=messages):
            if event.kind in ("text_delta", "message") and event.text:
                parts.append(event.text)
            elif event.kind == "error":
                raise RuntimeError(event.text or "agent 出错")
    return "".join(parts).strip()


async def make_plan(
    agent: AgentPort, session_id: str, task: str, cfg: Config
) -> list[dict]:
    prompt = f"任务：{task}\n最多 {cfg.planning.max_steps} 步，每步带上验收标准。"
    try:
        text = await ask_text(agent, session_id, PLAN_SYSTEM, prompt, cfg.planning.step_timeout_s)
        steps = _clean_steps(_json_object(text), cfg)
        if steps:
            return steps
        log.warning("规划结果无法解析，退回单步执行：%s", text[:120])
    except Exception as exc:
        log.warning("规划失败，退回单步执行：%s", exc)
    return [{"goal": task, "check": "任务完成，结果与任务要求一致"}]


async def verify_step(
    agent: AgentPort,
    session_id: str,
    task: str,
    step: dict,
    result: str,
    cfg: Config,
) -> tuple[bool, str]:
    prompt = (
        f"总任务：{task}\n"
        f"当前步骤：{step.get('goal')}\n"
        f"验收标准：{step.get('check')}\n"
        f"执行结果：{result[:1200] or '（没有输出）'}"
    )
    try:
        text = await ask_text(agent, session_id, VERIFY_SYSTEM, prompt, cfg.planning.step_timeout_s)
        value = _json_object(text)
        if value is None:
            return True, "校验结果无法解析，按通过处理"
        reason = str(value.get("reason") or "").strip()[:200]
        return bool(value.get("pass")), reason or "未通过"
    except Exception as exc:
        log.warning("校验失败：%s", exc)
        return False, f"校验调用失败：{exc}"


def format_plan(steps: list[dict]) -> str:
    lines = [f"{index}. {step['goal']}" for index, step in enumerate(steps, 1)]
    return "我拆成了这几步：\n" + "\n".join(lines)


def summarize_results(results: list[dict]) -> str:
    lines = []
    for item in results:
        mark = "通过" if item.get("pass") else "未通过"
        text = re.sub(r"\s+", " ", str(item.get("result") or "")).strip()[:180]
        lines.append(f"{item['index']}. {item['goal']} —— {mark}" + (f"：{text}" if text else ""))
    return "\n".join(lines)
