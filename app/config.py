"""配置加载：config.yaml + .env，不引入额外依赖。"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: pathlib.Path | None = None) -> None:
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(slots=True)
class AgentCfg:
    name: str
    adapter: str = "openai"          # openai | echo | acp
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "LLM_KEY"
    api_key: str = ""
    temperature: float = 0.8
    timeout_s: float = 120.0
    prompt_file: str = ""            # 空则由场景（私聊/群/公开）决定
    command: list[str] = field(default_factory=list)   # acp：agent 启动命令
    cwd: str = ""                    # acp：agent 的工作目录（权限边界）
    env: dict[str, str] = field(default_factory=dict)  # acp：额外注入的环境变量
    # acp：从 ~/.dsh/.credentials.yaml 的 refs 注入密钥。
    # true = 全部注入；传列表 = 只注入这几个（推荐，减少泄露面）
    dsh_refs: bool | list[str] = False
    read_only: bool = False          # acp：只读模式，写操作直接拒绝（先给低权限就开这个）
    character: str = ""              # 角色卡路径（.png / .json），配了就用它当人格
    memory_scope: str = ""           # 记忆作用域；留空 = 用 agent 名。多个 agent 配同一个值即可共享记忆
    system_in_user: bool = False     # 网关不透传 system 角色时，把设定并入用户消息
    headers: dict[str, str] = field(default_factory=dict)   # 额外请求头（如 x-opencode-session）

    def resolve_key(self) -> str:
        return self.api_key or os.getenv(self.api_key_env, "")


@dataclass(slots=True)
class LimitsCfg:
    interval_s: float = 0.0          # 同一用户两次请求的最小间隔
    max_len: int = 1500


@dataclass(slots=True)
class MemoryCfg:
    enabled: bool = True
    agent: str = "chat"          # 用哪个后端的模型做摘要与抽取
    summarize: bool = True
    extract_facts: bool = True
    recall: bool = True          # 相关性召回（向量优先，失败退回字符 n-gram + IDF）
    recall_k: int = 5
    embedding: bool = True
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    recall_decay_days: float = 30.0   # 越久远的对话召回权重越低


@dataclass(slots=True)
class SttCfg:
    enabled: bool = True
    model: str = "models/whisper-small"   # 相对仓库根目录或绝对路径
    language: str = "zh"


@dataclass(slots=True)
class TtsCfg:
    enabled: bool = True
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    max_chars: int = 200
    proxy: str = ""            # edge-tts 走代理时填，如 http://127.0.0.1:7890


@dataclass(slots=True)
class OcrCfg:
    enabled: bool = True
    min_score: float = 0.6
    max_chars: int = 1200


@dataclass(slots=True)
class ProactiveCfg:
    enabled: bool = True
    poll_s: float = 20.0              # 多久查一次到期提醒
    interrupted_lookback_h: float = 24.0   # 启动时回看多久内的中断任务


@dataclass(slots=True)
class AlertCfg:
    enabled: bool = True
    interval_s: float = 120.0      # 巡检间隔
    threshold: int = 3             # 连续多少次不健康才处理
    restart_napcat: bool = False   # 掉线到阈值时自动重启 NapCat
    napcat_container: str = "napcat"
    restart_cooldown_s: float = 600.0


@dataclass(slots=True)
class Config:
    db_path: pathlib.Path
    channels: dict[str, dict]
    agents: dict[str, AgentCfg]
    routes: list[dict]
    limits: dict[str, LimitsCfg]
    allowed_users: set[str]
    prompts_dir: pathlib.Path
    owner_users: set[str] = field(default_factory=set)   # 能碰 agent 的人；空则等同 allowed_users
    default_agent: str = "chat"
    memory: MemoryCfg = field(default_factory=MemoryCfg)
    alerts: AlertCfg = field(default_factory=AlertCfg)
    stt: SttCfg = field(default_factory=SttCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)
    ocr: OcrCfg = field(default_factory=OcrCfg)
    proactive: ProactiveCfg = field(default_factory=ProactiveCfg)

    def agent_for(self, *, channel: str, is_private: bool, text: str) -> str:
        for rule in self.routes:
            if rule.get("default"):
                return rule.get("agent", self.default_agent)
            if "prefix" in rule and not text.startswith(rule["prefix"]):
                continue
            if "channel" in rule and rule["channel"] != channel:
                continue
            if "private_only" in rule and bool(rule["private_only"]) != is_private:
                continue
            if rule.get("channels") and channel not in rule["channels"]:
                continue
            return rule.get("agent", self.default_agent)
        return self.default_agent

    def limit_for(self, channel: str) -> LimitsCfg:
        return self.limits.get(channel) or self.limits.get("*") or LimitsCfg()


def load_config(path: str | pathlib.Path | None = None) -> Config:
    load_dotenv()
    cfg_path = pathlib.Path(path) if path else ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        raise ConfigError(f"找不到配置文件：{cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    agents: dict[str, AgentCfg] = {}
    for name, item in (raw.get("agents") or {}).items():
        item = dict(item or {})
        item["name"] = name
        agents[name] = AgentCfg(**item)
    if not agents:
        raise ConfigError("config.yaml 至少要定义一个 agent")

    limits = {
        name: LimitsCfg(**dict(item or {}))
        for name, item in (raw.get("limits") or {}).items()
    }

    db_path = pathlib.Path(raw.get("db_path", "data/fishbot.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    prompts_dir = pathlib.Path(raw.get("prompts_dir", "config/prompts"))
    if not prompts_dir.is_absolute():
        prompts_dir = ROOT / prompts_dir

    default_agent = raw.get("default_agent") or next(iter(agents))
    if default_agent not in agents:
        raise ConfigError(f"default_agent 未定义：{default_agent}")

    memory = MemoryCfg(**dict(raw.get("memory") or {}))
    alerts = AlertCfg(**dict(raw.get("alerts") or {}))
    stt = SttCfg(**dict(raw.get("stt") or {}))
    tts = TtsCfg(**dict(raw.get("tts") or {}))
    ocr = OcrCfg(**dict(raw.get("ocr") or {}))
    proactive = ProactiveCfg(**dict(raw.get("proactive") or {}))

    return Config(
        db_path=db_path,
        channels=raw.get("channels") or {},
        agents=agents,
        routes=raw.get("routes") or [],
        limits=limits,
        allowed_users={str(x) for x in (raw.get("allowed_users") or [])},
        owner_users={str(x) for x in (raw.get("owner_users") or [])},
        prompts_dir=prompts_dir,
        default_agent=default_agent,
        memory=memory,
        alerts=alerts,
        stt=stt,
        tts=tts,
        ocr=ocr,
        proactive=proactive,
    )
