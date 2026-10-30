from __future__ import annotations

import os

import paramiko

PW = os.environ["FS_SSH_PW"]
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.199.90.218", username="root", password=PW, timeout=25, allow_agent=False, look_for_keys=False)
cmds = [
    "grep -n unused_research /opt/fengsuan/web_next/src/pages/Graph.jsx | head",
    "grep -n directionId /opt/fengsuan/web_next/src/pages/Runs.jsx | head",
    "grep -n unusedTitles /opt/fengsuan/web_next/src/pages/Digest.jsx | head",
    "docker exec fengsuan-web-1 sh -c 'grep -l unused_research /usr/share/nginx/html/assets/*.js | head'",
    "docker exec fengsuan-api-1 python -c 'from fengsuan.app.services.writing import unused_note_from_pack; from fengsuan.app.services.research_engine import requester_ids_by_direction; print(\"api-ok\")'",
]
for cmd in cmds:
    print("=====", cmd[:100], flush=True)
    _i, out, err = client.exec_command(cmd, timeout=40)
    print(out.read().decode("utf-8", "replace"), flush=True)
    text = err.read().decode("utf-8", "replace")
    if text.strip():
        print("ERR", text[:400], flush=True)
