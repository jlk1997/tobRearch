"""大模型原生联网。MiniMax 走服务端 web_search；通义、智谱留了适配位。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

_MINIMAX_HOSTS = ("minimaxi.com", "minimax.cn", "minimax.io")


def native_loop_enabled() -> bool:
    import os

    return (os.environ.get("FENGSUN_NATIVE_WEB_LOOP") or "").strip().lower() in {"1", "true", "yes"}


def native_capability(llm) -> dict | None:
    if llm is None:
        return None
    host = (urlparse(getattr(llm, "base_url", "") or "").hostname or "").lower()
    provider = (getattr(llm, "provider", "") or "").lower()
    if "minimax" in provider or any(bit in host for bit in _MINIMAX_HOSTS):
        base = (llm.base_url or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return {"kind": "minimax", "base": base + "/anthropic", "model": llm.model, "key": llm.api_key}
    if "dashscope" in host or provider in {"qwen", "dashscope", "tongyi"}:
        return {"kind": "qwen", "base": (llm.base_url or "").rstrip("/"), "model": llm.model, "key": llm.api_key}
    if "bigmodel" in host or provider in {"zhipu", "glm"}:
        return {"kind": "zhipu", "base": (llm.base_url or "").rstrip("/"), "model": llm.model, "key": llm.api_key}
    return None


def parse_minimax_message(data: dict) -> dict:
    queries: list[str] = []
    hits: list[dict] = []
    texts: list[str] = []
    calls: list[dict] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type") or ""
        if kind == "text" and block.get("text"):
            texts.append(str(block.get("text")))
        elif kind == "server_tool_use" and block.get("name") == "web_search":
            query = str((block.get("input") or {}).get("query") or "").strip()
            if query:
                queries.append(query)
        elif kind == "web_search_tool_result":
            for row in block.get("content") or []:
                if not isinstance(row, dict):
                    continue
                if row.get("type") not in {None, "web_search_result"}:
                    continue
                url = str(row.get("url") or "").strip()
                if not url:
                    continue
                hits.append(
                    {
                        "title": str(row.get("title") or url)[:300],
                        "url": url[:1000],
                        "snippet": str(row.get("content") or "")[:800],
                        "published": str(row.get("page_age") or "")[:40],
                        "provider": "minimax",
                    }
                )
        elif kind == "tool_use" and block.get("name") and block.get("name") != "web_search":
            raw = block.get("input") if isinstance(block.get("input"), dict) else {}
            calls.append({"id": str(block.get("id") or ""), "name": str(block.get("name")), "args": raw})
    return {"queries": queries, "hits": _dedupe_hits(hits), "text": "\n".join(texts).strip(), "tool_calls": calls}


def _dedupe_hits(hits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for hit in hits:
        key = hit.get("url") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def native_search(llm, instruction: str, *, transport=None, timeout: float = 60.0) -> dict:
    """一次原生检索。失败时 ok=False，调用方再换博查 / Tavily。"""
    cap = native_capability(llm)
    empty = {"ok": False, "hits": [], "queries": [], "text": "", "error": "", "provider": ""}
    if cap is None:
        return {**empty, "error": "unsupported"}
    if cap["kind"] != "minimax":
        return {**empty, "error": "adapter_pending", "provider": cap["kind"]}
    payload = {
        "model": cap["model"] or "MiniMax-M3",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": (instruction or "")[:1500]}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cap["key"],
        "anthropic-version": "2023-06-01",
    }
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            resp = client.post(cap["base"].rstrip("/") + "/v1/messages", headers=headers, json=payload)
        if resp.status_code >= 400:
            return {**empty, "error": f"http_{resp.status_code}"}
        data = resp.json()
    except Exception as exc:
        return {**empty, "error": type(exc).__name__}
    parsed = parse_minimax_message(data if isinstance(data, dict) else {})
    if not parsed["hits"] and not parsed["text"]:
        return {**empty, "error": "empty", "provider": "minimax"}
    return {"ok": True, "provider": "minimax", "error": "", **parsed}


def openai_messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """把运行时的 OpenAI 消息转成 Anthropic。服务端 web_search 不需要回传。"""
    system = []
    out: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") or ""
        if role == "system":
            system.append(str(msg.get("content") or ""))
            continue
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(msg.get("tool_call_id") or ""),
                            "content": str(msg.get("content") or "")[:4000],
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            blocks = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": str(msg.get("content"))[:2000]})
            for call in msg.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": args if isinstance(args, dict) else {},
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        if role in {"user", "assistant"}:
            out.append({"role": role, "content": str(msg.get("content") or "")[:8000]})
    return "\n".join(s for s in system if s), out


def openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """函数工具转 Anthropic。名字是 web_search 的去掉，避免和服务端工具撞名。"""
    out = [{"type": "web_search_20250305", "name": "web_search"}]
    for tool in tools or []:
        fn = (tool or {}).get("function") or {}
        name = str(fn.get("name") or "")
        if not name or name == "web_search":
            continue
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}}
        out.append(
            {
                "name": name,
                "description": str(fn.get("description") or "")[:500],
                "input_schema": params,
            }
        )
    return out


def anthropic_turn(llm, messages: list[dict], tools: list[dict], *, transport=None) -> dict:
    """一轮：自定义工具 + 服务端联网。形状对齐 complete_with_tools，多 web_hits。"""
    cap = native_capability(llm)
    if cap is None or cap["kind"] != "minimax":
        return {"ok": False, "error": "unsupported", "text": "", "tool_calls": [], "web_hits": [], "usage": {}}
    system, converted = openai_messages_to_anthropic(messages)
    payload: dict[str, Any] = {
        "model": cap["model"] or "MiniMax-M3",
        "max_tokens": 4096,
        "messages": converted or [{"role": "user", "content": ""}],
        "tools": openai_tools_to_anthropic(tools),
    }
    if system:
        payload["system"] = system[:8000]
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cap["key"],
        "anthropic-version": "2023-06-01",
    }
    try:
        with httpx.Client(timeout=60.0, transport=transport, follow_redirects=False) as client:
            resp = client.post(cap["base"].rstrip("/") + "/v1/messages", headers=headers, json=payload)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"http_{resp.status_code}", "text": "", "tool_calls": [], "web_hits": [], "usage": {}}
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "text": "", "tool_calls": [], "web_hits": [], "usage": {}}
    parsed = parse_minimax_message(data if isinstance(data, dict) else {})
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    return {
        "ok": True,
        "error": "",
        "text": parsed["text"],
        "tool_calls": parsed["tool_calls"],
        "web_hits": parsed["hits"],
        "web_queries": parsed["queries"],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
        },
        "model": cap["model"],
    }
