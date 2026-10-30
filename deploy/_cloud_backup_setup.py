"""装每日备份：凌晨 3:30 导出数据库（留 7 天），同步 PDF 等文件目录；装完立刻跑一次。

向量在库里一起导出；就算丢了也能由后台按原文重算（embed_model 标 hash 的块会自动补）。
密码只从环境变量 FS_SSH_PW 读，不打印。
"""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")

BACKUP_SH = r"""#!/bin/sh
set -eu
DEST=/opt/fengsuan/backups
DAY=$(date +%Y%m%d)
mkdir -p "$DEST/db" "$DEST/files"
PG=$(docker ps --format '{{.Names}}' | grep '^fengsuan-postgres' | head -1)
docker exec "$PG" pg_dump -U fengsuan -Fc fengsuan > "$DEST/db/fengsuan-$DAY.dump.part"
mv "$DEST/db/fengsuan-$DAY.dump.part" "$DEST/db/fengsuan-$DAY.dump"
find "$DEST/db" -name 'fengsuan-*.dump' -mtime +7 -delete
SRC=/var/lib/docker/volumes/fengsuan_fengsuan_data/_data
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete-after --exclude 'tmp/' --exclude 'jobs/' "$SRC/" "$DEST/files/"
else
  cp -au "$SRC/." "$DEST/files/"
fi
echo "$(date -Is) ok db=$(du -sh "$DEST/db/fengsuan-$DAY.dump" | cut -f1) files=$(du -sh "$DEST/files" | cut -f1)" >> "$DEST/backup.log"
"""

CRON = "30 3 * * * root /opt/fengsuan/backup.sh >> /opt/fengsuan/backups/cron.log 2>&1\n"


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> str:
    _i, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    print(f">> {cmd}\n{text}", end="" if text.endswith("\n") or not text else "\n", flush=True)
    if code != 0:
        raise RuntimeError(f"remote exit {code}")
    return text


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=os.environ["FS_SSH_PW"], timeout=25, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    run(client, "mkdir -p /opt/fengsuan/backups")
    with sftp.file("/opt/fengsuan/backup.sh", "w") as fh:
        fh.write(BACKUP_SH)
    with sftp.file("/etc/cron.d/fengsuan-backup", "w") as fh:
        fh.write(CRON)
    sftp.close()
    run(client, "chmod 755 /opt/fengsuan/backup.sh && chmod 644 /etc/cron.d/fengsuan-backup")
    run(client, "/opt/fengsuan/backup.sh && tail -1 /opt/fengsuan/backups/backup.log && ls -la /opt/fengsuan/backups/db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
