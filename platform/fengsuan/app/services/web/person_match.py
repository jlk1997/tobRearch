"""认人：拼音、机构别名、名字边界。模型核对全部候选，规则只在名字完全没有出现时否决。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urlparse

from fengsuan.app.services.web.common import SourceFailures

_COMPOUND = ("欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "令狐", "慕容", "司徒", "夏侯", "宇文", "公孙", "长孙")
_PARTICLE = set("的等与和及")
_TITLE_AFTER = ("副教授", "教授", "导师", "老师", "博士", "主任", "院长", "院士", "研究员", "副研究员")


def _cjk_after_ok(text: str, end: int) -> bool:
    """名字后面可以是职称或标点。秦川导师算秦川，秦川平不算。"""
    rest = text[end:]
    if not rest:
        return True
    if rest[0] in _PARTICLE or rest[0] in "，。、；：·-—（(【[ ":
        return True
    for title in _TITLE_AFTER:
        if rest.startswith(title):
            return True
    return not ("\u4e00" <= rest[0] <= "\u9fff")


def name_in(name: str, text: str) -> bool:
    """中文名按边界：薛海不算薛海平。英文名忽略大小写和空格。"""
    name = (name or "").strip()
    text = text or ""
    if len(name) < 2 or not text:
        return False
    if re.search(r"[\u4e00-\u9fff]", name):
        for match in re.finditer(re.escape(name), text):
            before = text[match.start() - 1] if match.start() else ""
            if before and "\u4e00" <= before <= "\u9fff":
                continue
            if not _cjk_after_ok(text, match.end()):
                continue
            return True
        return False
    tokens = [tok.lower() for tok in re.findall(r"[A-Za-z]+", text)]
    want = [part.lower() for part in re.findall(r"[A-Za-z]+", name)]
    if not want:
        return False
    if len(want) == 1:
        return want[0] in tokens
    for i in range(0, len(tokens) - len(want) + 1):
        if tokens[i : i + len(want)] == want:
            return True
    return False


def _split_cjk(name: str) -> tuple[str, str]:
    for sur in _COMPOUND:
        if name.startswith(sur) and len(name) > len(sur):
            return sur, name[len(sur) :]
    return name[:1], name[1:]


def name_forms(name: str) -> dict:
    """中文名给出 Hai Xue / Xue Hai / H Xue。没有拼音库时只留中文名。"""
    name = (name or "").strip()
    out = {"cjk": "", "latin": [], "initial": ""}
    if not name:
        return out
    if not re.search(r"[\u4e00-\u9fff]", name):
        out["latin"] = [name]
        bits = name.split()
        if len(bits) >= 2 and bits[0] and bits[-1]:
            out["initial"] = f"{bits[0][0]} {bits[-1]}"
        return out
    out["cjk"] = name
    sur, given = _split_cjk(name)
    if not given:
        return out
    try:
        from pypinyin import lazy_pinyin
    except Exception:
        return out
    family = "".join(lazy_pinyin(sur)).title()
    given_py = "".join(lazy_pinyin(given)).title()
    if not family or not given_py:
        return out
    out["latin"] = [f"{given_py} {family}", f"{family} {given_py}"]
    out["initial"] = f"{given_py[0]} {family}"
    return out


def aliases_of(affiliation: str, institution: dict | None) -> list[str]:
    inst = institution or {}
    raw = [affiliation, inst.get("name") or "", inst.get("domain") or ""]
    raw.extend(inst.get("names") or [])
    out = []
    for bit in raw:
        text = str(bit or "").strip()
        if len(text) >= 2 and text not in out:
            out.append(text)
    return out


def affiliation_hit(text: str, aliases: list[str]) -> bool:
    blob = (text or "").lower()
    compact = re.sub(r"[\s.]", "", blob)
    for alias in aliases:
        token = (alias or "").strip().lower()
        if len(token) < 2:
            continue
        if token in blob or re.sub(r"[\s.]", "", token) in compact:
            return True
    return False


def _host(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def named_as(query: str, forms: dict, row: dict) -> bool:
    blob = " ".join(
        [
            str(row.get("name") or ""),
            " ".join(str(x) for x in (row.get("alternatives") or [])),
            " ".join(str(x) for x in (row.get("authors") or []) if x),
            str(row.get("title") or ""),
            str(row.get("excerpt") or ""),
        ]
    )
    if name_in(query, blob):
        return True
    for latin in forms.get("latin") or []:
        if name_in(latin, blob):
            return True
    initial = str(forms.get("initial") or "")
    if initial and initial.lower() in blob.lower():
        return True
    dotted = initial.replace(" ", ". ")
    return bool(dotted and dotted.lower() in blob.lower())


def judge_row(query: str, forms: dict, aliases: list[str], domain: str, row: dict) -> tuple[str, str]:
    """yes / no / unsure。单位、官网、已确认的同一作者才算 yes。"""
    if not named_as(query, forms, row):
        return "no", "名字对不上"
    url = str(row.get("url") or row.get("href") or "")
    if domain and domain in url:
        return "yes", "学校官网"
    aff_text = " ".join(str(x) for x in (row.get("affiliations") or []))
    aff_text += " " + str(row.get("excerpt") or "")
    if affiliation_hit(aff_text, aliases):
        return "yes", "单位对上"
    if row.get("openalex_id") and row.get("affiliation_ok"):
        return "yes", "同一个作者"
    return "unsure", "名字对上了，单位没写"


_ROSTER = ("名单", "公示", "招生", "考核", "拟录取")


def is_roster(text: str) -> bool:
    return any(tok in (text or "") for tok in _ROSTER)


def looks_like_profile(row: dict, name: str = "") -> bool:
    """学者主页、知网作者页不是论文。"""
    title = str((row or {}).get("title") or "")
    excerpt = str((row or {}).get("excerpt") or "")
    url = str((row or {}).get("url") or (row or {}).get("href") or "").lower()
    blob = f"{title} {excerpt}"
    if "cnki.net" in url and any(tok in blob for tok in ("H指数", "G指数", "被引次数", "学者")):
        return True
    if name and name in title and any(tok in title for tok in ("-", "－", "—", "｜", "|")) and len(title) <= len(name) + 24:
        return True
    if any(tok in title for tok in ("导师介绍", "个人主页", "教师主页", "师资", "在读博士")):
        return True
    return False


def page_facts(text: str, name: str) -> tuple[list[str], list[str]]:
    """只留下原文里真有的句子和书名号标题。"""
    raw = text or ""
    if not name_in(name, raw):
        return [], []
    facts = []
    for line in re.split(r"[\n。]", raw):
        line = line.strip()
        if len(line) < 6 or len(line) > 120:
            continue
        if not any(tok in line for tok in ("研究方向", "研究领域", "研究兴趣", "主持", "教授", "副教授", "课题")):
            continue
        if line not in raw and line not in raw.replace("\n", ""):
            continue
        facts.append(line)
        if len(facts) >= 4:
            break
    titles = []
    for title in re.findall(r"《([^》]{4,80})》", raw):
        if title in raw and title not in titles:
            titles.append(title)
        if len(titles) >= 2:
            break
    return facts, titles


def _web_row(hit: dict) -> dict:
    return {
        "name": "",
        "title": hit.get("title") or "",
        "excerpt": hit.get("snippet") or hit.get("abstract") or "",
        "url": hit.get("url") or "",
        "site": hit.get("site") or _host(hit.get("url") or ""),
        "source": "web",
        "authors": hit.get("authors") or [],
        "affiliations": [],
    }


def _paper_row(paper: dict) -> dict:
    return {
        "name": "",
        "title": paper.get("title") or "",
        "excerpt": paper.get("abstract") or paper.get("snippet") or "",
        "url": paper.get("url") or "",
        "year": paper.get("year"),
        "authors": paper.get("authors") or [],
        "affiliations": [],
        "source": "scholar",
        "citation_count": paper.get("citation_count") or 0,
    }


def accept_model_yes(row: dict, item: dict, name: str, forms: dict) -> bool:
    """模型说是，必须引用这条原文，并且原文里有这个人的名字。"""
    evidence = str((item or {}).get("evidence") or "").strip()
    if len(evidence) < 2:
        return False
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("title", "excerpt", "name")
    )
    if re.sub(r"\s+", "", evidence) not in re.sub(r"\s+", "", blob):
        return False
    if name_in(name, evidence):
        return True
    for latin in (forms or {}).get("latin") or []:
        if name_in(str(latin), evidence):
            return True
    return False


def _apply_llm(rows: list[dict], judged: dict, name: str, forms: dict) -> None:
    """名字对不上的不能改成是。说是的必须带原文。"""
    for row in rows:
        if row.get("_reason") == "名字对不上":
            continue
        key = str(row.get("_key") or "")
        got = judged.get(key) if isinstance(judged, dict) else None
        if not isinstance(got, dict):
            continue
        verdict = str(got.get("verdict") or "")
        if verdict == "yes" and accept_model_yes(row, got, name, forms):
            row["_verdict"] = "yes"
            row["_reason"] = str(got.get("reason") or "核对过原文")[:80]
        elif verdict == "no":
            row["_verdict"] = "no"
            row["_reason"] = str(got.get("reason") or "不是这位")[:80]


def _plan_queries(plan: dict | None, key: str, fallback: list[str]) -> list[str]:
    rows = []
    raw = (plan or {}).get(key) if isinstance(plan, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    for bit in raw or []:
        text = str(bit or "").strip()
        if text and text not in rows:
            rows.append(text[:180])
        if len(rows) >= 2:
            break
    return rows or [bit for bit in fallback if bit][:2]


def _miss_text(dropped: int, failed: dict) -> str:
    """没核上时要说清是「查了没有」还是「没查成」。"""
    base = f"这次没有核上同时对得上名字和单位的记录。排除了{dropped}条同名或名字相近的结果。"
    if failed:
        names = "、".join(list(failed)[:4])
        more = "等" if len(failed) > 4 else ""
        return f"{base}不过 {names}{more} 这次没连上，所以还不能断定公开库里没有。可以稍后重试，或补学院、英文名、主页链接。"
    return f"{base}可以补学院、英文名或主页链接再查。"


def resolve_person(
    name: str,
    affiliation: str = "",
    *,
    question: str = "",
    session: str = "",
    llm=None,
    db=None,
    user_id: str = "",
    lab_id: str = "",
    plan: dict | None = None,
    name_en: list | None = None,
    affiliation_en: str = "",
    allow_replan: bool = True,
) -> dict:
    """带单位认人。对不上就 miss，不把同名的人交回去选。"""
    from fengsuan.app.services.web import scholarly
    from fengsuan.app.services.web.scholar import scholar_search
    from fengsuan.app.services.web.search import search_web

    with SourceFailures.scoped():
        return _resolve_person(
            name,
            affiliation,
            question=question,
            session=session,
            llm=llm,
            db=db,
            user_id=user_id,
            lab_id=lab_id,
            plan=plan,
            name_en=name_en,
            affiliation_en=affiliation_en,
            allow_replan=allow_replan,
        )


def _resolve_person(
    name: str,
    affiliation: str = "",
    *,
    question: str = "",
    session: str = "",
    llm=None,
    db=None,
    user_id: str = "",
    lab_id: str = "",
    plan: dict | None = None,
    name_en: list | None = None,
    affiliation_en: str = "",
    allow_replan: bool = True,
) -> dict:
    from fengsuan.app.services.web import scholarly
    from fengsuan.app.services.web.scholar import scholar_search
    from fengsuan.app.services.web.search import search_web

    forms = name_forms(name)
    for extra in name_en or []:
        text = str(extra or "").strip()
        if text and text not in forms["latin"]:
            forms["latin"].insert(0, text)
    institution = {}
    try:
        institution = scholarly.openalex_institution(affiliation) if affiliation else {}
    except Exception as exc:
        # 机构没解出来会让两个采集器直接失效，必须写明，否则会误报「没找到」。
        holder = SourceFailures.current()
        if holder is not None and affiliation:
            holder.note("机构库", type(exc).__name__)
        institution = {}
    if not institution.get("id") and affiliation_en:
        try:
            institution = scholarly.openalex_institution(affiliation_en) or institution
        except Exception:
            pass
    aliases = aliases_of(affiliation, institution)
    domain = str(institution.get("domain") or "")
    scholar_q = ""
    if forms.get("latin") and institution.get("name"):
        scholar_q = f'author:"{forms["latin"][0]}" "{institution["name"]}"'[:180]
    elif forms.get("latin"):
        scholar_q = f'{forms["latin"][0]} {institution.get("name") or affiliation}'.strip()[:180]
    scholar_q_zh = f"{name} {affiliation}".strip()[:180]

    def _safe(label, fn):
        try:
            return fn() or []
        except Exception as exc:
            # D-HONEST：认人时某个源挂了要记下来，不能让它看起来像「没找到这位」。
            holder = SourceFailures.current()
            if holder is not None:
                holder.note(label, type(exc).__name__)
            return []

    def _mark_aff(rows_in, query):
        rows = []
        for hit in rows_in:
            row = _web_row(hit) if "title" in hit or "url" in hit else hit
            blob = f"{row.get('title')} {row.get('excerpt')}"
            if name_in(name, blob) and affiliation and (affiliation in blob or affiliation in query):
                if affiliation in blob:
                    row["affiliations"] = [affiliation]
            rows.append(row)
        return rows

    def _alex():
        rows = []
        if not institution.get("id"):
            return rows
        for form in _plan_queries(plan, "openalex", forms.get("latin") or []):
            rows.extend(scholarly.openalex_search_authors(form, institution_id=institution["id"]))
        return rows

    def _site():
        if not domain:
            return []
        queries = _plan_queries(plan, "site", [name])
        if name and all(name not in query for query in queries):
            queries = [name] + queries[:1]
        rows = []
        for query in queries[:2]:
            pack = search_web(query, site=domain, llm=llm)
            rows.extend(_web_row(hit) for hit in (pack.get("hits") or []))
        return rows

    def _cn():
        rows = []
        for query in _plan_queries(plan, "cnki", [f"{name} {affiliation}".strip()]):
            pack = search_web(query, site="cnki.net", llm=llm)
            rows.extend(_mark_aff(pack.get("hits") or [], query))
        return rows

    def _sch():
        papers = []
        for query in _plan_queries(plan, "scholar", [scholar_q, scholar_q_zh]):
            pack = scholar_search(query, session=session)
            papers.extend(pack.get("papers") or [])
        return [_paper_row(paper) for paper in papers]

    def _open():
        rows = []
        plain = f"{name} {affiliation}".strip()
        queries = _plan_queries(plan, "web", [plain, f"{plain} 博士".strip()])
        if name and all(name not in query for query in queries):
            queries = [plain] + queries[:1]
        for query in queries[:2]:
            pack = search_web(query, llm=llm)
            rows.extend(_mark_aff(pack.get("hits") or [], query))
        return rows

    def _cs():
        from fengsuan.app.services.sweep import discipline_of

        if discipline_of(question or "") != "cs":
            return []
        latin = (forms.get("latin") or [""])[0]
        if not latin:
            return []
        return (scholarly.dblp_authors(latin) or []) + (scholarly.s2_authors(latin) or [])

    pool = ThreadPoolExecutor(max_workers=6)
    jobs = (
        ("OpenAlex", _alex),
        ("学校官网", _site),
        ("知网", _cn),
        ("谷歌学术", _sch),
        ("Crossref", _cs),
        ("公开网页", _open),
    )
    futures = [pool.submit(_safe, label, fn) for label, fn in jobs]
    wait(futures, timeout=8)
    pool.shutdown(wait=False, cancel_futures=True)
    holder = SourceFailures.current()
    if holder is not None:
        # 超时没回来的采集器同样要如实说明，不能当作「查过了没有」。
        for (label, _fn), fut in zip(jobs, futures):
            if not fut.done():
                holder.note(label, "超时")
    rows: list[dict] = []
    for fut in futures:
        try:
            rows.extend(fut.result() or [])
        except Exception:
            continue
    for i, row in enumerate(rows):
        verdict, reason = judge_row(name, forms, aliases, domain, row)
        row["_verdict"] = verdict
        row["_reason"] = reason
        row["_key"] = str(row.get("openalex_id") or row.get("url") or row.get("title") or i)
        aff_text = " ".join(str(x) for x in (row.get("affiliations") or [])) + " " + str(row.get("excerpt") or "")
        row["_name_hit"] = named_as(name, forms, row)
        row["_affiliation_hit"] = affiliation_hit(aff_text, aliases)
        row["_official"] = bool(domain and domain in str(row.get("url") or ""))
    if db is not None and user_id and lab_id:
        try:
            from fengsuan.app.services.agent.parse import judge_candidates

            _apply_llm(
                rows,
                judge_candidates(
                    db,
                    user_id,
                    lab_id,
                    name,
                    affiliation,
                    rows,
                    forms=forms,
                    session_id=session,
                ),
                name,
                forms,
            )
        except Exception:
            pass
    yes = [row for row in rows if row.get("_verdict") == "yes"]
    people = []
    for row in yes:
        if row.get("openalex_id") or (row.get("source") not in {"web", "scholar"} and row.get("name")):
            people.append(row)
        elif domain and domain in str(row.get("url") or "") and name_in(name, f"{row.get('title')} {row.get('excerpt')}"):
            people.append(row)
    unique = []
    seen = set()
    for row in people:
        key = str(row.get("openalex_id") or row.get("url") or row.get("name") or row.get("title"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    def _profile_score(row: dict) -> int:
        title = str(row.get("title") or "")
        score = 0
        if name_in(name, title):
            score += 4
        if any(tok in title for tok in ("导师", "简介", "主页", "教师")):
            score += 2
        if any(tok in title for tok in ("多位", "榜单", "入选", "名单")):
            score -= 3
        if row.get("openalex_id"):
            score += 1
        return score

    unique.sort(key=_profile_score, reverse=True)

    def _page_ok(row: dict) -> bool:
        blob = f"{row.get('title') or ''} {row.get('excerpt') or ''}"
        if row.get("source") != "web" or not name_in(name, blob):
            return False
        url = str(row.get("url") or "")
        if domain and domain in url:
            return True
        return bool(affiliation and affiliation in blob)

    profiles = [row for row in yes if _page_ok(row)]

    def _web_score(row: dict) -> int:
        title = f"{row.get('title') or ''} {row.get('excerpt') or ''}"
        url = str(row.get("url") or "")
        score = _profile_score(row)
        if domain and domain in url:
            score += 5
        if any(tok in title for tok in ("博士", "教授", "导师", "研究方向")):
            score += 2
        if looks_like_profile(row, name) and "cnki.net" in url:
            score -= 2
        if is_roster(str(row.get("title") or "")):
            score -= 6
        return score

    profiles.sort(key=_web_score, reverse=True)
    if not unique and profiles:
        unique = [profiles[0]]
    phase = "认机构、查学校官网、核对同名"
    rejected = [row for row in rows if row.get("_verdict") != "yes"]
    sources = []
    for row in rejected[:12]:
        title = row.get("title") or row.get("name") or "来源"
        sources.append(
            {
                "id": f"drop:{row.get('_key')}",
                "kind": "web" if row.get("source") == "web" else "scholar",
                "title": title,
                "url": row.get("url") or "",
                "href": row.get("url") or "",
                "excerpt": str(row.get("excerpt") or "")[:240],
                "drop_reason": row.get("_reason") or "对不上",
                "external": True,
            }
        )
    if len(unique) >= 2 and all(row.get("openalex_id") or row.get("name") for row in unique[:4]):
        options = []
        for person in unique[:4]:
            label = " · ".join(
                bit
                for bit in (
                    person.get("name") or name,
                    (person.get("affiliations") or [affiliation])[0],
                    person.get("_reason") or "",
                )
                if bit
            )
            options.append(
                {
                    "label": label[:120],
                    "question": f"查 {person.get('name') or name}（{(person.get('affiliations') or [affiliation or '单位未写'])[0]}）的学术情况",
                    "author_id": person.get("openalex_id") or "",
                }
            )
        return {
            "ask_back": {"text": "点下面的一位。", "name": name, "options": options, "where": "web"},
            "name": name,
            "affiliation": affiliation,
            "institution": institution,
            "phase_label": phase,
            "sources": sources,
        }
    if not unique:
        if allow_replan and db is not None and user_id and lab_id:
            try:
                from fengsuan.app.services.agent.parse import replan_queries

                tried = []
                for key in ("openalex", "scholar", "site", "cnki", "web"):
                    tried.extend(_plan_queries(plan, key, []))
                tried.extend([q for q in (scholar_q, scholar_q_zh) if q])
                fresh = replan_queries(
                    db,
                    user_id,
                    lab_id,
                    name,
                    affiliation,
                    tried,
                    [str(row.get("title") or "")[:40] for row in rows[:6]],
                    name_en=forms.get("latin") or [],
                    session_id=session,
                )
            except Exception:
                fresh = []
            if fresh:
                return _resolve_person(
                    name,
                    affiliation,
                    question=question,
                    session=session,
                    llm=llm,
                    db=db,
                    user_id=user_id,
                    lab_id=lab_id,
                    plan={"web": fresh, "scholar": fresh, "cnki": fresh, "site": fresh},
                    name_en=name_en,
                    affiliation_en=affiliation_en,
                    allow_replan=False,
                )
        return {
            "miss": True,
            "name": name,
            "affiliation": affiliation,
            "institution": institution,
            "scholar_q": scholar_q,
            "dropped": len(rejected),
            "phase_label": phase,
            "sources": sources,
            "failed_sources": (SourceFailures.current().as_dict() if SourceFailures.current() else {}),
            "text": _miss_text(
                len(rejected),
                SourceFailures.current().as_dict() if SourceFailures.current() else {},
            ),
        }
    person = dict(unique[0])
    facts, titles = [], []
    page_rows = []
    if domain:
        for row in [person] + yes:
            url = str(row.get("url") or "")
            if domain in url and url not in {str(item.get("url") or "") for item in page_rows}:
                page_rows.append(row)
            if len(page_rows) >= 2:
                break
    for row in page_rows:
        try:
            from fengsuan.app.services.web.fetch import fetch_page

            page = fetch_page(str(row.get("url") or ""), strip=True)
            if not page.get("ok"):
                continue
            if db is not None and user_id and lab_id:
                from fengsuan.app.services.agent.parse import extract_profile

                got = extract_profile(db, user_id, lab_id, page.get("text") or "", name, session_id=session)
                facts.extend(got.get("facts") or [])
                titles.extend(got.get("titles") or [])
            else:
                fact_rows, title_rows = page_facts(page.get("text") or "", name)
                facts.extend(fact_rows)
                titles.extend(title_rows)
        except Exception:
            continue
    if titles and not person.get("openalex_id"):
        try:
            works = scholarly.openalex_search_works(titles[0], per_page=3)
        except Exception:
            works = []
        for work in works:
            authors = " ".join(str(a) for a in (work.get("authors") or []))
            if named_as(name, forms, {"authors": work.get("authors") or [], "title": work.get("title") or ""}) and affiliation_hit(
                authors + " " + str(work.get("abstract") or ""), aliases
            ):
                person["works"] = works[:5]
                break
    works = list(person.get("works") or [])
    works_total = int(person.get("works_count") or 0)
    if person.get("openalex_id"):
        try:
            pack = scholarly.openalex_author_works(
                str(person.get("openalex_id") or ""),
                limit=20,
                institution_id=str(institution.get("id") or ""),
            )
            works.extend(scholarly.work_list(pack))
            works_total = scholarly.work_total(pack) or works_total
        except Exception:
            pass
    if works_total:
        person["works_count"] = works_total
    for row in yes:
        if row.get("source") != "scholar" or not row.get("title") or looks_like_profile(row, name):
            continue
        if not named_as(name, forms, row):
            continue
        works.append(
            {
                "title": row.get("title") or "",
                "abstract": row.get("excerpt") or "",
                "url": row.get("url") or "",
                "year": row.get("year"),
                "authors": row.get("authors") or [],
                "source": "scholar",
                "matched_person": True,
            }
        )
    for work in works:
        work["matched_person"] = True
    if works:
        try:
            from openlens_ai.utils.paper_dedupe import dedupe_paper_dicts

            works = dedupe_paper_dicts(works)
        except Exception:
            pass
        person["works"] = works[:20]
    page_title = str(person.get("title") or "")
    if is_roster(page_title):
        person["bio"] = f"{name}的名字出现在学校这份名单里：{page_title[:48]}"
        facts = []
    elif not facts:
        snippet = str(person.get("excerpt") or "")
        if name_in(name, snippet) and not is_roster(snippet):
            facts = [snippet[:80]]
    if person.get("source") == "web" and person.get("excerpt") and not person.get("bio"):
        person["bio"] = str(person.get("excerpt"))[:120]
    if not person.get("name"):
        person["name"] = name
    aliases_name = [str(x) for x in (person.get("alternatives") or []) if x]
    for bit in [name] + list(forms.get("latin") or []):
        if bit and bit not in aliases_name:
            aliases_name.append(bit)
    person["alternatives"] = aliases_name[:6]
    if facts and not person.get("topics"):
        person["topics"] = [str(fact)[:40] for fact in facts[:3]]
    person["affiliations"] = person.get("affiliations") or ([affiliation] if affiliation else [])
    excerpt = "可能是这位。" + "，".join(person.get("affiliations") or [])
    if facts:
        excerpt += "。" + "。".join(facts[:3])
    source = {
        "id": f"person:{person.get('openalex_id') or person.get('url') or name}",
        "kind": "identity",
        "title": person.get("name") or name,
        "href": person.get("url") or "",
        "url": person.get("url") or "",
        "excerpt": excerpt[:500],
        "identity": {
            **{k: v for k, v in person.items() if k != "works" and not str(k).startswith("_")},
            "confirmed": False,
        },
        "external": True,
    }
    out_sources = [source]
    for work in (person.get("works") or [])[:12]:
        out_sources.append(
            {
                "id": f"work:{work.get('doi') or work.get('openalex_id') or work.get('title')}",
                "kind": "external",
                "title": work.get("title") or "",
                "url": work.get("url") or "",
                "href": work.get("url") or "",
                "year": work.get("year"),
                "authors": work.get("authors") or [],
                "doi": work.get("doi") or "",
                "arxiv_id": work.get("arxiv_id") or "",
                "openalex_id": work.get("openalex_id") or "",
                "pdf_url": work.get("pdf_url") or "",
                "cluster_id": work.get("cluster_id") or "",
                "cnki_id": work.get("cnki_id") or "",
                "citation_count": work.get("citation_count") or 0,
                "venue": work.get("venue") or "",
                "excerpt": str(work.get("abstract") or "")[:240],
                "matched_person": bool(work.get("matched_person")),
                "external": True,
            }
        )
    person_url = str(person.get("url") or "")
    if person_url.startswith("http"):
        out_sources.append(
            {
                "id": f"page:{person_url}",
                "kind": "web",
                "title": person.get("title") or person.get("name") or name,
                "url": person_url,
                "href": person_url,
                "site": person.get("site") or "",
                "excerpt": str(person.get("bio") or person.get("excerpt") or "")[:240],
                "external": True,
            }
        )
    for page in profiles[:4]:
        url = str(page.get("url") or "")
        if not url or url == person_url:
            continue
        out_sources.append(
            {
                "id": f"page:{url}",
                "kind": "web",
                "title": page.get("title") or url,
                "url": url,
                "href": url,
                "site": page.get("site") or "",
                "excerpt": str(page.get("excerpt") or "")[:240],
                "external": True,
            }
        )
    out_sources.extend(sources[:6])
    return {
        "picked": {k: v for k, v in person.items() if not str(k).startswith("_")},
        "name": name,
        "affiliation": affiliation,
        "institution": institution,
        "scholar_q": scholar_q,
        "phase_label": phase,
        "sources": out_sources,
        "text": f"可能是 {person.get('name') or name}。还没确认。只找这位老师本人的主页或简介。",
        "remember": {"person": {k: person.get(k) for k in ("name", "openalex_id", "orcid", "scholar_id", "dblp")}},
    }
