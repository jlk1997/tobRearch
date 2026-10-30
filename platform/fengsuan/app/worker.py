"""独立 worker 进程入口：轮询知识库解析与重型作业。"""

from fengsuan.app.db.session import init_db
from fengsuan.app.services.worker import run_standalone_worker


def main() -> None:
    print("fengsuan sidecar worker booting", flush=True)
    init_db()
    print("fengsuan sidecar worker db ready", flush=True)
    run_standalone_worker()


if __name__ == "__main__":
    main()
