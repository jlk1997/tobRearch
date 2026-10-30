"""Confirm rebuilt web bundle contains writeback busy / stale copy."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")


def main() -> int:
    pw = os.environ["FS_SSH_PW"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    cmd = (
        "docker exec fengsuan-web-1 sh -c "
        "\"grep -o '刷新后再写回' /usr/share/nginx/html/assets/*.js | wc -l; "
        "grep -o '相关工作已写入手稿' /usr/share/nginx/html/assets/*.js | wc -l; "
        "grep -l '刷新后再写回' /usr/share/nginx/html/assets/*.js | head -1\""
    )
    _i, out, err = client.exec_command(cmd, timeout=30)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    print(text.rstrip(), flush=True)
    return 0 if out.channel.recv_exit_status() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
