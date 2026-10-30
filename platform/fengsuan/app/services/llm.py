from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.netguard import assert_llm_base_url
from fengsuan.app.core.security import decrypt_secret
from fengsuan.app.db.base import utcnow
from fengsuan.app.models.credential import LLMCredential
from fengsuan.app.models.runs import UsageLedger


class ResolvedLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        source: str,
        kind: str = "cloud",
        purpose: str = "chat",
        provider: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.source = source
        self.kind = kind or "cloud"
        self.purpose = purpose or "chat"
        self.provider = provider or ""


def _model_for(row: LLMCredential, purpose: str) -> str:
    purpose = (purpose or "chat").strip() or "chat"
    if purpose == "vision":
        return (row.model_vision or row.model_chat or "gpt-4o-mini").strip()
    if purpose == "embed":
        return (getattr(row, "model_embed", None) or os.environ.get("FENGSUN_EMBED_MODEL") or "text-embedding-3-small").strip()
    if purpose == "rerank":
        return (getattr(row, "model_rerank", None) or os.environ.get("FENGSUN_RERANK_MODEL") or "").strip()
    return (row.model_chat or "gpt-4o-mini").strip()


def _cred_row(db: Session, owner_type: str, owner_id: str) -> LLMCredential | None:
    return (
        db.query(LLMCredential)
        .filter(
            LLMCredential.owner_type == owner_type,
            LLMCredential.owner_id == owner_id,
            LLMCredential.is_active.is_(True),
        )
        .first()
    )


def _resolved_from_row(row: LLMCredential | None, purpose: str) -> ResolvedLLM | None:
    if row is None or not row.api_key_encrypted:
        return None
    try:
        key = decrypt_secret(row.api_key_encrypted)
    except Exception:
        return None
    if not key:
        return None
    model = _model_for(row, purpose)
    if purpose == "rerank" and not model:
        return None
    return ResolvedLLM(
        row.base_url or "https://api.openai.com/v1",
        key,
        model or "gpt-4o-mini",
        row.owner_type,
        kind=getattr(row, "kind", None) or "cloud",
        purpose=purpose,
        provider=getattr(row, "provider", None) or "",
    )


def _demo_fallback_row(db: Session, lab_id: str) -> LLMCredential | None:
    """体验实验室自己没有密钥时，借用已经配好的那一把。密钥不会出现在游客能打开的页面上。"""
    if not lab_id:
        return None
    from fengsuan.app.models.lab import Lab

    if not _guest_lab(db.get(Lab, lab_id)):
        return None
    rows = db.query(LLMCredential).filter(LLMCredential.is_active.is_(True)).all()
    rank = {"platform": 0, "lab": 1, "user": 2}
    rows.sort(key=lambda row: rank.get(row.owner_type or "", 9))
    for row in rows:
        if row.api_key_encrypted:
            return row
    return None


def visible_lab_credential(db: Session, lab_id: str) -> tuple[LLMCredential | None, bool]:
    """本实验室自己的密钥优先。体验实验室没有时，借用已经配好的那一把。"""
    own = _cred_row(db, "lab", lab_id)
    if own is not None and own.api_key_encrypted:
        return own, False
    borrowed = _demo_fallback_row(db, lab_id)
    if borrowed is not None:
        return borrowed, True
    return None, False


