"""只读核对线上镜像是否包含本轮改动。密码只从 FS_SSH_PW 读，不打印。"""

from __future__ import annotations

import base64
import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")

SCRIPT = """
import fengsuan.app.services.paper_import as pi
import fengsuan.app.api.v1.literature as lit
import inspect

print('do_import_queues', 'oa_pdf' in inspect.getsource(pi.do_import))
print('create_queues', 'oa_pdf' in inspect.getsource(lit.create_literature))
print('create_checks_files', 'LiteratureFile' in inspect.getsource(lit.create_literature))
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    payload = base64.b64encode(SCRIPT.encode("utf-8")).decode("ascii")
    cmd = (
        f"docker exec fengsuan-api-1 python -c \"import base64;"
        f"exec(base64.b64decode('{payload}').decode('utf-8'))\""
    )
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
