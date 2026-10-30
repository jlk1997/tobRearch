"""阅读路线：从已有前沿和缺口草拟，并标出本人读到哪一步。不新建表。"""

from __future__ import annotations

_STAGE_OF_SHELF = {
    "入门综述": "入门",
    "中文综述": "入门",
    "奠基经典": "进阶",
    "近两年代表": "前沿",
}


def draft_reading_route(papers: list | None, gaps: list | None = None, *, limit: int = 12) -> list[dict]:
    """入门放综述，进阶放经典，前沿放近两年。缺口里的篇补进门。已有路线不会在这里被改掉。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(stage: str, title: str, item_id: str = "") -> None:
        text = (title or "").strip()
        key = text.lower()
        if not text or key in seen or len(out) >= limit:
            return
        if sum(1 for row in out if row["stage"] == stage) >= 4:
            return
        seen.add(key)
        out.append({"stage": stage, "title": text[:200], "item_id": (item_id or "")[:64]})

    for paper in papers or []:
        if not isinstance(paper, dict):
            continue
        stage = _STAGE_OF_SHELF.get(str(paper.get("shelf") or ""), "进阶")
        add(stage, str(paper.get("title") or ""), str(paper.get("item_id") or paper.get("id") or ""))
    for gap in gaps or []:
        if isinstance(gap, dict):
            add("入门", str(gap.get("title") or ""), str(gap.get("item_id") or ""))
        else:
            add("入门", str(gap or ""))
    return out


def venue_outlets(papers: list | None, *, limit: int = 4) -> list[dict]:
    counts: dict[str, int] = {}
    for paper in papers or []:
        if not isinstance(paper, dict):
            continue
        venue = str(paper.get("venue") or "").strip()
        if len(venue) < 2:
            continue
        counts[venue] = counts.get(venue, 0) + 1
    ranked = sorted(counts, key=lambda name: (-counts[name], name))[:limit]
    return [{"venue": name, "count": counts[name]} for name in ranked]


def outlet_text(outlets: list | None) -> str:
    names = [str(row.get("venue") or "") for row in outlets or [] if isinstance(row, dict) and row.get("venue")]
    if not names:
        return ""
    return f"这几篇发在 {'、'.join(names[:4])}。可以当作投稿去向来看，不是排名。"


def annotate_route(route: list | None, readings: list | None) -> list[dict]:
    """给路线上的每一篇标上这个人自己的状态。不看别人。"""
    done: set[str] = set()
    reading: set[str] = set()
    for row in readings or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip().lower()
        item_id = str(row.get("item_id") or "")
        status = str(row.get("status") or "")
        bucket = done if status in {"finished", "read"} else reading if status == "reading" else None
        if bucket is None:
            continue
        if title:
            bucket.add(title)
        if item_id:
            bucket.add(item_id)
    out = []
    for step in route or []:
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if not title:
            continue
        item_id = str(step.get("item_id") or "")
        if title.lower() in done or (item_id and item_id in done):
            state = "已读完"
        elif title.lower() in reading or (item_id and item_id in reading):
            state = "在读"
        else:
            state = "还没读"
        out.append({**step, "title": title, "state": state})
    return out


def next_on_route(route: list | None, readings: list | None) -> dict:
    """跳过已经读完的，指出路线上还没读或正在读的下一篇。"""
    done: set[str] = set()
    reading: set[str] = set()
    for row in readings or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip().lower()
        item_id = str(row.get("item_id") or "")
        status = str(row.get("status") or "")
        bucket = done if status in {"finished", "read"} else reading if status == "reading" else None
        if bucket is None:
            continue
        if title:
            bucket.add(title)
        if item_id:
            bucket.add(item_id)
    nxt = None
    finished: list[str] = []
    for step in route or []:
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        item_id = str(step.get("item_id") or "")
        if not title:
            continue
        if title.lower() in done or (item_id and item_id in done):
            finished.append(title)
            continue
        if nxt is None:
            state = "在读" if title.lower() in reading or (item_id and item_id in reading) else "还没读"
            nxt = {"stage": step.get("stage") or "入门", "title": title, "item_id": item_id, "state": state}
    return {"next": nxt, "finished": finished[:4]}
