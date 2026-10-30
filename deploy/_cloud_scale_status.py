"""只读：容器启动时间、重启次数、最近日志。密码只从 FS_SSH_PW 读，不打印。"""
from __future__ import annotations

import os
import sys

import paramiko

sys.path.insert(0, os.path.dirname(__file__))
from _cloud_scale_capacity import HOST, run  # noqa: E402


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=os.environ["FS_SSH_PW"], timeout=25, allow_agent=False, look_for_keys=False)
    run(client, "date -Is; for c in $(docker ps -a --format '{{.Names}}' | grep '^fengsuan-'); do docker inspect -f '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}} status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' $c; done")
    tail = sys.argv[1] if len(sys.argv) > 1 else "15"
    for c in ("fengsuan-api-1", "fengsuan-worker-1", "fengsuan-grobid-1"):
        run(client, f"echo '--- {c}'; docker logs --tail {tail} {c} 2>&1 | cut -c1-300")
    return 0


if __name__ == "__main__":
    sys.exit(main())
