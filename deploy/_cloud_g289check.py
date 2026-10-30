"""Verify G289-G295 landed. No bittune."""
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
        "from fengsuan.app.services.insight import list_href_label, research_list_empty_next; "
        "assert list_href_label('/x?unused=1') == '综述没写到'; "
        "assert research_list_empty_next('lab', empty_kind='library')['title'] == '还没有文献调研'; "
        "print('api-helpers:ok')\"",
    )
    js = run(
        client,
        f"docker exec {web} sh -c \""
        "grep -o '提一个问题，自动写综述' /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -o 'fs-hint-btn' /usr/share/nginx/html/assets/*.js /usr/share/nginx/html/assets/*.css | head -1; "
        "grep -o '文献调研' /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -o '还没做完' /usr/share/nginx/html/assets/*.js | head -1\"",
    )
    if "提一个问题，自动写综述" not in js:
        raise SystemExit("missing short runs lead")
    if "fs-hint-btn" not in js:
        raise SystemExit("missing hint button")
    return 0


if __name__ == "__main__":
    sys.exit(main())
