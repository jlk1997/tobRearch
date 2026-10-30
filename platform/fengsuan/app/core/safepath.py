"""Filename and upload containment. Uploads must stay under file_root."""

from __future__ import annotations

import re
from pathlib import Path

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
_UNSAFE = re.compile(r"[^\w\u4e00-\u9fff.\- ()\[\]]+", re.UNICODE)


def safe_filename(name: str, default: str = "file.bin") -> str:
    raw = (name or "").replace("\\", "/").split("/")[-1]
    raw = raw.replace("\x00", "").strip()
    if not raw or raw in {".", ".."}:
        return default
    cleaned = _UNSAFE.sub("_", raw).strip(" .") or default
    if cleaned in {".", ".."}:
        return default
    return cleaned[:180]


def ensure_under(root: Path, target: Path) -> Path:
    root_r = root.resolve()
    target_r = target.resolve()
    target_r.relative_to(root_r)
    return target_r


def write_bytes_under(dest_dir: Path, filename: str, content: bytes, *, default: str = "file.bin") -> Path:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("too_large")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(filename, default)
    ensure_under(dest_dir, dest)
    dest.write_bytes(content)
    return dest


def resolve_under(root: str | Path, rel: str) -> Path:
    if not rel or "\x00" in rel:
        raise ValueError("bad_path")
    rel_path = Path(rel.replace("\\", "/"))
    if rel_path.is_absolute() or getattr(rel_path, "anchor", None):
        raise ValueError("absolute")
    root_p = Path(root).resolve()
    target = (root_p / rel_path).resolve()
    target.relative_to(root_p)
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target


def clamp_page(page: int = 1, page_size: int = 50, *, max_size: int = 100) -> tuple[int, int]:
    p = max(1, int(page or 1))
    s = max(1, min(int(page_size or 50), max_size))
    return p, s


def read_upload_limited(upload, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("too_large")
    return data
