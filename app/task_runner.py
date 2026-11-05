"""多步任务执行：规划 -> 逐步执行 -> 校验 -> 失败重试一次 -> 汇总。"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from . import planner
from .agent.port import AgentPort
from .config import Config
from .domain import AgentEvent, InboundEvent, OutboundAction
from .store import Store

log = logging.getLogger(__name__)


class TaskRunner:
    def __init__(self, cfg: Config, store: Store, agents: dict[str, AgentPort]) -> None:
        self.cfg = cfg
        self.store = store
        self.agents = agents

    async def run(
        self,
        ev: InboundEvent,
        task: str,
        *,
        worker: AgentPort,
        worker_session_id: str,
        messages: list[dict],
        task_id: str,
        verbose: bool,
    ) -> AsyncIterator[OutboundAction]:
        pc = self.cfg.planning
        plan_agent = self.agents.get(pc.planner_agent)
        plan_session = f"plan:{task_id}"
        steps: list[dict]

        if plan_agent is not None:
            try:
                await plan_agent.open(plan_session, {}, {"channel": ev.channel, "user_id": ev.user_id})
                steps = await planner.make_plan(plan_agent, plan_session, task, self.cfg)
            except Exception as exc:
                log.warning("规划器不可用，按单步执行：%s", exc)
                steps = [{"goal": task, "check": "任务完成，结果与任务要求一致"}]
                plan_agent = None
        else:
            steps = [{"goal": task, "check": "任务完成，结果与任务要求一致"}]

        if verbose:
            yield OutboundAction("text", planner.format_plan(steps), delay=0.2)

        self.store.task_step_reset(task_id, steps)
        results: list[dict] = []
        try:
            for index, step in enumerate(steps, 1):
                try:
                    self.store.task_step_start(task_id, index)
                    result = await self._execute(
                        worker,
                        worker_session_id,
                        task,
                        steps,
                        index,
                        step,
                        messages if index == 1 else None,
                        feedback="",
                    )
                    passed, reason = True, "未启用校验"
                    if plan_agent is not None:
                        passed, reason = await planner.verify_step(
                            plan_agent, plan_session, task, step, result, self.cfg
                        )

                    retried = False
                    if not passed and pc.max_retries > 0:
                        retried = True
                        self.store.task_step_start(task_id, index)
                        result = await self._execute(
                            worker,
                            worker_session_id,
                            task,
                            steps,
                            index,
                            step,
                            None,
                            feedback=reason,
                        )
                        if plan_agent is not None:
                            passed, reason = await planner.verify_step(
                                plan_agent, plan_session, task, step, result, self.cfg
                            )

                    self.store.task_step_finish(
                        task_id, index, passed, result, "" if passed else reason
                    )
                    item = {
                        "index": index,
                        "goal": step["goal"],
                        "result": result,
                        "pass": passed,
                        "reason": reason,
                        "retried": retried,
                    }
                    results.append(item)
                    if verbose:
                        text = f"第 {index} 步通过" if passed else f"第 {index} 步未通过：{reason}"
                        yield OutboundAction("text", text, delay=0.2)
                except asyncio.CancelledError:
                    self.store.task_step_finish(task_id, index, False, "任务被取消")
                    raise
                except Exception as exc:
                    self.store.task_step_finish(task_id, index, False, f"执行失败：{exc}")
                    raise
        finally:
            if plan_agent is not None:
                try:
                    await plan_agent.close(plan_session)
                except Exception:
                    log.debug("关闭规划器失败", exc_info=True)

        ok = all(item["pass"] for item in results)
        body = planner.summarize_results(results)
        final = ("做完了：\n" if ok else "有步骤没通过校验：\n") + body
        status = "done" if ok else "unverified"
        self.store.task_finish(task_id, status, None if ok else "部分步骤未通过校验")
        yield OutboundAction("text", final, delay=0.3)

    async def _execute(
        self,
        worker: AgentPort,
        session_id: str,
        task: str,
        steps: list[dict],
        index: int,
        step: dict,
        messages: list[dict] | None,
        feedback: str,
    ) -> str:
        plan_text = "\n".join(
            f"{number}. {item['goal']}" for number, item in enumerate(steps, 1)
        )
        prompt = (
            f"总任务：{task}\n"
            f"整体计划：\n{plan_text}\n\n"
            f"现在只执行第 {index} 步：{step['goal']}\n"
            f"验收标准：{step['check']}\n"
            "完成后用一两句话汇报结果，并给出关键证据（文件、命令输出或数字）；"
            "不要提前执行后续步骤。"
        )
        if feedback:
            prompt += f"\n上次这一步没通过校验：{feedback}\n请针对性地重做。"

        texts: list[str] = []
        evidence: list[str] = []
        async with asyncio.timeout(max(10.0, self.cfg.planning.step_timeout_s)):
            async for event in worker.prompt(session_id, prompt, messages=messages):
                if event.kind in ("text_delta", "message") and event.text:
                    texts.append(event.text)
                elif event.kind == "tool_call":
                    data = event.data or {}
                    item = f"[工具] {str(data.get('title') or '')[:80]} {str(data.get('status') or '')}"
                    output = str(data.get("output") or "").strip()
                    if output:
                        item += f" -> {output[:300]}"
                    evidence.append(item)
                elif event.kind == "error":
                    evidence.append(f"[错误] {event.text[:120]}")
        text = "".join(texts).strip()
        if evidence:
            text = (text + "\n" if text else "") + "\n".join(evidence)
        return text
