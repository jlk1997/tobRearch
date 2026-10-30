"""只读探测：镜像源、能否拉到 GROBID 镜像。"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _i, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    return text


def main() -> int:
    pw = os.environ["FS_SSH_PW"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    run(client, "docker info 2>/dev/null | grep -A4 -i 'registry mirrors' || echo no-mirror")
    run(client, "cat /etc/docker/daemon.json 2>/dev/null || echo no-daemon-json")
    run(client, "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | head -30")
    run(client, "docker top fengsuan-worker-1 2>/dev/null | head -5")
    run(client, "docker logs --tail 20 fengsuan-worker-1 2>&1 | tail -20")
    return 0


if __name__ == "__main__":
    sys.exit(main())
