"""只读核对线上容器状态与镜像时间。密码只从 FS_SSH_PW 读，不打印。"""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")

CMD = r"""cd /opt/fengsuan/deploy && docker compose -f docker-compose.cloud.yml ps --format '{{.Name}} {{.State}} {{.Status}}' && docker images --format '{{.Repository}} {{.CreatedSince}}' | grep -E '^fengsuan-(api|worker|web) ' && ls -la /opt/fengsuan/backups/db | tail -3"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        password=os.environ["FS_SSH_PW"],
        timeout=25,
        allow_agent=False,
        look_for_keys=False,
    )
    _i, out, err = client.exec_command(CMD, timeout=180)
    print(out.read().decode("utf-8", "replace"))
    print(err.read().decode("utf-8", "replace"))
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
