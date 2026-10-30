"""只读核对线上镜像是否包含 C215–C218 改动。密码只从 FS_SSH_PW 读，不打印。"""

from __future__ import annotations

import base64
import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")

SCRIPT = """
from fengsuan.app.services.web.common import SourceFailures
from fengsuan.app.services.web import cn, scholarly, search
from fengsuan.app.services.literature import search_empty_next
from fengsuan.app.services.frontier import _junk_paper
import inspect

print('SourceFailures', bool(SourceFailures))
print('search_scoped', 'scoped' in inspect.signature(search.search_web).parameters)
print('cn_parallel', 'ThreadPoolExecutor' in inspect.getsource(cn.search_cn))
print('cn_failed', 'failed_sources' in inspect.getsource(cn.search_cn))
print('empty_next_failed', 'failed_sources' in inspect.signature(search_empty_next).parameters)
print('junk_books', _junk_paper({'title': 'Handbook of Image Processing'}, code=False))
print('related_dict', 'isinstance(item, dict)' in inspect.getsource(scholarly.openalex_related))
r = scholarly._paper({'display_name': 'X', 'id': 'W1', 'is_retracted': True, 'primary_location': {'source': None}}, 'openalex')
print('retracted_flag', r.get('retracted'), 'venue_ok', r.get('venue') == '')
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    payload = base64.b64encode(SCRIPT.encode("utf-8")).decode("ascii")
    cmd = f"docker exec fengsuan-api-1 python -c \"import base64;exec(base64.b64decode('{payload}').decode('utf-8'))\""
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
    _i, out, err = client.exec_command(cmd, timeout=180)
    print(out.read().decode("utf-8", "replace"))
    print(err.read().decode("utf-8", "replace"))
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
