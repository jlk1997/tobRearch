from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_prompt(name: str, **kwargs) -> str:
    """只读 markdown 提示词。禁止做成管理后台。"""
    path = PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8").strip()
    if not kwargs:
        return text
    return text.format_map(_Safe({k: "" if v is None else v for k, v in kwargs.items()}))
