"""Verify G116-G118 landed on 18080."""
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
    cmds = [
        "curl -sS -m 12 -o /dev/null -w 'home:%{http_code} ' http://127.0.0.1:18080/; "
        "curl -sS -m 12 -o /dev/null -w 'health:%{http_code}\\n' http://127.0.0.1:18080/health",
        "docker exec fengsuan-api-1 python -c "
        "\"from fengsuan.app.services.research_engine import unused_list_href, unused_view, scoped_unused_pack; "
        "print('href', unused_list_href('L', requester_id='U')); "
        "print('view', callable(unused_view), callable(scoped_unused_pack))\"",
        "docker exec fengsuan-web-1 sh -c "
        "\"grep -o '该成员的未点名' /usr/share/nginx/html/assets/*.js | wc -l; "
        "grep -o requester_id /usr/share/nginx/html/assets/*.js | wc -l\"",
    ]
    for cmd in cmds:
        print(">>", cmd[:88], flush=True)
        _i, out, err = client.exec_command(cmd, timeout=40)
        text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
        print(text.rstrip(), flush=True)
        if out.channel.recv_exit_status() != 0:
            return 1
    print("checked", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
