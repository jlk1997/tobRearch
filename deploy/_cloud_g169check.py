"""Verify G169-G178 landed. No bittune."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 40) -> str:
    _i, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    return text


def main() -> int:
    pw = os.environ["FS_SSH_PW"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    run(
        client,
        "curl -sS -m 12 -o /dev/null -w 'home:%{http_code} ' http://127.0.0.1:18080/; "
        "curl -sS -m 12 -o /dev/null -w 'health:%{http_code} ' http://127.0.0.1:18080/health; "
        "curl -sS -m 8 -o /dev/null -w 'bittune:%{http_code}\\n' http://127.0.0.1/health || true",
    )
    names = run(client, "docker ps --format '{{.Names}}' | grep fengsuan | tr '\\n' ' '")
    api = "fengsuan-api-1" if "fengsuan-api-1" in names else "fengsuan-api"
    web = "fengsuan-web-1" if "fengsuan-web-1" in names else "fengsuan-web"
    run(
        client,
        f"docker exec {api} python -c \""
        "from fengsuan.app.services.literature import search_empty_next; "
        "from fengsuan.app.services.insight import reproduce_list_empty_next; "
        "from fengsuan.app.services.reproduce import empty_reproduce_spine; "
        "n=search_empty_next('x','q',direction_id='d',includes_children=True); "
        "assert '含子课题' in (n.get('title') or ''); "
        "assert 'direction_id=d' in (n.get('steps') or [{}])[0].get('href',''); "
        "e=reproduce_list_empty_next('x',empty_kind='filter',direction_id='d',includes_children=True); "
        "assert '含子课题' in (e.get('title') or '')+(e.get('text') or ''); "
        "s=empty_reproduce_spine('x',direction_id='d'); "
        "assert 'direction_id=d' in (s.get('not_run_href') or ''); "
        "print('api-helpers:ok')\"",
    )
    run(
        client,
        f"docker exec {web} sh -c \""
        "grep -o '本方向及子课题的复现' /usr/share/nginx/html/assets/*.js | head -2; "
        "grep -o '当前按父方向筛选，含子课题' /usr/share/nginx/html/assets/*.js | head -2; "
        "grep -o 'direction_id' /usr/share/nginx/html/assets/*.js | head -1\"",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
