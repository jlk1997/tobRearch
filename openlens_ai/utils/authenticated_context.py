"""Authenticated, redaction-tolerant context for the OpenLens compressor.

Threat this module addresses
----------------------------
``vector_search`` (embedding.py) flattens every selected chunk into a
``HumanMessage``. After that step, a fabricated ``System:`` line inside a
fetched paper is indistinguishable from a real system turn. Keyword routers
and sidecar injection then treat the flattened text as authoritative.

What this module guarantees
---------------------------
A framework-held HMAC key signs each segment as
``(role, source, tool_id, turn, content)``. Compression is modelled as
*authorised redaction*: any subset of signed leaves can be kept, and each
kept leaf still verifies. The LLM cannot compute the MAC, so it cannot mint
a new ``role=system`` segment. A Merkle tree over the leaves additionally
proves that a kept subset came from a specific signed snapshot (optional
stronger check).

What this module does *not* guarantee (honesty bound)
-----------------------------------------------------
A MAC only binds *provenance*. A genuinely-signed tool output can still
contain malicious instructions. A light information-flow policy therefore
downgrades unverified / tool / sidecar text to ``untrusted`` and strips
reserved control tokens from it before routing.

Enable with ``[context] authenticated_context = true`` and optionally
``OPENLENS_CONTEXT_KEY`` (hex). Disabled by default so existing runs are
unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

SCHEMA_VERSION = 1
MAC_ALG = "HMAC-SHA256"
RESERVED_CONTROL_TOKENS = (
    "DECISION: END",
    "DECISION: CONTINUE",
    "DECISION: POLISH",
    "DECISION: ALTER_PLAN",
    "DECISION: REDO_LAST_SUBTASK",
    "DECISION: FIX_LAST_SUBTASK",
    "DECISION: CONTINUE_NEXT_SUBTASK",
)
UNTRUSTED_PREFIX = "[UNTRUSTED DATA | provenance unverified or tool/sidecar]\n"


def _role_of(message: BaseMessage) -> str:
    name = message.__class__.__name__
    return {
        "SystemMessage": "system",
        "HumanMessage": "user",
        "AIMessage": "assistant",
        "ToolMessage": "tool",
    }.get(name, name)


def _source_of(message: BaseMessage, turn: int) -> str:
    role = _role_of(message)
    if role == "system":
        return "orchestrator"
    if role == "user":
        return "user"
    if role == "assistant":
        return "model"
    if role == "tool":
        return f"tool:{getattr(message, 'name', '') or 'unknown'}"
    return f"unknown:{turn}"


def _tool_id_of(message: BaseMessage) -> str:
    return str(getattr(message, "tool_call_id", "") or getattr(message, "name", "") or "")


def _canonical(role: str, source: str, tool_id: str, turn: int, content: str) -> bytes:
    # Length-prefixed fields so content cannot smuggle a field separator.
    parts = [
        f"v{SCHEMA_VERSION}",
        role,
        source,
        tool_id,
        str(int(turn)),
        content or "",
    ]
    encoded = "\x1f".join(f"{len(p)}:{p}" for p in parts)
    return encoded.encode("utf-8")


def derive_key(explicit: str | bytes | None = None) -> bytes:
    """Resolve the MAC key: argument, then env, then a per-process fallback."""
    if isinstance(explicit, bytes) and explicit:
        return explicit
    if isinstance(explicit, str) and explicit.strip():
        raw = explicit.strip()
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return hashlib.sha256(raw.encode("utf-8")).digest()
    env = os.environ.get("OPENLENS_CONTEXT_KEY", "").strip()
    if env:
        try:
            return bytes.fromhex(env)
        except ValueError:
            return hashlib.sha256(env.encode("utf-8")).digest()
    # Last resort: deterministic but *not* secret. Fine for offline eval;
    # production should set OPENLENS_CONTEXT_KEY.
    return hashlib.sha256(b"openlens-dev-context-key").digest()


def mac_hex(key: bytes, blob: bytes) -> str:
    return hmac.new(key, blob, hashlib.sha256).hexdigest()


def leaf_hash(mac: str, content: str) -> str:
    h = hashlib.sha256()
    h.update(mac.encode("ascii"))
    h.update(b"\x00")
    h.update((content or "").encode("utf-8"))
    return h.hexdigest()


def merkle_parent(left: str, right: str) -> str:
    return hashlib.sha256(bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return hashlib.sha256(b"empty").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level), 2):
            a = level[i]
            b = level[i + 1] if i + 1 < len(level) else a
            nxt.append(merkle_parent(a, b))
        level = nxt
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[tuple[str, str]]:
    """Return sibling hashes along the path. Each item is (side, hash)."""
    if not leaves or index < 0 or index >= len(leaves):
        return []
    level = list(leaves)
    idx = index
    proof: list[tuple[str, str]] = []
    while len(level) > 1:
        if idx % 2 == 0:
            sib = level[idx + 1] if idx + 1 < len(level) else level[idx]
            proof.append(("R", sib))
        else:
            proof.append(("L", level[idx - 1]))
        nxt: list[str] = []
        for i in range(0, len(level), 2):
            a = level[i]
            b = level[i + 1] if i + 1 < len(level) else a
            nxt.append(merkle_parent(a, b))
        level = nxt
        idx //= 2
    return proof


def verify_merkle_proof(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    acc = leaf
    for side, sib in proof:
        acc = merkle_parent(sib, acc) if side == "L" else merkle_parent(acc, sib)
    return hmac.compare_digest(acc, root)


@dataclass
class ContextSegment:
    role: str
    source: str
    tool_id: str
    turn: int
    content: str
    mac: str
    leaf: str
    trust: str = "trusted"  # trusted | untrusted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextBundle:
    segments: list[ContextSegment] = field(default_factory=list)
    root: str = ""
    key_id: str = ""

    def by_turn(self) -> dict[int, ContextSegment]:
        return {s.turn: s for s in self.segments}


class AuthenticatedContext:
    def __init__(self, key: bytes | str | None = None):
        self.key = derive_key(key)
        self.key_id = hashlib.sha256(self.key).hexdigest()[:16]

    def sign_text(
        self,
        *,
        role: str,
        source: str,
        tool_id: str,
        turn: int,
        content: str,
        trust: str | None = None,
    ) -> ContextSegment:
        blob = _canonical(role, source, tool_id, turn, content)
        mac = mac_hex(self.key, blob)
        leaf = leaf_hash(mac, content)
        if trust is None:
            trust = "untrusted" if role in {"tool", "user"} and source.startswith(("tool:", "file:")) else (
                "trusted" if role in {"system", "assistant"} else "untrusted"
            )
            # User question is trusted as *user* input (not as system).
            if role == "user" and source == "user":
                trust = "trusted"
        return ContextSegment(
            role=role,
            source=source,
            tool_id=tool_id,
            turn=turn,
            content=content or "",
            mac=mac,
            leaf=leaf,
            trust=trust,
        )

    def verify_segment(self, seg: ContextSegment) -> bool:
        blob = _canonical(seg.role, seg.source, seg.tool_id, seg.turn, seg.content)
        expected = mac_hex(self.key, blob)
        if not hmac.compare_digest(expected, seg.mac):
            return False
        return hmac.compare_digest(leaf_hash(seg.mac, seg.content), seg.leaf)

    def sign_messages(self, messages: Iterable[BaseMessage]) -> ContextBundle:
        segs: list[ContextSegment] = []
        for i, msg in enumerate(messages):
            role = _role_of(msg)
            source = _source_of(msg, i)
            trust = "trusted" if role in {"system", "assistant", "user"} else "untrusted"
            if role == "tool":
                trust = "untrusted"
            segs.append(
                self.sign_text(
                    role=role,
                    source=source,
                    tool_id=_tool_id_of(msg),
                    turn=i,
                    content=str(getattr(msg, "content", "") or ""),
                    trust=trust,
                )
            )
        root = merkle_root([s.leaf for s in segs])
        return ContextBundle(segments=segs, root=root, key_id=self.key_id)

    def authorised_redaction(self, bundle: ContextBundle, keep_turns: Iterable[int]) -> ContextBundle:
        """Keep a subset of signed leaves. Each kept leaf still verifies."""
        keep = set(int(t) for t in keep_turns)
        kept = [s for s in bundle.segments if s.turn in keep]
        return ContextBundle(
            segments=kept,
            root=bundle.root,
            key_id=bundle.key_id,
        )

    def forge_blocked(self, role: str, content: str, turn: int = 0) -> bool:
        """True iff an attacker *without the key* cannot produce a valid MAC."""
        fake = ContextSegment(
            role=role,
            source="orchestrator",
            tool_id="",
            turn=turn,
            content=content,
            mac="00" * 32,
            leaf="00" * 32,
            trust="trusted",
        )
        return not self.verify_segment(fake)


def _attach(msg: BaseMessage, seg: ContextSegment, verified: bool) -> BaseMessage:
    extra = dict(getattr(msg, "additional_kwargs", None) or {})
    extra.update(
        {
            "ctx_role": seg.role,
            "ctx_source": seg.source,
            "ctx_turn": seg.turn,
            "ctx_trust": seg.trust if verified else "untrusted",
            "ctx_mac": seg.mac,
            "ctx_verified": verified,
        }
    )
    msg.additional_kwargs = extra
    return msg


def segment_to_message(seg: ContextSegment, *, verified: bool) -> BaseMessage:
    content = seg.content
    if not verified or seg.trust == "untrusted":
        content = apply_ifc(content, untrusted=True)
        cls = HumanMessage
    elif seg.role == "system":
        cls = SystemMessage
    elif seg.role == "assistant":
        cls = AIMessage
    elif seg.role == "tool":
        cls = ToolMessage
        msg = ToolMessage(content=content, name=seg.tool_id or "tool", tool_call_id=seg.tool_id or f"t{seg.turn}")
        return _attach(msg, seg, verified)
    else:
        cls = HumanMessage
    return _attach(cls(content=content), seg, verified)


def apply_ifc(text: str, *, untrusted: bool) -> str:
    """Strip reserved control tokens from untrusted text; mark the blob."""
    if not untrusted:
        return text
    out = text or ""
    for tok in RESERVED_CONTROL_TOKENS:
        if tok in out:
            out = out.replace(tok, "[filtered-control-token]")
    # Neutralise fabricated role prefixes so a later flatten cannot resurrect them.
    for prefix in ("System:", "Tool:", "Assistant:", "Human:"):
        out = out.replace(prefix, f"[quoted-{prefix.rstrip(':').lower()}]")
    if not out.startswith(UNTRUSTED_PREFIX):
        out = UNTRUSTED_PREFIX + out
    return out


def message_trust(msg: BaseMessage) -> str:
    extra = getattr(msg, "additional_kwargs", None) or {}
    if extra.get("ctx_verified") is False:
        return "untrusted"
    return str(extra.get("ctx_trust") or "unknown")


def reconstruct_after_compression(
    auth: AuthenticatedContext,
    bundle: ContextBundle,
    compressed: list[BaseMessage],
) -> list[BaseMessage]:
    """Map flattened HumanMessage chunks back onto signed segments.

    A chunk is accepted as a given segment if the segment content is a
    substring of the chunk (or vice versa) *and* the MAC still verifies.
    Unmatched chunks — including fabricated ``System:`` lines that never had
    a MAC — are downgraded to untrusted user data with IFC applied.
    """
    out: list[BaseMessage] = []
    used: set[int] = set()
    for chunk in compressed:
        text = str(getattr(chunk, "content", "") or "")
        matched: ContextSegment | None = None
        for seg in bundle.segments:
            if seg.turn in used:
                continue
            if not text or not seg.content:
                continue
            if seg.content in text or text in seg.content:
                if auth.verify_segment(seg):
                    matched = seg
                    break
        if matched is not None:
            used.add(matched.turn)
            out.append(segment_to_message(matched, verified=True))
        else:
            fake = auth.sign_text(
                role="user",
                source="compressor-orphan",
                tool_id="",
                turn=-1,
                content=text,
                trust="untrusted",
            )
            out.append(segment_to_message(fake, verified=False))
    # Fail-closed: if compression dropped the system prompt, put it back first.
    sys_segs = [s for s in bundle.segments if s.role == "system" and auth.verify_segment(s)]
    have_system = any(_role_of(m) == "system" for m in out)
    if sys_segs and not have_system:
        out.insert(0, segment_to_message(sys_segs[0], verified=True))
    return out


def flatten_overflow_preserving_auth(
    auth: AuthenticatedContext,
    messages: list[BaseMessage],
    keep_fraction: float = 0.6,
) -> list[BaseMessage]:
    """Replacement for chatbot.py's ``str(messages)[-60%]`` flatten.

    Keeps the *tail* of the signed message list (not a string slice), restores
    roles for verified segments, and IFC-marks the rest. The system prompt is
    always retained if it was signed.
    """
    bundle = auth.sign_messages(messages)
    n = len(messages)
    start = max(0, n - max(1, int(n * keep_fraction)))
    tail = messages[start:]
    rebuilt = reconstruct_after_compression(auth, bundle, tail)
    return rebuilt


def wrap_sidecar(text: str, *, from_workspace: bool) -> str:
    """Sidecars written under workspace/ are attacker-reachable; mark them."""
    body = text or ""
    if from_workspace:
        return apply_ifc(body, untrusted=True)
    return body


def enabled_by_config(config: Any) -> bool:
    ctx = getattr(config, "context", None)
    if ctx is not None and bool(getattr(ctx, "authenticated_context", False)):
        return True
    return os.environ.get("OPENLENS_AUTHENTICATED_CONTEXT", "").strip() in {"1", "true", "TRUE", "yes"}


def key_from_config(config: Any) -> bytes:
    ctx = getattr(config, "context", None)
    explicit = getattr(ctx, "authenticated_context_key", None) if ctx is not None else None
    return derive_key(explicit)