def resolve_llm(db: Session, user_id: str, lab_id: str, purpose: str = "chat") -> ResolvedLLM:
    purpose = (purpose or "chat").strip() or "chat"
    owners: list[tuple[str, str]] = []
    if user_id:
        owners.append(("user", user_id))
    if lab_id:
        owners.append(("lab", lab_id))
    owners.append(("platform", "platform"))
    for owner_type, owner_id in owners:
        found = _resolved_from_row(_cred_row(db, owner_type, owner_id), purpose)
        if found:
            return found
    found = _resolved_from_row(_demo_fallback_row(db, lab_id), purpose)
    if found:
        return found
    env_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("FENGSUN_API_KEY") or ""
    if env_key:
        env_model = os.environ.get("FENGSUN_CHAT_MODEL") or "gpt-4o-mini"
        if purpose == "embed":
            env_model = os.environ.get("FENGSUN_EMBED_MODEL") or "text-embedding-3-small"
        elif purpose == "rerank":
            env_model = os.environ.get("FENGSUN_RERANK_MODEL") or ""
            if not env_model:
                env_key = ""
        if env_key:
            return ResolvedLLM(
                os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
                env_key,
                env_model,
                "env",
                kind="cloud",
                purpose=purpose,
            )
    raise ClassifiedHTTPException(
        400,
        "未配置 API Key：请在个人、实验室或平台默认中填写云端或内网（OpenAI 兼容）密钥",
        "permission",
    )


def lab_tokens_this_month(db: Session, lab_id: str) -> int:
    from sqlalchemy import func

    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 向量化按实验室单独计量（embed_tokens_this_month），不占每月阅读 token。
    base = db.query(UsageLedger).filter(
        UsageLedger.lab_id == lab_id,
        UsageLedger.created_at >= start,
        UsageLedger.feature != "embed",
    )
    prompt = base.with_entities(func.coalesce(func.sum(UsageLedger.prompt_tokens), 0)).scalar() or 0
    completion = base.with_entities(func.coalesce(func.sum(UsageLedger.completion_tokens), 0)).scalar() or 0
    return int(prompt) + int(completion)


def embed_tokens_this_month(db: Session, lab_id: str) -> int:
    from sqlalchemy import func

    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = (
        db.query(func.coalesce(func.sum(UsageLedger.prompt_tokens), 0))
        .filter(UsageLedger.lab_id == lab_id, UsageLedger.created_at >= start, UsageLedger.feature == "embed")
        .scalar()
        or 0
    )
    return int(total)


TOKEN_QUOTA_NEXT = "本月 token 已用尽。请联系导师或管理员调整预算，或下月再试。不会伪造指标。"
GUEST_QUOTA_NEXT = "本月体验额度已用完。下月再试。不会借用正式实验室的额度。"
QUOTA_FEATURES = frozenset(
    {"research", "reproduce", "analysis", "compare", "tutor", "writing", "language", "kb_query", "meeting"}
)


def _guest_lab(lab) -> bool:
    if lab is None:
        return False
    if (getattr(lab, "code", "") or "") == "demo":
        return True
    try:
        raw = json.loads(getattr(lab, "settings_json", None) or "{}")
    except json.JSONDecodeError:
        return False
    return isinstance(raw, dict) and bool(raw.get("guest"))


def lab_token_budget(db: Session, lab_id: str) -> int:
    """正式实验室用每月大额；体验实验室用单独的小额，互不借用。"""
    from fengsuan.app.core.config import get_settings
    from fengsuan.app.models.lab import Lab

    settings = get_settings()
    regular = int(settings.lab_monthly_token_budget or 0)
    if not lab_id:
        return regular
    lab = db.get(Lab, lab_id)
    if not _guest_lab(lab):
        return regular
    return int(settings.guest_monthly_token_budget or 0)


def assert_lab_quota(db: Session, lab_id: str, feature: str) -> None:
    if feature not in QUOTA_FEATURES:
        return
    budget = lab_token_budget(db, lab_id)
    if lab_tokens_this_month(db, lab_id) < budget:
        return
    from fengsuan.app.models.lab import Lab

    guest = _guest_lab(db.get(Lab, lab_id)) if lab_id else False
    if guest:
        raise ClassifiedHTTPException(429, "本月体验额度已用完，生成已暂停", "quota", next_step=GUEST_QUOTA_NEXT)
    raise ClassifiedHTTPException(429, "实验室本月 token 预算已用尽，生成与重型任务已暂停", "quota", next_step=TOKEN_QUOTA_NEXT)


def llm_stub_enabled() -> bool:
    return os.environ.get("FENGSUN_LLM_STUB", "").lower() in {"1", "true", "yes"}


