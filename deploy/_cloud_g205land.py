"""Land G205-G211 digest items by object direction. Rebuild web; hot-patch api/worker. No bittune."""
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
    "platform/fengsuan/app/services/insight.py",
    "web_next/src/pages/Digest.jsx",
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
    with sftp.file("/tmp/fs_g205.tgz", "wb") as fh:
        fh.write(blob)
    sftp.close()
    run(client, "mkdir -p /opt/fengsuan && tar -xzf /tmp/fs_g205.tgz -C /opt/fengsuan")
    run(
        client,
        "cd /opt/fengsuan/deploy && docker compose -f docker-compose.cloud.yml build web && "
        "docker compose -f docker-compose.cloud.yml up -d web",
        timeout=420,
    )
    names = run(client, "docker ps --format '{{.Names}}' | grep fengsuan | tr '\\n' ' '")
    api = "fengsuan-api-1" if "fengsuan-api-1" in names else "fengsuan-api"
    worker = "fengsuan-worker-1" if "fengsuan-worker-1" in names else "fengsuan-worker"
    for rel in FILES:
        if not rel.startswith("platform/"):
            continue
        parent = str(Path(rel).parent).replace("\\", "/")
        run(client, f"docker exec {api} mkdir -p /app/{parent}")
        if "worker" in names:
            run(client, f"docker exec {worker} mkdir -p /app/{parent}")
        run(client, f"docker cp /opt/fengsuan/{rel} {api}:/app/{rel}")
        if "worker" in names:
            run(client, f"docker cp /opt/fengsuan/{rel} {worker}:/app/{rel}")
    run(client, f"docker restart {api} {worker}")
    time.sleep(10)
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
