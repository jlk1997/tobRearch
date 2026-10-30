"""压力测试：在云上开一个临时库 fengsuan_stress，造 5000 篇论文跑检索和评测，跑完删库。

不碰正式库 fengsuan。密码只从环境变量 FS_SSH_PW 读，不打印。
用法：python deploy/_cloud_scale_stress.py [--papers 5000] [--per 40]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")
INNER = Path(__file__).with_name("_scale_stress_inner.py")
MIN_FREE_MB = 3500


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120, check: bool = True) -> str:
    print(">>", cmd, flush=True)
    _i, out, _e = client.exec_command(cmd, timeout=timeout, get_pty=True)
    chan = out.channel
    buf: list[str] = []
    deadline = time.time() + timeout
    while True:
        if chan.recv_ready():
            buf.append(chan.recv(65536).decode("utf-8", "replace"))
            print(buf[-1], end="", flush=True)
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        if time.time() > deadline:
            raise TimeoutError(cmd)
        time.sleep(0.3)
    code = chan.recv_exit_status()
    if check and code != 0:
        raise RuntimeError(f"remote exit {code}")
    return "".join(buf)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers", type=int, default=5000)
    parser.add_argument("--per", type=int, default=40)
    parser.add_argument("--mode", choices=["full", "build", "measure", "probe", "drop"], default="full")
    parser.add_argument("--keep", action="store_true", help="跑完不删临时库，方便接着 measure / probe")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=os.environ["FS_SSH_PW"], timeout=25, allow_agent=False, look_for_keys=False)
    client.get_transport().set_keepalive(30)
    free = run(client, "free -m | awk '/Mem:/{print $7}'").strip().split()[-1]
    if int(free) < MIN_FREE_MB:
        print(f"only {free} MB free, need {MIN_FREE_MB}; skip", flush=True)
        return 3
    names = run(client, "docker ps --format '{{.Names}}' | grep '^fengsuan-' | tr '\\n' ' '")
    pg = next((n for n in names.split() if "postgres" in n), "fengsuan-postgres-1")
    api = next((n for n in names.split() if n.startswith("fengsuan-api")), "fengsuan-api-1")
    psql = f"docker exec {pg} psql -v ON_ERROR_STOP=1 -U fengsuan"
    drop = f'{psql} -d postgres -c "DROP DATABASE IF EXISTS fengsuan_stress WITH (FORCE)"'
    if args.mode == "drop":
        run(client, drop, check=False)
        return 0
    if args.mode in ("full", "build"):
        run(client, drop)
        run(client, f'{psql} -d postgres -c "CREATE DATABASE fengsuan_stress"')
        run(
            client,
            f'{psql} -d fengsuan_stress -c "CREATE EXTENSION IF NOT EXISTS vector" '
            '-c "CREATE EXTENSION IF NOT EXISTS pg_trgm" '
            "-c \"ALTER DATABASE fengsuan_stress SET maintenance_work_mem = '1GB'\" "
            '-c "ALTER DATABASE fengsuan_stress SET max_parallel_maintenance_workers = 0"',
        )
    sftp = client.open_sftp()
    with sftp.file("/tmp/fs_stress_inner.py", "wb") as fh:
        fh.write(INNER.read_bytes())
    sftp.close()
    try:
        run(
            client,
            f"docker exec -i -e FS_STRESS_PAPERS={args.papers} -e FS_STRESS_CHUNKS_PER={args.per} "
            f"-e FS_STRESS_MODE={args.mode} {api} python -u - < /tmp/fs_stress_inner.py",
            timeout=5400,
        )
    finally:
        if not args.keep:
            run(client, drop, check=False)
        run(client, "rm -f /tmp/fs_stress_inner.py; free -m | head -2", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