_THINK_CLOSED = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_REASONING_FENCE = re.compile(r"```(?:reasoning|think|thoughts)\s*\n.*?```", re.IGNORECASE | re.DOTALL)


def _drop_unclosed_think(text: str) -> str:
    """Unclosed <think> is preamble; keep the body after a blank line or first newline."""
    match = _THINK_OPEN.search(text)
    if not match:
        return text
    rest = text[match.end() :]
    parts = re.split(r"\n\s*\n", rest, maxsplit=1)
    if len(parts) == 2:
        return text[: match.start()] + parts[1]
    parts = rest.split("\n", 1)
    if len(parts) == 2:
        return text[: match.start()] + parts[1]
    return text[: match.start()]


def strip_model_reasoning(text: str | None) -> tuple[str, bool]:
    """Drop hidden reasoning. UI must never render think / reasoning_content."""
    raw = text if isinstance(text, str) else ""
    out = raw
    stripped = False
    nxt = _THINK_CLOSED.sub("", out)
    if nxt != out:
        stripped = True
        out = nxt
    nxt = _REASONING_FENCE.sub("", out)
    if nxt != out:
        stripped = True
        out = nxt
    if _THINK_OPEN.search(out):
        nxt = _drop_unclosed_think(out)
        if nxt != out:
            stripped = True
            out = nxt
    nxt = re.sub(r"</think>", "", out, flags=re.IGNORECASE)
    if nxt != out:
        stripped = True
        out = nxt
    cleaned = re.sub(r"\n{3,}", "\n\n", out).strip()
    return cleaned, stripped


def stream_visible(text: str | None) -> str:
    """流式时能给用户看的部分。未闭合的 think 先按住，不把推理过程吐出去。"""
    raw = text if isinstance(text, str) else ""
    out = _THINK_CLOSED.sub("", raw)
    out = _REASONING_FENCE.sub("", out)
    match = _THINK_OPEN.search(out)
    if match:
        out = out[: match.start()]
    return re.sub(r"</think>", "", out, flags=re.IGNORECASE)


def _first_json_object(blob: str) -> dict:
    text = re.sub(r"```(?:json)?", "", blob or "", flags=re.IGNORECASE).replace("```", "")
    start_at = 0
    while True:
        start = text.find("{", start_at)
        if start < 0:
            return {}
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    return data if isinstance(data, dict) else {}
        start_at = start + 1


def extract_json(text: str | None) -> dict:
    """去掉思考过程和代码块，取出第一个 JSON 对象。"""
    raw = text if isinstance(text, str) else ""
    if not raw.strip():
        return {}
    cleaned, _ = strip_model_reasoning(raw)
    for blob in (cleaned, raw):
        found = _first_json_object(blob)
        if found:
            return found
    return {}


