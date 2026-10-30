"""预拉 GROBID 镜像（走镜像源），拉完只报大小，不启动。密码只从 FS_SSH_PW 读，不打印。"""
from __future__ import annotations

import os
import sys

import paramiko

sys.path.insert(0, os.path.dirname(__file__))
from release import HOST, run  # noqa: E402

IMAGE = "lfoppiano/grobid:0.8.1"


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=os.environ["FS_SSH_PW"], timeout=25, allow_agent=False, look_for_keys=False)
    client.get_transport().set_keepalive(30)
    run(client, f"docker image inspect {IMAGE} >/dev/null 2>&1 || docker pull -q {IMAGE}", timeout=1800)
    run(client, f"docker image ls {IMAGE} --format '{{{{.Repository}}}}:{{{{.Tag}}}} {{{{.Size}}}}'")
    run(client, f"docker run --rm --entrypoint sh {IMAGE} -c 'command -v curl || echo no-curl; command -v bash || echo no-bash'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
