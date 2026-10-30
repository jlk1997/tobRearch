"""创建管理员（若尚未存在）以及一份可删除的引导实验室。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "platform"))

from fengsuan.app.db.session import SessionLocal, init_db
from fengsuan.app.services.seed import create_guide_lab, ensure_seed


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        ensure_seed(db)
        lab = create_guide_lab(db)
        print(f"引导实验室已创建：{lab.name} code={lab.code} id={lab.id}")
        print("可删除。真实实验室请用管理员账号新建空实验室。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