def _content_parts(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and (part.get("type") or "text") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _message_text(choice: dict) -> str:
    msg = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(msg, dict):
        return ""
    return _content_parts(msg.get("content"))


def _delta_text(choice: dict) -> str:
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return _content_parts(delta.get("content"))
    return _message_text(choice)


def is_transient_llm_error(exc: Exception) -> bool:
    return isinstance(exc, ClassifiedHTTPException) and exc.status_code in {502, 504} and exc.error_class == "llm"


def sse_pack(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _raise_llm_http(status_code: int) -> None:
    if status_code in {401, 403}:
        raise ClassifiedHTTPException(502, "密钥被供应商拒绝，请测通或更换 Key", "permission")
    if status_code == 429:
        raise ClassifiedHTTPException(429, "供应商额度已满，请稍后重试", "quota")
    raise ClassifiedHTTPException(502, f"模型服务返回 HTTP {status_code}", "llm")


def _chat_headers(llm: ResolvedLLM) -> dict[str, str]:
    return {"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"}


def _split_reasoning(llm: ResolvedLLM) -> bool:
    """MiniMax 把推理放进 reasoning_content。不打开的话，思考会写进正文。"""
    host = (urlparse(llm.base_url or "").hostname or "").lower()
    provider = (getattr(llm, "provider", "") or "").lower()
    return "minimax" in provider or any(bit in host for bit in ("minimaxi.com", "minimax.cn", "minimax.io"))


def _chat_payload(llm: ResolvedLLM, messages: list[dict], json_mode: bool, *, stream: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": llm.model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if _split_reasoning(llm):
        payload["reasoning_split"] = True
    if stream:
        payload["stream"] = True
    return payload


def _complete_chat(llm: ResolvedLLM, messages: list[dict], json_mode: bool) -> tuple[str, dict]:
    base = assert_llm_base_url(llm.base_url or "https://api.openai.com/v1", kind=getattr(llm, "kind", None) or "cloud")
    url = base + "/chat/completions"
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False) as client:
            resp = client.post(url, headers=_chat_headers(llm), json=_chat_payload(llm, messages, json_mode))
            if resp.status_code >= 400:
                _raise_llm_http(resp.status_code)
            data = resp.json()
    except ClassifiedHTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm") from exc
    except httpx.HTTPError as exc:
        raise ClassifiedHTTPException(502, "无法连接模型服务", "llm") from exc
    choice = (data.get("choices") or [{}])[0]
    text = _message_text(choice if isinstance(choice, dict) else {})
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return text, usage


def _iter_openai_tokens(llm: ResolvedLLM, messages: list[dict], json_mode: bool) -> Iterator[tuple[str, dict | None]]:
    base = assert_llm_base_url(llm.base_url or "https://api.openai.com/v1", kind=getattr(llm, "kind", None) or "cloud")
    url = base + "/chat/completions"
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False) as client:
            with client.stream(
                "POST",
                url,
                headers=_chat_headers(llm),
                json=_chat_payload(llm, messages, json_mode, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    _raise_llm_http(resp.status_code)
                usage: dict = {}
                for line in resp.iter_lines():
                    if not line:
                        continue
                    raw = line.decode("utf-8", errors="replace") if isinstance(line, (bytes, bytearray)) else str(line)
                    raw = raw.strip()
                    if not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    choice = (obj.get("choices") or [{}])[0]
                    token = _delta_text(choice if isinstance(choice, dict) else {})
                    if token:
                        yield token, None
                if usage:
                    yield "", usage
    except ClassifiedHTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm") from exc
    except httpx.HTTPError as exc:
        raise ClassifiedHTTPException(502, "无法连接模型服务", "llm") from exc


def _stub_agent_answer(user: str) -> str:
    """Agent 收尾作答的 stub：只引用真的传进来的 [S#]，没有就拒答。"""
    question = ""
    corpus = user or ""
    try:
        payload = json.loads(user or "{}")
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and ("sources" in payload or "question" in payload):
        corpus = str(payload.get("sources") or "")
        question = str(payload.get("question") or "").strip()
    elif "查到的材料：" in corpus:
        if "问题：" in corpus:
            question = corpus.split("问题：", 1)[1].split("查到的材料：", 1)[0].strip()
        corpus = corpus.split("查到的材料：", 1)[1]
        if "回答要求：" in corpus:
            corpus = corpus.split("回答要求：", 1)[0]
    labels = re.findall(r"\[S(\d+)\]", corpus)
    if not labels:
        return "现在查不到可引用的材料，先别当结论用。可以说得更具体一点吗？"
    lines = []
    for label in list(dict.fromkeys(labels))[:3]:
        m = re.search(rf"\[S{label}\]\s*(.{{0,60}})", corpus)
        head = (m.group(1).strip() if m else "").splitlines()[0] if m else ""
        lines.append(f"根据{head or '检索到的材料'}，这一点有对应记录 [S{label}]。")
    if question:
        lines.append(f"围绕这个问题，以上是可追溯的部分 [S{labels[0]}]。")
    return "\n".join(lines)


def stub_complete(messages: list[dict], *, json_mode: bool = False) -> str:
    user = ""
    system = ""
    for m in messages:
        if m.get("role") == "system":
            system += m.get("content") or ""
        if m.get("role") == "user":
            user = m.get("content") or ""
    if "蜂算科研的实验室助手" in system:
        return _stub_agent_answer(user)
    if "tutor" in system.lower() or "精读辅导" in system or "对着这一页" in system:
        return "请结合本页与这篇怎么做继续阅读。stub 辅导。"
    if "method_card" in system or "科研论文分析器" in system:
        body = user or ""
        for sep in ("已用工具取到的可追溯片段", "【下面才是要处理的正文】"):
            if sep in body:
                body = body.split(sep, 1)[0 if sep.startswith("已用") else 1]
        plain = re.sub(r"\[p\.\d+\]\s*", "", body)
        snippet = (plain.split("\n\n")[0] if plain else "").strip()[:200] or "stub"
        return json.dumps(
            {
                "problem": snippet,
                "claims": [{"text": snippet, "pages": [1]}],
                "methods": [{"text": snippet, "pages": [1]}],
                "datasets": [],
                "metrics": [],
                "baselines": [],
                "limitations": [],
                "reusable": [],
                "related_in_lab": [],
            },
            ensure_ascii=False,
        )
    if "行动项" in system or "组会纪要" in system:
        items: list[str] = []
        for line in (user or "").splitlines():
            s = line.strip().lstrip("-*").strip()
            if s.startswith(("行动", "TODO", "todo", "[ ]", "AI:")):
                items.append(s.split(":", 1)[-1].strip() or s)
            elif s.startswith(("1.", "2.", "3.")) and ("请" in s or "完成" in s):
                items.append(s)
        return "\n".join(items)
    if "compare" in system or "对比" in system:
        ids: list[str] = []
        blob = (user or "").split("已用工具取到的可追溯片段")[0].strip()
        try:
            payload = json.loads(blob)
            if isinstance(payload, list):
                ids = [str(x.get("id") or "") for x in payload if isinstance(x, dict) and x.get("id")]
        except json.JSONDecodeError:
            ids = []
        src = "、".join(ids[:2]) or "所选文献"
        return json.dumps(
            {
                "table_md": "| dim | A |\n|---|---|\n| methods | stub |",
                "gap_md": f"差距：方法覆盖不足（来源 {src}）。对照所选文献这篇怎么做，尚未形成可写回的完整综述。",
                "json": {"methods": [], "claims": []},
            },
            ensure_ascii=False,
        )
    if json_mode and ("outline" in system.lower() or "大纲" in system or "sections" in system):
        return json.dumps(
            {
                "title": "stub-outline",
                "sections": [{"id": "s1", "heading": "围绕文献主题的问题", "children": [{"heading": "对照篇目"}]}],
            },
            ensure_ascii=False,
        )
    if json_mode:
        return "{}"
    return user


def quota_warning(db: Session, lab_id: str) -> str | None:
    if lab_tokens_this_month(db, lab_id) < lab_token_budget(db, lab_id):
        return None
    from fengsuan.app.models.lab import Lab

    if _guest_lab(db.get(Lab, lab_id)) if lab_id else False:
        return "本月体验额度已用完。分析、辅导、写作、知识库生成与重型任务将被拒绝。"
    return "本月实验室 token 已达预算。分析、辅导、写作、知识库生成与重型任务将被拒绝。"


def ping_credential(row: LLMCredential) -> dict:
    """测通：stub 环境只校验解密；线上请求 /models，不把密钥写入返回。"""
    from fengsuan.app.core.security import mask_key

    if not row or not row.api_key_encrypted:
        raise ClassifiedHTTPException(400, "未配置 API Key", "permission")
    try:
        key = decrypt_secret(row.api_key_encrypted)
    except Exception as exc:
        raise ClassifiedHTTPException(400, "密钥解密失败，请重新保存", "permission") from exc
    masked = mask_key(key)
    if llm_stub_enabled():
        return {"ok": True, "mode": "stub", "api_key_masked": masked}
    base = assert_llm_base_url(row.base_url or "https://api.openai.com/v1", kind=getattr(row, "kind", None) or "cloud")
    url = base + "/models"
    try:
        with httpx.Client(timeout=12.0, follow_redirects=False) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "mode": "live",
            "detail": str(exc)[:200],
            "api_key_masked": masked,
            "error_class": "llm",
        }
    if resp.status_code < 400:
        return {"ok": True, "mode": "live", "api_key_masked": masked}
    klass = "permission" if resp.status_code in {401, 403} else "quota" if resp.status_code == 429 else "llm"
    return {
        "ok": False,
        "mode": "live",
        "detail": f"HTTP {resp.status_code}",
        "api_key_masked": masked,
        "error_class": klass,
    }


def iter_chat_deltas(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    messages: list[dict],
    feature: str,
    json_mode: bool = False,
    run_id: str | None = None,
    ledger: bool = True,
) -> Iterator[str]:
    """Yield visible tokens. Ledger once. Retry once if nothing was emitted."""
    assert_lab_quota(db, lab_id, feature)
    if llm_stub_enabled():
        text, _ = strip_model_reasoning(stub_complete(messages, json_mode=json_mode))
        if text:
            yield text
        if ledger:
            _ledger(db, lab_id, user_id, feature, "stub", 0, 0, run_id, "stub")
        return
    try:
        llm = resolve_llm(db, user_id, lab_id, purpose="chat")
    except ClassifiedHTTPException:
        if ledger:
            _ledger(db, lab_id, user_id, feature, "failed", 0, 0, run_id, "failed")
        raise
    last_err: Exception | None = None
    emitted = False
    for attempt in range(2):
        try:
            raw = ""
            visible = ""
            usage: dict = {}
            try:
                for token, usage_part in _iter_openai_tokens(llm, messages, json_mode):
                    if usage_part:
                        usage = usage_part
                    if not token:
                        continue
                    raw += token
                    vis = stream_visible(raw)
                    if len(vis) > len(visible):
                        piece = vis[len(visible) :]
                        visible = vis
                        emitted = True
                        yield piece
            except ClassifiedHTTPException as exc:
                if exc.status_code in {400, 422} and not emitted:
                    raw_text, usage = _complete_chat(llm, messages, json_mode)
                    visible = stream_visible(raw_text).strip()
                    if visible:
                        emitted = True
                        yield visible
                else:
                    raise
            if not visible and raw:
                visible = stream_visible(raw).strip()
                if visible:
                    emitted = True
                    yield visible
            if ledger:
                _ledger(
                    db,
                    lab_id,
                    user_id,
                    feature,
                    llm.model,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    run_id,
                    llm.source,
                )
            return
        except ClassifiedHTTPException as exc:
            last_err = exc
            if attempt == 0 and is_transient_llm_error(exc) and not emitted:
                continue
            if ledger:
                _ledger(db, lab_id, user_id, feature, getattr(llm, "model", None) or "failed", 0, 0, run_id, "failed")
            raise
    if last_err:
        raise last_err


def chat(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    messages: list[dict],
    feature: str,
    json_mode: bool = False,
    run_id: str | None = None,
    on_delta=None,
) -> str:
    if on_delta is not None:
        parts: list[str] = []
        for delta in iter_chat_deltas(
            db,
            user_id=user_id,
            lab_id=lab_id,
            messages=messages,
            feature=feature,
            json_mode=json_mode,
            run_id=run_id,
        ):
            parts.append(delta)
            on_delta(delta)
        return "".join(parts)
    assert_lab_quota(db, lab_id, feature)
    if llm_stub_enabled():
        text, _ = strip_model_reasoning(stub_complete(messages, json_mode=json_mode))
        _ledger(db, lab_id, user_id, feature, "stub", 0, 0, run_id, "stub")
        return text
    llm = None
    try:
        llm = resolve_llm(db, user_id, lab_id, purpose="chat")
    except ClassifiedHTTPException:
        _ledger(db, lab_id, user_id, feature, "failed", 0, 0, run_id, "failed")
        raise
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw, usage = _complete_chat(llm, messages, json_mode)
            text, _ = strip_model_reasoning(raw)
            _ledger(
                db,
                lab_id,
                user_id,
                feature,
                llm.model,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                run_id,
                llm.source,
            )
            return text
        except ClassifiedHTTPException as exc:
            last_err = exc
            if attempt == 0 and is_transient_llm_error(exc):
                continue
            _ledger(db, lab_id, user_id, feature, llm.model or "failed", 0, 0, run_id, "failed")
            raise
    if last_err:
        raise last_err
    return ""


def supports_tool_calling(db: Session, user_id: str, lab_id: str) -> bool:
    """能不能多轮调工具。stub 环境由运行时自己模拟，不走这条判断。"""
    if llm_stub_enabled():
        return False
    try:
        resolve_llm(db, user_id, lab_id, purpose="chat")
    except ClassifiedHTTPException:
        return False
    return True


def complete_with_tools(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    messages: list[dict],
    tools: list[dict],
    feature: str,
    run_id: str | None = None,
    tool_choice: str = "auto",
) -> dict:
    """一轮 OpenAI 兼容 tool-calling。返回 {text, tool_calls, usage, model}。

    记账仍走 `_ledger`：一次 `run_agent` 的多轮合起来按实际 usage 记，不按轮数放大。
    """
    assert_lab_quota(db, lab_id, feature)
    llm = resolve_llm(db, user_id, lab_id, purpose="chat")
    from fengsuan.app.services.web.native import anthropic_turn, native_capability, native_loop_enabled

    if native_loop_enabled() and native_capability(llm):
        turned = anthropic_turn(llm, messages, tools)
        if turned.get("ok"):
            return {
                "text": turned.get("text") or "",
                "tool_calls": turned.get("tool_calls") or [],
                "usage": turned.get("usage") or {},
                "model": turned.get("model") or llm.model,
                "source": llm.source,
                "web_hits": turned.get("web_hits") or [],
                "web_queries": turned.get("web_queries") or [],
            }
    base = assert_llm_base_url(llm.base_url or "https://api.openai.com/v1", kind=getattr(llm, "kind", None) or "cloud")
    payload: dict[str, Any] = {"model": llm.model, "messages": messages}
    if _split_reasoning(llm):
        payload["reasoning_split"] = True
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    try:
        with httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0), follow_redirects=False) as client:
            resp = client.post(base + "/chat/completions", headers=_chat_headers(llm), json=payload)
            if resp.status_code >= 400:
                _raise_llm_http(resp.status_code)
            data = resp.json()
    except ClassifiedHTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm") from exc
    except httpx.HTTPError as exc:
        raise ClassifiedHTTPException(502, "无法连接模型服务", "llm") from exc
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") if isinstance(choice, dict) else {}
    msg = msg if isinstance(msg, dict) else {}
    raw_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
    calls = []
    for c in raw_calls:
        fn = (c or {}).get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append(
            {
                "id": c.get("id") or "",
                "name": str(fn.get("name") or ""),
                "args": args if isinstance(args, dict) else {},
            }
        )
    text, _ = strip_model_reasoning(_content_parts(msg.get("content")))
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"text": text, "tool_calls": calls, "usage": usage, "model": llm.model, "source": llm.source}


