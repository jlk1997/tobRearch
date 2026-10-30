"""方向里的主题、每周变化。

方向里论文多了，就按篇级向量分成 3 到 8 个主题，每个主题几篇代表作和一句话，
存在 `research_directions.brief_json` 的 themes 里。新论文只改变了某一个主题时，只重写那一个。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import timedelta

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.lab import ResearchDirection
from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
from fengsuan.app.services.lab_brief import _dump, _loads

THEME_MIN_PAPERS = 12
MATCH_KEEP = 0.75
_MAX_ITEMS = 6000
_STOP = {
    "using", "based", "learning", "deep", "towards", "toward", "via", "with", "from", "for", "and", "the",
    "network", "networks", "model", "models", "method", "methods", "approach", "new", "novel", "study",
    "analysis", "system", "systems", "paper", "large", "efficient", "robust", "improved",
}


def _scope(db: Session, direction: ResearchDirection) -> list[str]:
    from fengsuan.app.services.directions import direction_scope_ids

    return direction_scope_ids(db, direction.id, include_children=True) or [direction.id]


def _embedded_query(db: Session, direction: ResearchDirection):
    linked = db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(_scope(db, direction)))
    return db.query(LiteratureItem).filter(
        LiteratureItem.id.in_(linked),
        LiteratureItem.deleted_at.is_(None),
        LiteratureItem.paper_embedding_json.isnot(None),
        LiteratureItem.paper_embedding_json != "",
        LiteratureItem.paper_embedding_json != "[]",
    )


def _load_items(db: Session, direction: ResearchDirection) -> list[tuple[str, str, list[float]]]:
    rows = (
        _embedded_query(db, direction)
        .with_entities(LiteratureItem.id, LiteratureItem.title, LiteratureItem.paper_embedding_json)
        .order_by(LiteratureItem.id.asc())
        .limit(_MAX_ITEMS)
        .all()
    )
    out = []
    for iid, title, raw in rows:
        try:
            vec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(vec, list) and vec:
            out.append((iid, title or "", [float(x) for x in vec]))
    return out


def _kmeans(vecs: list[list[float]], k: int, seed: int, iters: int = 15) -> tuple[list[int], list[list[float]]]:
    """余弦 k-means。k-means++ 起点，种子固定，同一批论文每次分出来一样。"""
    import numpy as np

    dim = min(len(v) for v in vecs)
    x = np.array([v[:dim] for v in vecs], dtype=np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-9
    rng = np.random.default_rng(seed)
    centers = [x[int(rng.integers(len(x)))]]
    for _ in range(1, k):
        sims = np.max(x @ np.array(centers).T, axis=1)
        dist = np.clip(1.0 - sims, 0.0, None) ** 2
        total = float(dist.sum())
        pick = int(rng.choice(len(x), p=dist / total)) if total > 0 else int(rng.integers(len(x)))
        centers.append(x[pick])
    c = np.array(centers)
    labels = np.zeros(len(x), dtype=int)
    for step in range(iters):
        new = np.argmax(x @ c.T, axis=1)
        if step > 0 and np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                m = members.mean(axis=0)
                c[j] = m / (np.linalg.norm(m) + 1e-9)
    return labels.tolist(), c.tolist()


def _keyword_name(titles: list[str]) -> str:
    from fengsuan.app.services.search_index import search_terms

    counts: Counter = Counter()
    for t in titles:
        for w in set(search_terms(t, cap=12)):
            if w.lower() not in _STOP and not w.isdigit():
                counts[w.lower()] += 1
    top = [w for w, n in counts.most_common(3) if n >= 2]
    return " / ".join(top[:2]) if top else ""


def _name_theme(db: Session, direction: ResearchDirection, reps: list[dict]) -> tuple[str, str]:
    """有模型且额度够时让模型起名；否则用题名里共同的词。"""
    titles = [r["title"] for r in reps if r.get("title")]
    fallback = _keyword_name(titles) or (titles[0][:24] if titles else "")
    from fengsuan.app.services.llm import llm_stub_enabled

    if llm_stub_enabled():
        return fallback, ""
    from fengsuan.app.services.digest import digest_room

    if not digest_room(db, direction.lab_id):
        return fallback, ""
    from fengsuan.app.models.lab import Lab
    from fengsuan.app.services.analysis import extract_json_object
    from fengsuan.app.services.llm import chat

    lab = db.get(Lab, direction.lab_id)
    lines = "\n".join(f"- {r['title']}：{r.get('line') or ''}" for r in reps[:6])
    try:
        raw = chat(
            db,
            user_id=direction.owner_user_id or (lab.created_by if lab else ""),
            lab_id=direction.lab_id,
            messages=[
                {
                    "role": "system",
                    "content": "给一组论文起一个中文主题名（不超过 12 个字）和一句话说明它们共同在做什么。只输出 JSON：{\"name\": \"...\", \"line\": \"...\"}。只根据给出的题名和笔记，不要编。",
                },
                {"role": "user", "content": f"方向：{direction.name}\n这组论文：\n{lines}"},
            ],
            feature="brief",
            json_mode=True,
        )
    except Exception:
        return fallback, ""
    got = extract_json_object(raw) or {}
    name = str(got.get("name") or "").strip()[:24] or fallback
    line = str(got.get("line") or "").strip()[:160]
    return name, line


def _rep_line(db: Session, item_id: str) -> str:
    from fengsuan.app.services.analysis import best_method_analysis, parse_card
    from fengsuan.app.services.lab_brief import plain_bits

    row = best_method_analysis(db, item_id)
    return plain_bits(parse_card(row) if row else {}).get("one_line") or ""


def refresh_direction_themes(db: Session, direction: ResearchDirection, *, force: bool = False) -> list[dict]:
    items = _load_items(db, direction)
    brief = _loads(direction.brief_json)
    n = len(items)
    if n < THEME_MIN_PAPERS:
        brief["themes"] = []
        brief["theme_members"] = {}
        brief["themes_n"] = n
        brief["themes_at"] = utcnow().isoformat()
        direction.brief_json = _dump(brief)
        return []
    k = max(3, min(8, round(math.sqrt(n / 3))))
    k = min(k, max(1, n // 4))
    seed = int(hashlib.sha1(direction.id.encode()).hexdigest()[:8], 16)
    labels, centers = _kmeans([v for _i, _t, v in items], k, seed)
    old = {t.get("key"): t for t in brief.get("themes") or [] if isinstance(t, dict) and t.get("key")}
    old_members = {k2: set(v) for k2, v in (brief.get("theme_members") or {}).items() if isinstance(v, list)}
    themes: list[dict] = []
    members_out: dict[str, list[str]] = {}
    used_keys: set[str] = set()
    for j in range(k):
        idx = [i for i, lab in enumerate(labels) if lab == j]
        if not idx:
            continue
        ids = [items[i][0] for i in idx]
        center = centers[j]

        def _sim(i: int) -> float:
            v = items[i][2]
            dim = min(len(v), len(center))
            norm = math.sqrt(sum(x * x for x in v[:dim])) or 1.0
            return sum(v[d] * center[d] for d in range(dim)) / norm

        top = sorted(idx, key=_sim, reverse=True)[:4]
        reps = [{"id": items[i][0], "title": items[i][1]} for i in top]
        mine = set(ids)
        best_key, best_j = "", 0.0
        for key, prev in old_members.items():
            if key in used_keys or not prev:
                continue
            jac = len(mine & prev) / len(mine | prev)
            if jac > best_j:
                best_key, best_j = key, jac
        if best_key and best_j >= MATCH_KEEP and not force and old.get(best_key, {}).get("name"):
            prev = old[best_key]
            key, name, line, rewritten = best_key, prev.get("name") or "", prev.get("line") or "", False
        else:
            for r in reps:
                r["line"] = _rep_line(db, r["id"])
            name, line = _name_theme(db, direction, reps)
            key = hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:12]
            rewritten = True
        used_keys.add(key)
        members_out[key] = ids
        themes.append(
            {
                "key": key,
                "name": name or f"主题 {len(themes) + 1}",
                "line": line,
                "size": len(ids),
                "reps": [{"id": r["id"], "title": r["title"]} for r in reps[:3]],
                "rewritten": rewritten,
            }
        )
    themes.sort(key=lambda t: -t["size"])
    brief["themes"] = themes
    brief["theme_members"] = members_out
    brief["themes_n"] = n
    brief["themes_at"] = utcnow().isoformat()
    direction.brief_json = _dump(brief)
    return themes


def refresh_one_themes(db: Session) -> str:
    """每轮最多重分一个方向：篇数变了一成（至少 5 篇）才重分。"""
    dirs = db.query(ResearchDirection).filter(ResearchDirection.deleted_at.is_(None)).order_by(ResearchDirection.updated_at.asc()).limit(200).all()
    for d in dirs:
        brief = _loads(d.brief_json)
        now_n = _embedded_query(db, d).count()
        prev = brief.get("themes_n")
        if prev is None:
            due = now_n >= THEME_MIN_PAPERS
        else:
            due = abs(now_n - int(prev)) >= max(5, int(int(prev) * 0.1))
        if due:
            refresh_direction_themes(db, d)
            return d.id
    return ""


def public_themes(brief: dict) -> list[dict]:
    return [
        {k: t.get(k) for k in ("key", "name", "line", "size", "reps")}
        for t in brief.get("themes") or []
        if isinstance(t, dict)
    ]


def item_theme_map(db: Session, lab_id: str) -> dict[str, dict]:
    """item_id → {direction, theme 名}。给每周变化和综述分组用。"""
    out: dict[str, dict] = {}
    for d in db.query(ResearchDirection).filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None)).all():
        brief = _loads(d.brief_json)
        names = {t.get("key"): t.get("name") for t in brief.get("themes") or [] if isinstance(t, dict)}
        for key, ids in (brief.get("theme_members") or {}).items():
            for iid in ids if isinstance(ids, list) else []:
                if iid not in out and names.get(key):
                    out[iid] = {"direction_id": d.id, "direction": d.name, "theme": names[key], "key": key}
    return out


def week_change(db: Session, lab_id: str, *, days: int = 7) -> dict:
    """这周进来几篇、多了哪个主题、谁有新工作。一段话说完。"""
    from fengsuan.app.models.literature import Authorship
    from fengsuan.app.services.authors import live_author, member_author_ids

    since = utcnow() - timedelta(days=max(1, days))
    rows = (
        db.query(LiteratureItem.id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None), LiteratureItem.created_at >= since)
        .all()
    )
    ids = [r[0] for r in rows]
    if not ids:
        return {"count": 0, "text": "这周没有新论文进来。", "theme": "", "people": []}
    themes = item_theme_map(db, lab_id)
    counts = Counter(themes[i]["theme"] for i in ids if i in themes)
    top_theme, top_n = (counts.most_common(1)[0] if counts else ("", 0))
    if not top_theme:
        dir_counts = Counter(
            name
            for (name,) in db.query(ResearchDirection.name)
            .join(LiteratureDirectionLink, LiteratureDirectionLink.direction_id == ResearchDirection.id)
            .filter(LiteratureDirectionLink.item_id.in_(ids), ResearchDirection.deleted_at.is_(None))
            .all()
        )
        if dir_counts:
            top_theme, top_n = dir_counts.most_common(1)[0]
    claimed = member_author_ids(db, lab_id)
    ship_rows = (
        db.query(Authorship.author_id)
        .filter(Authorship.item_id.in_(ids), Authorship.author_id.isnot(None))
        .all()
    )
    by_author = Counter(aid for (aid,) in ship_rows)
    member_names: list[str] = []
    if claimed:
        from fengsuan.app.models.identity import User

        for aid in by_author:
            uid = claimed.get(aid)
            if uid:
                u = db.get(User, uid)
                if u is not None:
                    member_names.append(u.display_name or u.email or "")
    busy_author = ""
    for aid, n in by_author.most_common(3):
        if n >= 2 and aid not in claimed:
            a = live_author(db, aid)
            if a is not None:
                busy_author = f"{a.display_name} 的论文进了 {n} 篇"
                break
    parts = [f"这周进来 {len(ids)} 篇"]
    if top_theme and top_n:
        parts.append(f"多的是「{top_theme}」（{top_n} 篇）")
    if member_names:
        parts.append("组里" + "、".join(dict.fromkeys(n for n in member_names if n)) + "有新署名的论文")
    if busy_author:
        parts.append(busy_author)
    text = "，".join(parts) + "。"
    return {
        "count": len(ids),
        "text": re.sub(r"\s+", " ", text),
        "theme": top_theme,
        "theme_count": top_n,
        "people": [n for n in dict.fromkeys(member_names) if n],
        "author_line": busy_author,
    }
