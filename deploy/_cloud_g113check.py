"""Verify G113-G115 landed on 18080. Do not print secrets."""
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
        "curl -sS -m 12 -o /dev/null -w 'home:%{http_code} health:' http://127.0.0.1:18080/; "
        "curl -sS -m 12 -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:18080/health",
        "docker exec fengsuan-api-1 python -c "
        "\"from fengsuan.app.core.concurrency import row_write_guard, _hold_lock_until_tx_end; "
        "print('row_lock', callable(row_write_guard), callable(_hold_lock_until_tx_end))\"",
        "docker exec fengsuan-api-1 python -c "
        "\"import inspect; from fengsuan.app.services.research_engine import persist_unused_diagnosis, research_unused_brief; "
        "src=inspect.getsource(persist_unused_diagnosis)+inspect.getsource(research_unused_brief); "
        "print('persist_db', 'row_write_guard' in src); print('titles_all', 'for item in shown' in src)\"",
        "docker exec fengsuan-web-1 sh -c "
        "\"grep -l writeBusy /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -o writeBusy /usr/share/nginx/html/assets/*.js | wc -l; "
        "grep -o unused_research /usr/share/nginx/html/assets/*.js | wc -l\"",
    ]
    for cmd in cmds:
        print(">>", cmd[:90], flush=True)
        _i, out, err = client.exec_command(cmd, timeout=40)
        text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
        print(text.rstrip(), flush=True)
        if out.channel.recv_exit_status() != 0:
            print("check_failed", cmd[:60], flush=True)
            return 1
    print("checked", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
