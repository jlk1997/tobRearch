from __future__ import annotations

import os
from dataclasses import dataclass

SCOPE_STEPS = {
    "paper": 6,
    "lab": 8,
    "onboarding": 8,
    "writing": 6,
}


@dataclass(frozen=True)
class Budget:
    """一次会话的硬上限。超限即收尾作答，不是报错。"""

    max_steps: int = 6
    max_tool_calls: int = 10
    tool_timeout_s: float = 20.0
    max_tool_chars: int = 6000
    max_sources: int = 24

    def exhausted(self, *, step: int, tool_calls: int) -> str:
        if step >= self.max_steps:
            return "steps"
        if tool_calls >= self.max_tool_calls:
            return "tool_calls"
        return ""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


def budget_for(scope: str) -> Budget:
    steps = SCOPE_STEPS.get((scope or "paper").strip(), 6)
    return Budget(
        max_steps=_env_int("FENGSUN_AGENT_MAX_STEPS", steps),
        max_tool_calls=_env_int("FENGSUN_AGENT_MAX_TOOL_CALLS", steps * 2),
        tool_timeout_s=float(os.environ.get("FENGSUN_AGENT_TOOL_TIMEOUT") or 20.0),
    )
