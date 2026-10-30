"""备份蜂算科研数据目录与数据库（SQLite 文件复制或 PostgreSQL pg_dump）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    dest_root = Path(os.environ.get("BACKUP_DIR") or (ROOT / "data" / "backups"))
    dest = dest_root / utc_stamp()
    dest.mkdir(parents=True, exist_ok=True)
    data_dir = Path(os.environ.get("FILE_ROOT") or (ROOT / "data"))
    if data_dir.exists():
        shutil.copytree(data_dir, dest / "files", dirs_exist_ok=True, ignore=shutil.ignore_patterns("backups"))
    url = os.environ.get("DATABASE_URL") or ""
    if url.startswith("sqlite"):
        db_path = url.split("///")[-1]
        src = Path(db_path)
        if not src.is_absolute():
            src = ROOT / src
        if src.exists():
            shutil.copy2(src, dest / "fengsuan.db")
    elif url.startswith("postgresql"):
        dump = dest / "fengsuan.dump"
        subprocess.check_call(["pg_dump", url.replace("postgresql+psycopg://", "postgresql://"), "-f", str(dump)])
    print(str(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
