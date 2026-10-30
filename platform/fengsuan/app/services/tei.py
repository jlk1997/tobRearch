"""GROBID TEI 解析与按章节切块。

GROBID 给的是结构：标题、作者（含单位、ORCID）、摘要、章节段落（带页坐标）、图表说明、参考文献。
这里只做两件事：把 TEI 读成普通字典；把正文按章节、段落切成 300–600 token 的块，保留真实页码。
读不出来就返回空，调用方退回按页切。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

NS = {"tei": "http://www.tei-c.org/ns/1.0"}
_T = "{http://www.tei-c.org/ns/1.0}"

MIN_TOKENS = 300
MAX_TOKENS = 600


def _text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _page_of(el) -> int | None:
    coords = el.get("coords") if el is not None else None
    if not coords:
        return None
    m = re.match(r"\s*(\d+)\s*,", coords)
    return int(m.group(1)) if m else None


def _person(pers) -> dict:
    if pers is None:
        return {}
    fore = " ".join(_text(f) for f in pers.findall("tei:forename", NS) if _text(f))
    sur = _text(pers.find("tei:surname", NS))
    name = " ".join(x for x in (fore, sur) if x).strip()
    return {"name": name, "forename": fore, "surname": sur}


def _author(el) -> dict | None:
    info = _person(el.find("tei:persName", NS))
    if not info.get("name"):
        return None
    orcid = ""
    for idno in el.findall("tei:idno", NS):
        if (idno.get("type") or "").lower() == "orcid":
            orcid = _normalize_orcid(_text(idno))
    affs = []
    for aff in el.findall("tei:affiliation", NS):
        orgs = [_text(o) for o in aff.findall("tei:orgName", NS) if _text(o)]
        line = ", ".join(orgs) or _text(aff)
        if line:
            affs.append(line[:300])
    info["orcid"] = orcid
    info["affiliation"] = "; ".join(affs)[:500]
    info["email"] = _text(el.find("tei:email", NS))[:200]
    return info


def _normalize_orcid(raw: str) -> str:
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])", raw or "")
    return m.group(1).upper() if m else ""


def normalize_doi(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", s)
    return s.strip().rstrip(".")[:128]


def _reference(bibl, position: int) -> dict:
    analytic = bibl.find("tei:analytic", NS)
    monogr = bibl.find("tei:monogr", NS)
    title = ""
    if analytic is not None:
        title = _text(analytic.find("tei:title", NS))
    if not title and monogr is not None:
        title = _text(monogr.find("tei:title", NS))
    authors = []
    holder = analytic if analytic is not None and analytic.find("tei:author", NS) is not None else monogr
    if holder is not None:
        for a in holder.findall("tei:author", NS):
            name = _person(a.find("tei:persName", NS)).get("name")
            if name:
                authors.append(name)
    venue = ""
    year = None
    if monogr is not None:
        if analytic is not None:
            venue = _text(monogr.find("tei:title", NS))
        date = monogr.find("tei:imprint/tei:date", NS)
        when = (date.get("when") if date is not None else "") or _text(date)
        m = re.search(r"(19|20)\d{2}", when or "")
        year = int(m.group(0)) if m else None
    doi = ""
    arxiv = ""
    for idno in bibl.iter(f"{_T}idno"):
        kind = (idno.get("type") or "").lower()
        if kind == "doi":
            doi = normalize_doi(_text(idno))
        elif kind == "arxiv":
            arxiv = re.sub(r"^arxiv:\s*", "", _text(idno), flags=re.I)[:64]
    raw = ""
    for note in bibl.findall("tei:note", NS):
        if (note.get("type") or "") == "raw_reference":
            raw = _text(note)
    return {
        "position": position,
        "title": title[:500],
        "authors": authors[:20],
        "first_author": authors[0] if authors else "",
        "year": year,
        "venue": venue[:300],
        "doi": doi,
        "arxiv_id": arxiv,
        "raw": (raw or title)[:2000],
    }


def parse_tei(xml: str) -> dict:
    """TEI → {title, abstract, authors, sections, paragraphs, figures, references}。坏 XML 返回 {}。"""
    if not xml or "<TEI" not in xml:
        return {}
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError:
        return {}
    header = root.find("tei:teiHeader", NS)
    title = ""
    authors: list[dict] = []
    abstract = ""
    doi = ""
    if header is not None:
        title = _text(header.find("tei:fileDesc/tei:titleStmt/tei:title", NS))
        bibl = header.find("tei:fileDesc/tei:sourceDesc/tei:biblStruct", NS)
        if bibl is not None:
            for a in bibl.findall("tei:analytic/tei:author", NS):
                info = _author(a)
                if info:
                    authors.append(info)
            for idno in bibl.iter(f"{_T}idno"):
                if (idno.get("type") or "").lower() == "doi":
                    doi = normalize_doi(_text(idno))
        abs_el = header.find("tei:profileDesc/tei:abstract", NS)
        if abs_el is not None:
            abstract = " ".join(_text(p) for p in abs_el.iter(f"{_T}p") if _text(p)) or _text(abs_el)
    body = root.find("tei:text/tei:body", NS)
    sections: list[dict] = []
    paragraphs: list[dict] = []
    figures: list[dict] = []
    if body is not None:
        for div in body.iter(f"{_T}div"):
            head = div.find("tei:head", NS)
            heading = _text(head)
            label = (head.get("n") if head is not None else "") or ""
            if heading:
                sections.append(
                    {
                        "label": label,
                        "title": heading[:240],
                        "level": min(label.count(".") + 1, 4) if label else 1,
                        "page_no": _page_of(head),
                    }
                )
            for p in div.findall("tei:p", NS):
                text = _text(p)
                if len(text) < 20:
                    continue
                paragraphs.append({"section": heading[:300], "label": label, "text": text, "page_no": _page_of(p)})
        for fig in root.iter(f"{_T}figure"):
            desc = _text(fig.find("tei:figDesc", NS)) or _text(fig.find("tei:head", NS))
            if not desc:
                continue
            kind = "table" if (fig.get("type") or "").lower() == "table" else "figure"
            figures.append({"kind": kind, "caption": desc[:600], "page_no": _page_of(fig)})
    references: list[dict] = []
    back = root.find("tei:text/tei:back", NS)
    for bibl in back.iter(f"{_T}biblStruct") if back is not None else []:
        ref = _reference(bibl, len(references))
        if ref["title"] or ref["raw"]:
            references.append(ref)
    return {
        "title": title[:500],
        "doi": doi,
        "abstract": abstract[:6000],
        "authors": authors[:60],
        "sections": sections[:160],
        "paragraphs": paragraphs,
        "figures": figures[:120],
        "references": references[:400],
    }


def structure_from_tei(parsed: dict) -> dict:
    """给阅读器侧栏用的结构树，字段与原来一致，多带作者和参考文献数。"""
    return {
        "title": parsed.get("title") or "",
        "sections": list(parsed.get("sections") or [])[:120],
        "figures": [
            {"kind": f.get("kind"), "caption": (f.get("caption") or "")[:300], "page_no": f.get("page_no")}
            for f in (parsed.get("figures") or [])[:80]
        ],
        "authors": [
            {"name": a.get("name"), "affiliation": a.get("affiliation") or "", "orcid": a.get("orcid") or ""}
            for a in (parsed.get("authors") or [])[:40]
        ],
        "reference_count": len(parsed.get("references") or []),
        "source": "grobid",
    }


def _tokens(text: str) -> int:
    from fengsuan.app.services.kb import approx_tokens

    return approx_tokens(text)


def _split_long(text: str, limit: int = MAX_TOKENS) -> list[str]:
    if _tokens(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[\.\!\?。！？；;])\s+", text)
    out: list[str] = []
    buf = ""
    for part in parts:
        cand = f"{buf} {part}".strip() if buf else part
        if buf and _tokens(cand) > limit:
            out.append(buf)
            buf = part
        else:
            buf = cand
    if buf:
        out.append(buf)
    final: list[str] = []
    for piece in out:
        if _tokens(piece) <= limit * 1.5:
            final.append(piece)
            continue
        step = 1800
        final.extend(piece[i : i + step] for i in range(0, len(piece), step))
    return final


def _locate_page(text: str, pages: list[str]) -> int | None:
    probe = re.sub(r"\s+", "", (text or "")[:60]).lower()
    if len(probe) < 12:
        return None
    for idx, page in enumerate(pages or [], start=1):
        if probe in re.sub(r"\s+", "", page or "").lower():
            return idx
    return None


def chunk_structured(parsed: dict, pages: list[str] | None = None) -> list[dict] | None:
    """按章节段落切块：同一节的段落攒到 300–600 token 一块，不跨节；每块带页码。

    正文段落太少（GROBID 没切出来）返回 None，调用方退回按页切。
    """
    paras = list(parsed.get("paragraphs") or [])
    if len(paras) < 3:
        return None
    pages = pages or []
    chunks: list[dict] = []
    abstract = (parsed.get("abstract") or "").strip()
    if len(abstract) >= 40:
        for piece in _split_long(abstract):
            chunks.append(
                {"content": f"Abstract\n{piece}", "kind": "abstract", "section": "Abstract", "page_no": 1, "heading_path": "Abstract"}
            )

    def flush(section: str, buf: list[dict]) -> None:
        if not buf:
            return
        body = "\n".join(p["text"] for p in buf)
        page = next((p.get("page_no") for p in buf if p.get("page_no")), None) or _locate_page(body, pages)
        for piece in _split_long(body):
            head = section.strip()
            content = f"{head}\n{piece}" if head else piece
            chunks.append(
                {
                    "content": content,
                    "kind": "body",
                    "section": head[:300],
                    "page_no": page,
                    "heading_path": head[:500],
                }
            )

    cur_section = None
    buf: list[dict] = []
    size = 0
    for para in paras:
        section = para.get("section") or ""
        if cur_section is not None and section != cur_section:
            flush(cur_section, buf)
            buf, size = [], 0
        cur_section = section
        t = _tokens(para["text"])
        if buf and size + t > MAX_TOKENS and size >= MIN_TOKENS // 2:
            flush(section, buf)
            buf, size = [], 0
        buf.append(para)
        size += t
    flush(cur_section or "", buf)
    for fig in parsed.get("figures") or []:
        cap = (fig.get("caption") or "").strip()
        if len(cap) < 20:
            continue
        label = "Table" if fig.get("kind") == "table" else "Figure"
        chunks.append(
            {
                "content": f"{label}: {cap}",
                "kind": "table" if fig.get("kind") == "table" else "caption",
                "section": label,
                "page_no": fig.get("page_no") or _locate_page(cap, pages),
                "heading_path": label,
            }
        )
    body_n = sum(1 for c in chunks if c["kind"] == "body")
    return chunks if body_n >= 2 else None
