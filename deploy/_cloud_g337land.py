"""Land G337-G340 directions list layout. Rebuild web. No bittune."""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")
FILES = [
    "web_next/src/pages/Directions.jsx",
    "web_next/src/index.css",
]


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
            print(buf[-1].encode("utf-8", "replace").decode(sys.stdout.encoding or "utf-8", "replace"), end="", flush=True)
        if chan.recv_stderr_ready():
            buf.append(chan.recv_stderr(65536).decode("utf-8", "replace"))
            print(buf[-1].encode("utf-8", "replace").decode(sys.stdout.encoding or "utf-8", "replace"), end="", flush=True)
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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pw = os.environ["FS_SSH_PW"]
    missing = [rel for rel in FILES if not (ROOT / rel).is_file()]
    if missing:
        print("missing", missing, flush=True)
        return 2
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel in FILES:
            tf.add(ROOT / rel, arcname=rel)
    blob = buf.getvalue()
    print("packed", round(len(blob) / 1024, 1), "KB", flush=True)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    client.get_transport().set_keepalive(30)
    sftp = client.open_sftp()
    with sftp.file("/tmp/fs_g337.tgz", "wb") as fh:
        fh.write(blob)
    sftp.close()
    run(client, "mkdir -p /opt/fengsuan && tar -xzf /tmp/fs_g337.tgz -C /opt/fengsuan")
    run(
        client,
        "cd /opt/fengsuan/deploy && docker compose -f docker-compose.cloud.yml build web && "
        "docker compose -f docker-compose.cloud.yml up -d web",
        timeout=420,
    )
    time.sleep(4)
    run(
        client,
        "curl -sS -m 12 -o /dev/null -w 'home:%{http_code} ' http://127.0.0.1:18080/; "
        "curl -sS -m 12 -o /dev/null -w 'health:%{http_code} ' http://127.0.0.1:18080/health; "
        "curl -sS -m 8 -o /dev/null -w 'bittune:%{http_code}\\n' http://127.0.0.1/health || true",
    )
    print("landed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
