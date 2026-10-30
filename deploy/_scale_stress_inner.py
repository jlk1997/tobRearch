"""在 api 容器里跑，只连临时库 fengsuan_stress。由 _cloud_scale_stress.py 通过标准输入送进去。

造一个 5000 篇 / 约 20 万块的大组，外加一个只有 10 篇的小组（两个组共用块表），测：
入库耗时、建索引耗时、库和索引大小、混合检索 p50/p95、评测集分数（大组、小组各一遍）。
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = _url.rsplit("/", 1)[0] + "/fengsuan_stress"
os.environ["FILE_ROOT"] = "/tmp/fs_stress"
os.environ["FENGSUN_LLM_STUB"] = "1"

from sqlalchemy import text  # noqa: E402

from fengsuan.app.db.session import SessionLocal, get_engine, init_db  # noqa: E402

N_PAPERS = int(os.environ.get("FS_STRESS_PAPERS", "5000"))
PER = int(os.environ.get("FS_STRESS_CHUNKS_PER", "40"))
AUTHOR_BUDGET_S = int(os.environ.get("FS_STRESS_AUTHOR_BUDGET", "900"))
MODE = os.environ.get("FS_STRESS_MODE", "full")

if get_engine().url.database != "fengsuan_stress":
    print("refuse: not the stress database", flush=True)
    sys.exit(2)

EN = (
    "robust watermark image network detection adversarial training decoder encoder noise layer channel "
    "capacity forensic frequency spatial attention transformer diffusion generative model dataset benchmark "
    "evaluation accuracy robustness compression jpeg distortion geometric perspective camera screen print "
    "steganography steganalysis deepfake face video temporal consistency segmentation classification "
    "contrastive self-supervised pretraining finetuning embedding retrieval graph convolution optimization "
    "gradient loss regularization augmentation synthesis restoration denoising super-resolution inpainting "
    "watermarking fingerprint provenance tamper localization authentication cryptography hashing privacy"
).split()
ZH = (
    "图像 水印 网络 检测 模型 训练 数据集 鲁棒性 攻击 编码器 解码器 噪声 压缩 失真 频域 空域 注意力 "
    "生成 扩散 对抗 隐写 伪造 视频 人脸 分割 分类 对比学习 预训练 微调 检索 优化 损失 增强 修复 去噪 "
    "定位 认证 隐私 指纹 实验 结果 方法 性能 基线 消融 参数 精度 召回"
).split()
FIRST = "Alex Ben Chen Dan Eva Feng Gao Hui Ivan Jia Kai Lei Ming Ning Omar Ping Qiang Rui Sara Tao Umar Vera Xin Yan Zoe".split()
LAST = "Ahmed Brown Cai Deng Evans Fang Guo Huang Jiang Kim Lin Ma Nguyen Ou Park Qian Ren Sun Tang Ueda Vogel Wu Xu Yang Zhou".split()
UNIS = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa".split()
RESERVED = {"Wei Zhang", "Na Li", "Hao Chen", "Mei Wang", "Jun Liu", "Bob Stone"}


def now():
    return datetime.now(timezone.utc)


def words(rng: random.Random, n: int) -> str:
    out = []
    for _ in range(n):
        out.append(rng.choice(ZH) if rng.random() < 0.35 else rng.choice(EN))
    return " ".join(out)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(1000 * xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 1)


def build(report: dict) -> None:
    from fengsuan.app.models.identity import Org, User
    from fengsuan.app.models.kb import KbDocument
    from fengsuan.app.models.lab import Lab, LabMembership, ResearchDirection
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.services import search_index as si
    from fengsuan.app.services.biblio import sync_authorships
    from fengsuan.app.services.kb import get_or_create_kb, hash_embed
    from fengsuan.app.services.scale_eval import seed_eval_lab

    t0 = time.perf_counter()
    init_db()
    report["migrate_s"] = round(time.perf_counter() - t0, 1)

    db = SessionLocal()
    org = Org(name="stress")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email="stress@example.com", password_hash="x", display_name="stress")
    db.add(user)
    db.flush()
    big = Lab(org_id=org.id, name="big", code="stress-big", created_by=user.id)
    small = Lab(org_id=org.id, name="small", code="stress-small", created_by=user.id)
    db.add_all([big, small])
    db.flush()
    for lab in (big, small):
        db.add(LabMembership(lab_id=lab.id, user_id=user.id, role="teacher", joined_at=now()))
    kb = get_or_create_kb(db, big.id)
    get_or_create_kb(db, small.id)
    dirs = []
    for name in ("鲁棒水印", "隐写分析", "伪造检测", "生成模型", "图像修复", "隐私保护"):
        d = ResearchDirection(lab_id=big.id, name=name)
        db.add(d)
        dirs.append(d)
    db.commit()

    rng = random.Random(20260925)
    names = [f"{f} {l}" for f in FIRST for l in LAST if f"{f} {l}" not in RESERVED]
    rng.shuffle(names)
    names = names[:600]
    aff = {n: f"University of {UNIS[i % len(UNIS)]} {i % 37}" for i, n in enumerate(names)}

    items, docs, links, plan = [], [], [], []
    for i in range(N_PAPERS):
        iid, did = str(uuid.uuid4()), str(uuid.uuid4())
        people = rng.sample(names, rng.randint(2, 5))
        items.append(
            {
                "id": iid,
                "lab_id": big.id,
                "title": " ".join(rng.choice(EN) for _ in range(rng.randint(5, 9))).title() + f" {i}",
                "abstract": words(rng, 60),
                "authors_json": json.dumps(people),
                "venue": rng.choice(["CVPR", "ICCV", "ECCV", "NeurIPS", "TPAMI", "AAAI", "ACM MM"]),
                "publish_year": rng.randint(2012, 2026),
                "added_by": user.id,
                "source_channel": "manual",
                "read_level": rng.choice([0, 0, 1, 1, 1, 2, 3]),
            }
        )
        docs.append(
            {
                "id": did,
                "kb_id": kb.id,
                "literature_item_id": iid,
                "original_filename": f"p{i}.pdf",
                "status": "indexed",
                "chunk_count": PER,
                "uploaded_by": user.id,
                "source_kind": "paper",
            }
        )
        links.append({"item_id": iid, "direction_id": dirs[i % len(dirs)].id})
        plan.append((iid, did, people))
    t0 = time.perf_counter()
    db.execute(LiteratureItem.__table__.insert(), items)
    db.execute(KbDocument.__table__.insert(), docs)
    db.execute(LiteratureDirectionLink.__table__.insert(), links)
    db.commit()
    report["insert_papers_s"] = round(time.perf_counter() - t0, 1)

    with get_engine().begin() as conn:
        for idx in ("ix_kb_chunks_embedding_hnsw", "ix_kb_chunks_embedding_hnsw_v", "ix_kb_chunks_content_trgm", "ix_kb_chunks_content_fts"):
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))

    t0 = time.perf_counter()
    raw = get_engine().raw_connection()
    try:
        pg = raw.driver_connection
        cols = (
            "id, doc_id, seq, content, embedding_json, page_no, heading_path, metadata_json, "
            "lab_id, literature_item_id, kind, section, token_count, embed_model, embedding"
        )
        n_chunks = 0
        with pg.cursor() as cur:
            with cur.copy(f"COPY kb_chunks ({cols}) FROM STDIN") as cp:
                for iid, did, _people in plan:
                    for seq in range(PER):
                        body = words(rng, rng.randint(50, 90))
                        vec = hash_embed(body, 1024)
                        lit = "[" + ",".join("0" if x == 0 else f"{x:.5f}" for x in vec) + "]"
                        kind = "abstract" if seq == 0 else "body"
                        cp.write_row(
                            (
                                str(uuid.uuid4()), did, seq, body, "[]", 1 + seq // 3, f"{seq // 5 + 1} Section",
                                "{}", big.id, iid, kind, f"{seq // 5 + 1} Section", len(body) // 4, "hash", lit,
                            )
                        )
                        n_chunks += 1
        pg.commit()
    finally:
        raw.close()
    report["chunks"] = n_chunks
    report["copy_chunks_s"] = round(time.perf_counter() - t0, 1)

    get_engine().dispose()
    t0 = time.perf_counter()
    si.ensure_search_schema(get_engine())
    report["build_indexes_s"] = round(time.perf_counter() - t0, 1)
    with get_engine().connect() as conn:
        report["indexes"] = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) FROM pg_indexes "
                    "WHERE tablename = 'kb_chunks' AND indexname LIKE 'ix_kb_chunks_%' ORDER BY 1"
                )
            ).all()
        }
        report["db_size"] = conn.execute(text("SELECT pg_size_pretty(pg_database_size('fengsuan_stress'))")).scalar()
        report["pgvector"] = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
    print("STAGE build", json.dumps(report, ensure_ascii=False), flush=True)

    t0 = time.perf_counter()
    done = 0
    for start in range(0, len(plan), 250):
        ids = [p[0] for p in plan[start : start + 250]]
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids)).all():
            people = json.loads(it.authors_json)
            sync_authorships(db, it, [{"name": n, "affiliation": aff[n]} for n in people], evidence_source="stress")
            done += 1
        db.commit()
        if time.perf_counter() - t0 > AUTHOR_BUDGET_S:
            break
    report["authorships_papers"] = done
    report["authorships_s"] = round(time.perf_counter() - t0, 1)
    print("STAGE authors", done, report["authorships_s"], flush=True)

    seed_eval_lab(db, big.id, user.id)
    seed_eval_lab(db, small.id, user.id)
    db.commit()
    db.close()
    with get_engine().begin() as conn:
        conn.execute(text("ANALYZE"))


def _labs():
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import Lab
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.scale_eval import PAPERS

    db = SessionLocal()
    user = db.query(User).filter(User.email == "stress@example.com").one()
    big = db.query(Lab).filter(Lab.code == "stress-big").one()
    small = db.query(Lab).filter(Lab.code == "stress-small").one()
    keymaps = {}
    for lab in (big, small):
        rows = db.query(LiteratureItem.id, LiteratureItem.title).filter(LiteratureItem.lab_id == lab.id).all()
        by_title = {t: i for i, t in rows}
        keymaps[lab.id] = {p["key"]: by_title[p["title"]] for p in PAPERS}
    return db, user, big, small, keymaps


def measure(report: dict) -> None:
    from fengsuan.app.services import search_index as si
    from fengsuan.app.services.agent.registry import ToolContext, get_tool
    from fengsuan.app.services.kb import hash_embed, query_kb
    from fengsuan.app.services.scale_eval import EVAL_SET, run_eval

    db, user, big, small, keymaps = _labs()
    keymap_big, keymap_small = keymaps[big.id], keymaps[small.id]
    rng = random.Random(7)
    with get_engine().connect() as conn:
        q = si.vec_literal(hash_embed("可微噪声层模拟打印拍摄", 1024))
        plan_rows = conn.execute(
            text(
                "EXPLAIN ANALYZE SELECT c.id FROM kb_chunks c WHERE c.lab_id = :lab AND c.embedding IS NOT NULL "
                "ORDER BY (c.embedding::halfvec(1024)) <=> CAST(:q AS halfvec(1024)) LIMIT 100"
            ),
            {"lab": big.id, "q": q},
        ).all()
        report["vector_plan"] = [r[0] for r in plan_rows][:6]

    queries = [c["q"] for c in EVAL_SET if c["kind"] == "passage"]
    queries += [words(rng, rng.randint(3, 6)) for _ in range(51)]
    lat_kb, lat_tool = [], []
    for q in queries:
        t0 = time.perf_counter()
        query_kb(db, big.id, q, top_k=10, user_id=user.id, source_kinds=["paper"])
        lat_kb.append(time.perf_counter() - t0)
    passages = get_tool("search_passages")
    lit = get_tool("search_lab_literature")
    for q in queries[:30]:
        ctx = ToolContext(db=db, user_id=user.id, lab_id=big.id, scope="lab", role="teacher", extra={"question": q})
        t0 = time.perf_counter()
        passages.fn(ctx, {"query": q})
        if lit is not None:
            lit.fn(ctx, {"query": q})
        lat_tool.append(time.perf_counter() - t0)
    report["query_kb_ms"] = {"p50": pct(lat_kb, 0.5), "p95": pct(lat_kb, 0.95), "max": pct(lat_kb, 1.0), "n": len(lat_kb)}
    report["tools_ms"] = {"p50": pct(lat_tool, 0.5), "p95": pct(lat_tool, 0.95), "n": len(lat_tool)}
    report["query_kb_under_1500ms"] = round(sum(1 for x in lat_kb if x < 1.5) / len(lat_kb), 3)

    vec_small = si.vector_candidates(db, small.id, hash_embed("可微噪声层模拟打印拍摄", 1024), limit=100)
    report["small_lab_vector_candidates"] = None if vec_small is None else len(vec_small)

    for label, lab, keymap in (("eval_big", big, keymap_big), ("eval_small", small, keymap_small)):
        t0 = time.perf_counter()
        out = run_eval(db, lab.id, user.id, keymap)
        out["seconds"] = round(time.perf_counter() - t0, 1)
        out["misses"] = out["misses"][:8]
        report[label] = out
    db.close()
    report["mean_query_ms"] = round(1000 * statistics.mean(lat_kb), 1)


def probe(report: dict) -> None:
    """每条 SQL 计时，列出一次检索里最慢的几句。"""
    from sqlalchemy import event

    from fengsuan.app.services.kb import query_kb
    from fengsuan.app.services.scale_eval import EVAL_SET

    db, user, big, _small, _k = _labs()
    rng = random.Random(11)
    log: list[tuple[float, str]] = []
    starts: dict[int, float] = {}

    def before(conn, cursor, statement, params, context, executemany):
        starts[id(cursor)] = time.perf_counter()

    def after(conn, cursor, statement, params, context, executemany):
        t = time.perf_counter() - starts.pop(id(cursor), time.perf_counter())
        log.append((t, " ".join(statement.split())[:260]))

    event.listen(get_engine(), "before_cursor_execute", before)
    event.listen(get_engine(), "after_cursor_execute", after)
    queries = [c["q"] for c in EVAL_SET if c["kind"] == "passage"][:4] + [words(rng, rng.randint(3, 6)) for _ in range(4)]
    out = []
    for q in queries:
        log.clear()
        t0 = time.perf_counter()
        query_kb(db, big.id, q, top_k=10, user_id=user.id, source_kinds=["paper"])
        total = time.perf_counter() - t0
        slow = sorted(log, reverse=True)[:3]
        out.append(
            {
                "q": q[:40],
                "total_ms": round(1000 * total, 1),
                "sql_ms": round(1000 * sum(t for t, _ in log), 1),
                "n_sql": len(log),
                "slowest": [[round(1000 * t, 1), s] for t, s in slow],
            }
        )
    report["probe"] = out
    db.close()


def main() -> int:
    report: dict = {"mode": MODE, "papers": N_PAPERS, "chunks_per_paper": PER}
    if MODE in ("full", "build"):
        build(report)
    if MODE in ("full", "measure"):
        measure(report)
    if MODE == "probe":
        probe(report)
    print("STRESS_RESULT " + json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
