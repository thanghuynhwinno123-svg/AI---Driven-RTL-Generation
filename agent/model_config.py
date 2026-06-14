import os
from dataclasses import dataclass

DEFAULT_MODEL = os.getenv("MODEL_NAME", "gpt-5.4-mini")

AGENT_MODEL_ENV = {
    "planner": "PLANNER_MODEL",
    "architect": "ARCHITECT_MODEL",
    "rtl": "RTL_MODEL",
    "tb": "TB_MODEL",
    "vcs": "VCS_MODEL",
    "debug": "DEBUG_MODEL",
    "review": "REVIEW_MODEL",
}

AGENT_DEFAULT_MODELS = {
    "planner": "gpt-5.4",
    "architect": "gpt-5.4",
    "rtl": "gpt-5.4",
    "tb": "gpt-5.4",
    "vcs": "gpt-5.4-mini",
    "debug": "gpt-5.4",
    "review": "gpt-5.4-mini",
}

@dataclass(frozen=True)
class AgentModelConfig:
    planner: str
    architect: str
    rtl: str
    tb: str
    vcs: str
    debug: str
    review: str


def model_for(agent_name: str) -> str:
    env_name = AGENT_MODEL_ENV.get(agent_name)
    if env_name:
        return os.getenv(env_name, AGENT_DEFAULT_MODELS.get(agent_name, DEFAULT_MODEL))
    return DEFAULT_MODEL


def load_model_config() -> AgentModelConfig:
    return AgentModelConfig(**{name: model_for(name) for name in AGENT_MODEL_ENV})
