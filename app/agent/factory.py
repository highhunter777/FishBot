"""按配置造 agent 后端。依赖缺失时降级为占位，不阻塞启动。"""
from __future__ import annotations

import logging

from ..config import AgentCfg
from .echo_adapter import EchoAdapter
from .openai_adapter import OpenAiAdapter
from .port import AgentPort
from .unavailable import UnavailableAgent

log = logging.getLogger(__name__)


def build_agent(cfg: AgentCfg) -> AgentPort:
    adapter = (cfg.adapter or "openai").lower()

    if adapter == "openai":
        if not cfg.resolve_key():
            log.warning("agent[%s] 未配置 %s，降级为 echo 演示后端", cfg.name, cfg.api_key_env)
            return EchoAdapter(cfg, reason=f"未配置 {cfg.api_key_env}")
        return OpenAiAdapter(cfg)

    if adapter == "echo":
        return EchoAdapter(cfg)

    if adapter == "acp":
        try:
            from .acp_adapter import AcpAdapter
        except ImportError as exc:            # 没装 SDK 时不要炸掉整个服务
            log.error("ACP SDK 未安装：%s（pip install agent-client-protocol）", exc)
            return UnavailableAgent("ACP SDK 没装，跑一下 pip install agent-client-protocol")
        log.info("agent[%s] 使用 ACP：%s", cfg.name, " ".join(cfg.command or []))
        return AcpAdapter(cfg)

    log.warning("未知 adapter：%s，使用占位后端", adapter)
    return UnavailableAgent(f"未知的 agent 类型：{adapter}")
