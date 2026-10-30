"""Verify G341-G347 jump contrast + author/DOI landed. No bittune."""
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
    helpers = run(
        client,
        f"docker exec {api} python -c \""
        "from fengsuan.app.api.v1.labs import jump_match_score; "
        "assert jump_match_score('bert', 'BERT survey') == 1; "
        "assert jump_match_score('bert', 'A survey of BERT') == 2; "
        "print('api-helpers:ok')\"",
    )
    if "api-helpers:ok" not in helpers:
        raise SystemExit("api helpers missing")
    js = run(
        client,
        f"docker exec {web} sh -c \""
        "grep -o '打开组内文献' /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -o '去文献检索' /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -o '正在找组内对象' /usr/share/nginx/html/assets/*.js | head -1; "
        "grep -c '跳转文献 / 手稿 / 任务 / 文献调研' /usr/share/nginx/html/assets/*.js || true\"",
    )
    if "打开组内文献" not in js or "去文献检索" not in js or "正在找组内对象" not in js:
        raise SystemExit("missing jump copy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
