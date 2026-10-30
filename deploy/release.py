"""Parameterized cloud release. Rebuild images; never copy into a running container."""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")
DEFAULT_FILES = [
    "platform/fengsuan",
    "platform/requirements.txt",
    "platform/requirements-engine.txt",
    "modules/file1agent",
    "web_next",
    "openlens_ai",
    "deploy/docker-compose.cloud.yml",
    "deploy/Dockerfile.api",
    "deploy/Dockerfile.web",
    "deploy/Dockerfile.ngrok",
    "deploy/ngrok-entry.sh",
    "deploy/nginx.conf",
    ".dockerignore",
]
_SKIP_DIR = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "eval",
    "examples",
    ".git",
}


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(">>", cmd, flush=True)
    _i, out, err = client.exec_command(cmd, timeout=timeout, get_pty=True)
    chan = out.channel
    buf: list[str] = []
    deadline = time.time() + timeout
    while True:
        if chan.recv_ready():
            buf.append(chan.recv(65536).decode("utf-8", "replace"))
            print(buf[-1], end="", flush=True)
        if chan.recv_stderr_ready():
            buf.append(chan.recv_stderr(65536).decode("utf-8", "replace"))
            print(buf[-1], end="", flush=True)
        if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
            break
        if time.time() > deadline:
            raise TimeoutError(cmd)
        time.sleep(0.2)
    code = chan.recv_exit_status()
    text = "".join(buf)
    if code != 0:
        raise RuntimeError(f"remote exit {code}: {cmd}")
    return text


def _files(raw: str | None) -> list[str]:
    items = [x.strip().replace("\\", "/") for x in (raw or "").split(",") if x.strip()]
    return items or list(DEFAULT_FILES)


def _tar_filter(ti: tarfile.TarInfo):
    parts = set(Path(ti.name).parts)
    if parts & _SKIP_DIR:
        return None
    if ti.name.endswith((".pyc", ".pyo")):
        return None
    return ti


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild cloud images. No docker cp.")
    parser.add_argument("--files", default=os.environ.get("FS_RELEASE_FILES", ""), help="comma-separated paths")
    parser.add_argument(
        "--services",
        default=os.environ.get("FS_RELEASE_SERVICES", "api,worker,web"),
        help="compose services to rebuild",
    )
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pw = os.environ["FS_SSH_PW"]
    files = _files(args.files)
    missing = [rel for rel in files if not (ROOT / rel).exists()]
    if missing:
        print("missing", missing, flush=True)
        return 2
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel in files:
            tf.add(ROOT / rel, arcname=rel, filter=_tar_filter)
    blob = buf.getvalue()
    print("packed", round(len(blob) / 1024, 1), "KB", flush=True)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    client.get_transport().set_keepalive(30)
    sftp = client.open_sftp()
    with sftp.file("/tmp/fs_release.tgz", "wb") as fh:
        fh.write(blob)
    sftp.close()
    run(client, "mkdir -p /opt/fengsuan && tar -xzf /tmp/fs_release.tgz -C /opt/fengsuan")
    services = " ".join(s.strip() for s in args.services.split(",") if s.strip())
    run(
        client,
        "cd /opt/fengsuan/deploy && docker compose -f docker-compose.cloud.yml build "
        + services
        + " && docker compose -f docker-compose.cloud.yml up -d "
        + services,
        timeout=1800,
    )
    time.sleep(8)
    run(
        client,
        "curl -sS -m 12 -o /dev/null -w 'home:%{http_code} ' http://127.0.0.1:18080/; "
        "curl -sS -m 12 -o /dev/null -w 'health:%{http_code} ' http://127.0.0.1:18080/health; "
        "curl -sS -m 8 -o /dev/null -w 'metrics:%{http_code}\\n' http://127.0.0.1:18080/metrics || true",
    )
    print("landed (image rebuild, no docker cp)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
