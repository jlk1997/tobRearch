from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from fengsuan.app.core.netguard import assert_git_https


def inject_token(url: str, token: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return url
    user = quote(token, safe="")
    netloc = f"{user}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def push_overleaf(url: str, token: str, files: dict[str, str]) -> str:
    """将 main.tex / refs.bib 推送到 Overleaf git。失败抛 RuntimeError，调用方回退 zip。"""
    if not url or not token:
        raise RuntimeError("missing_url_or_token")
    if any(c in token for c in " \n\r\t"):
        raise RuntimeError("bad_token")
    url = assert_git_https(url)
    auth = inject_token(url, token)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "repo"
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", "--", auth, str(dest)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if clone.returncode != 0:
            err = (clone.stderr or clone.stdout or "clone failed")[-400:]
            raise RuntimeError(err.replace(token, "***"))
        for name, content in files.items():
            (dest / name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True, capture_output=True, text=True)
        commit = subprocess.run(
            ["git", "-C", str(dest), "commit", "-m", "蜂算科研同步"],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            raise RuntimeError((commit.stderr or commit.stdout)[-400:])
        push = subprocess.run(["git", "-C", str(dest), "push"], capture_output=True, text=True, timeout=60)
        if push.returncode != 0:
            raise RuntimeError((push.stderr or push.stdout or "push failed")[-400:].replace(token, "***"))
    return "pushed"
