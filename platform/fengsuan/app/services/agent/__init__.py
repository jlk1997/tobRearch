"""Agent 运行时（A-AGENT）。

内部层，不是新产品面。UI 只在三个现有入口露出：
工作台入门卡、知识库 `?tab=ask`、阅读器 `?panel=tutor`。

- `registry`：工具注册表，按 `scope` 划权限域，权限在工具内部强制。
- `loop`：多轮 tool-calling 循环，逐步产出 SSE 事件。
- `budget`：步数 / 超时 / 降级链。
- `trace`：每轮写 `agent_traces`，供导师与管理员复算答案来源。
"""

from fengsuan.app.services.agent.budget import Budget, budget_for
from fengsuan.app.services.agent.loop import agent_answer, run_agent
from fengsuan.app.services.agent.registry import ToolContext, list_tools, openai_tools, tool

__all__ = [
    "Budget",
    "ToolContext",
    "agent_answer",
    "budget_for",
    "list_tools",
    "openai_tools",
    "run_agent",
    "tool",
]