def ledger_usage(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    feature: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    run_id: str | None = None,
    credential_scope: str = "",
) -> None:
    """给 Agent 运行时用的记账入口。口径与 `chat()` 完全一致（G80）。"""
    _ledger(db, lab_id, user_id, feature, model, prompt_tokens, completion_tokens, run_id, credential_scope)


def _ledger(
    db: Session,
    lab_id: str,
    user_id: str,
    feature: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    run_id: str | None,
    credential_scope: str = "",
) -> None:
    db.add(
        UsageLedger(
            lab_id=lab_id,
            user_id=user_id,
            run_id=run_id,
            feature=feature,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            credential_scope=credential_scope,
            created_at=utcnow(),
        )
    )


def _fit_dim(vec: list[float], dim: int) -> list[float]:
    if dim <= 0:
        return vec
    if len(vec) >= dim:
        return [float(x) for x in vec[:dim]]
    return [float(x) for x in vec] + [0.0] * (dim - len(vec))


def embed_texts(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    texts: list[str],
    dim: int,
) -> tuple[list[list[float]], str]:
    """OpenAI-compatible embeddings; stub/失败时 hash 回退并标明。"""
    from fengsuan.app.services.kb import hash_embed

    cleaned = [t or "" for t in texts]
    if not cleaned:
        return [], "hash"
    if llm_stub_enabled() or os.environ.get("FENGSUN_EMBED_HASH", "").lower() in {"1", "true", "yes"}:
        return [hash_embed(t, dim) for t in cleaned], "hash"
    try:
        llm = resolve_llm(db, user_id, lab_id, purpose="embed")
    except ClassifiedHTTPException:
        return [hash_embed(t, dim) for t in cleaned], "hash"
    base = assert_llm_base_url(llm.base_url or "https://api.openai.com/v1", kind=getattr(llm, "kind", None) or "cloud")
    url = base + "/embeddings"
    out: list[list[float]] = []
    used_tokens = 0
    batch = EMBED_BATCH
    with httpx.Client(timeout=60.0, follow_redirects=False) as client:
        for start in range(0, len(cleaned), batch):
            part = [t[:8000] for t in cleaned[start : start + batch]]
            data = None
            for _attempt in range(2):
                try:
                    resp = client.post(
                        url,
                        headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
                        json={"model": llm.model, "input": part},
                    )
                except Exception:
                    continue
                if resp.status_code >= 500 or resp.status_code == 429:
                    continue
                if resp.status_code >= 400:
                    break
                try:
                    data = resp.json()
                except Exception:
                    data = None
                break
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list) or len(items) != len(part):
                _ledger(db, lab_id, user_id or "", "embed", "hash", 0, 0, None, "hash")
                return [hash_embed(t, dim) for t in cleaned], "hash"
            for i, item in enumerate(sorted(items, key=lambda x: int((x or {}).get("index") or 0))):
                vec = (item or {}).get("embedding") or []
                if not isinstance(vec, list) or not vec:
                    out.append(hash_embed(part[i], dim))
                else:
                    out.append(_fit_dim(vec, dim))
            used_tokens += int(((data or {}).get("usage") or {}).get("prompt_tokens") or 0)
    _ledger(
        db,
        lab_id,
        user_id or "",
        "embed",
        llm.model,
        used_tokens,
        0,
        None,
        llm.source,
    )
    return out, "api"


EMBED_BATCH = 64


def embed_model_name(db: Session, *, user_id: str, lab_id: str) -> str:
    if llm_stub_enabled() or os.environ.get("FENGSUN_EMBED_HASH", "").lower() in {"1", "true", "yes"}:
        return "hash"
    try:
        return (resolve_llm(db, user_id, lab_id, purpose="embed").model or "api")[:80]
    except ClassifiedHTTPException:
        return "hash"


def rerank_texts(
    db: Session,
    *,
    user_id: str,
    lab_id: str,
    query: str,
    documents: list[str],
) -> list[str] | None:
    """可选 rerank；失败返回 None，由调用方保持原序。"""
    if llm_stub_enabled() or not documents:
        return None
    try:
        llm = resolve_llm(db, user_id, lab_id, purpose="rerank")
    except ClassifiedHTTPException:
        return None
    if not llm.model:
        return None
    try:
        from openlens_ai.utils.embedding import perform_rerank

        ranked = perform_rerank(documents, query, 8000, llm.model, llm.api_key, llm.base_url.rstrip("/") + "/")
        return ranked or None
    except Exception:
        return None
