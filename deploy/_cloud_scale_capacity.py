"""容量检查：内存、磁盘、数据库大小、块数、向量索引、GROBID。只读，不改任何东西。"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("FS_SSH_HOST", "139.199.90.218")


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _i, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    return text


SQL = [
    "SELECT extname, extversion FROM pg_extension ORDER BY 1",
    "SELECT pg_size_pretty(pg_database_size('fengsuan')) AS db_size",
    "SELECT count(*) AS chunks, count(embedding) AS with_vec, sum(CASE WHEN lab_id = '' OR lab_id IS NULL THEN 1 ELSE 0 END) AS no_lab FROM kb_chunks",
    "SELECT embed_model, kind, count(*) FROM kb_chunks GROUP BY 1, 2 ORDER BY 3 DESC",
    "SELECT count(*) AS papers FROM literature_items WHERE deleted_at IS NULL",
    "SELECT (SELECT count(*) FROM authors) AS authors, (SELECT count(*) FROM authorships) AS authorships, (SELECT count(*) FROM literature_references) AS refs",
    "SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) FROM pg_indexes WHERE tablename IN ('kb_chunks','literature_items') AND (indexname LIKE '%hnsw%' OR indexname LIKE '%trgm%' OR indexname LIKE '%fts%') ORDER BY 1",
]

GROBID = (
    "import urllib.request,os;"
    "u=os.environ.get('GROBID_URL','http://grobid:8070');"
    "print('grobid', urllib.request.urlopen(u+'/api/isalive',timeout=5).read().decode())"
)


def main() -> int:
    pw = os.environ["FS_SSH_PW"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=25, allow_agent=False, look_for_keys=False)
    run(client, "free -m; nproc; df -h / /var/lib/docker 2>/dev/null | sort -u")
    run(client, "docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' | grep fengsuan")
    names = run(client, "docker ps --format '{{.Names}} {{.Status}}' | grep fengsuan")
    pg = next((n.split()[0] for n in names.splitlines() if "postgres" in n), "fengsuan-postgres-1")
    flags = " ".join(f'-c "{q}"' for q in SQL)
    run(client, f"docker exec {pg} psql -U fengsuan -d fengsuan {flags}")
    run(client, f"docker exec fengsuan-api-1 python -c \"{GROBID}\" 2>&1 | tail -1")
    run(client, "du -sh /var/lib/docker/volumes/fengsuan_fengsuan_data 2>/dev/null || true")
    run(client, "tail -2 /opt/fengsuan/backups/backup.log 2>/dev/null || echo no-backup-yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
