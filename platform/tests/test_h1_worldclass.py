from __future__ import annotations

from fengsuan.app.core.constants import PRODUCT_NAME
from fengsuan.app.services.capture import parse_paper_html

ARXIV_HTML = """
<html><head>
<meta name="citation_title" content="Attention Is All You Need" />
<meta name="citation_author" content="Ashish Vaswani" />
<meta name="citation_author" content="Noam Shazeer" />
<meta name="citation_doi" content="10.5555/3295222.3295349" />
<meta name="citation_publication_date" content="2017" />
<meta name="citation_abstract" content="We propose a new simple network architecture, the Transformer." />
</head></html>
"""

REVERSED_META = """
<html><head>
<meta content="Reversed Title Paper" name="citation_title" />
<meta content="Ada Lovelace" name="citation_author" />
<meta content="10.1000/xyz" name="citation_doi" />
</head></html>
"""


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_meta_product_name(client):
    r = client.get("/meta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"] == PRODUCT_NAME
    assert "OpenLens" in body["engine"]
    assert body["version"]


def test_change_password_and_wrong_current(client):
    token = _admin(client)
    h = _h(token)
    bad = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "nope1234", "new_password": "Newpass99"},
        headers=h,
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["error_class"] == "permission"
    weak = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "adminpass1", "new_password": "abcdefgh"},
        headers=h,
    )
    assert weak.status_code == 422, weak.text
    ok = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "adminpass1", "new_password": "Newpass99"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    old = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert old.status_code == 401
    nxt = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "Newpass99"})
    assert nxt.status_code == 200, nxt.text


def test_invite_emailed_false_without_smtp(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "EM", "code": "em1", "description": ""}, headers=h).json()["id"]
    r = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "mail@example.com", "role": "student"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["emailed"] is False
    assert "/invite/" in r.json()["invite_url"]


def test_parse_paper_html_arxiv_and_reversed():
    parsed = parse_paper_html(ARXIV_HTML, "https://arxiv.org/abs/1706.03762")
    assert parsed["title"] == "Attention Is All You Need"
    assert parsed["doi"] == "10.5555/3295222.3295349"
    assert "Ashish Vaswani" in parsed["authors"]
    assert parsed["publish_year"] == 2017
    assert parsed["site_adapter"] == "arxiv"
    rev = parse_paper_html(REVERSED_META, "https://example.com/p")
    assert rev["title"] == "Reversed Title Paper"
    assert rev["authors"] == ["Ada Lovelace"]
    assert rev["doi"] == "10.1000/xyz"


def test_import_capture_page_html_fills_title(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CAP", "code": "cap1", "description": ""}, headers=h).json()["id"]
    r = client.post(
        "/api/v1/literature/import-capture",
        json={
            "lab_id": lab,
            "page_url": "https://arxiv.org/abs/1706.03762",
            "page_html": ARXIV_HTML,
            "mark_read": True,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Attention Is All You Need"
    assert not body.get("degraded")
    empty = client.post(
        "/api/v1/literature/import-capture",
        json={"lab_id": lab, "page_url": "https://www.cnki.net/paper/xyz", "site_adapter": "cnki"},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    assert empty.json().get("degraded") is True


def test_language_empty_text_parse(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "LG", "code": "lg1", "description": ""}, headers=h).json()["id"]
    r = client.post("/api/v1/language/polish", json={"lab_id": lab, "text": "   "}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["error_class"] == "parse"


def test_kb_does_not_leak_across_labs(client):
    token = _admin(client)
    h = _h(token)
    lab_a = client.post("/api/v1/labs", json={"name": "KA", "code": "ka1", "description": ""}, headers=h).json()["id"]
    lab_b = client.post("/api/v1/labs", json={"name": "KB", "code": "kb1", "description": ""}, headers=h).json()["id"]
    a = client.post(
        f"/api/v1/labs/{lab_a}/literature",
        json={"title": "AlphaOnlyToken methods", "abstract": "AlphaOnlyToken appears only in lab A."},
        headers=h,
    ).json()["id"]
    b = client.post(
        f"/api/v1/labs/{lab_b}/literature",
        json={"title": "BetaOnlyToken methods", "abstract": "BetaOnlyToken appears only in lab B."},
        headers=h,
    ).json()["id"]
    assert client.post(f"/api/v1/literature/{a}/add-to-kb", headers=h).status_code == 200
    assert client.post(f"/api/v1/literature/{b}/add-to-kb", headers=h).status_code == 200
    qa = client.post(f"/api/v1/labs/{lab_a}/kb/query", json={"q": "BetaOnlyToken"}, headers=h)
    assert qa.status_code == 200, qa.text
    hits_a = qa.json()["hits"]
    assert not any("BetaOnlyToken" in (x.get("content") or "") for x in hits_a)
    qb = client.post(f"/api/v1/labs/{lab_b}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h)
    hits_b = qb.json()["hits"]
    assert not any("AlphaOnlyToken" in (x.get("content") or "") for x in hits_b)
    hit_a = client.post(f"/api/v1/labs/{lab_a}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h).json()["hits"]
    assert any(x.get("literature_item_id") == a for x in hit_a)


def test_cockpit_excerpt_and_home_unread_items(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CK", "code": "ck1", "description": ""}, headers=h).json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Draft One", "kind": "conference", "content_md": "First sentence of the paper body for excerpt."},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    cards = cockpit.json()["members"]
    assert any(
        any("First sentence" in (m.get("excerpt") or "") for m in (c.get("manuscripts") or [])) for c in cards
    )
    me = client.get("/api/v1/auth/me", headers=h).json()
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200, member.text
    assert any("First sentence" in (m.get("excerpt") or "") for m in member.json()["manuscripts"])
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert "unread_items" in home.json()
    assert "directions" in home.json()

    events = cockpit.json().get("recent_events") or []
    ms_id = ms.json()["id"]
    created = [e for e in events if e.get("verb") == "manuscript.created" and e.get("object_id") == ms_id]
    assert created, events
    assert created[0].get("object_type") == "manuscript"
    assert all(e.get("verb") != "presence.heartbeat" for e in events)

    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Month Read Paper", "abstract": "methods"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    marked = client.post(f"/api/v1/literature/{lit.json()['id']}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    patched = client.patch(f"/api/v1/manuscripts/{ms_id}", json={"status": "submitted"}, headers=h)
    assert patched.status_code == 200, patched.text

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    tm = stats.json().get("this_month") or {}
    assert tm.get("reading_started", 0) >= 1
    assert tm.get("submitted", 0) >= 1
    assert "kb_query" in tm

    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    body = csv.content.decode("utf-8", errors="ignore")
    assert "this_month" in body
    assert "reading_started" in body
    assert "submitted" in body


def test_task_notification_has_object_link(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "TK", "code": "tk1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "assignee@example.com", "role": "student"}, headers=h).json()
    client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv["token"],
            "email": "assignee@example.com",
            "password": "student99",
            "consent": True,
            "display_name": "Assignee",
        },
    )
    stu = client.post("/api/v1/auth/login", json={"email": "assignee@example.com", "password": "student99"}).json()["access"]
    sid = client.get("/api/v1/auth/me", headers=_h(stu)).json()["id"]
    task = client.post(
        f"/api/v1/labs/{lab}/tasks",
        json={"title": "Read transformer notes", "assignee_id": sid, "description": "week 1"},
        headers=h,
    )
    assert task.status_code == 200, task.text
    notes = client.get("/api/v1/notifications", headers=_h(stu))
    assert notes.status_code == 200, notes.text
    linked = [n for n in notes.json() if n.get("object_type") == "task" and n.get("object_id") == task.json()["id"]]
    assert linked, notes.text
    assert linked[0]["lab_id"] == lab


def test_me_includes_engine_credit(client):
    token = _admin(client)
    me = client.get("/api/v1/auth/me", headers=_h(token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["product"] == PRODUCT_NAME
    assert "OpenLens" in body["engine_credit"]
    assert body["version"]


def test_invite_preview_public(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "PV", "code": "pv1", "description": ""}, headers=h).json()
    inv = client.post(f"/api/v1/labs/{lab['id']}/invites", json={"email": "preview@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    raw = inv.json()["token"]
    r = client.get(f"/api/v1/auth/invites/{raw}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "preview@example.com"
    assert body["lab_name"] == "PV"
    assert body["usable"] is True
    assert body["consent_text"]
    missing = client.get("/api/v1/auth/invites/not-a-real-token")
    assert missing.status_code == 404


def test_cannot_remove_last_teacher(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "LT", "code": "lt1", "description": ""}, headers=h).json()["id"]
    me = client.get("/api/v1/auth/me", headers=h).json()
    r = client.delete(f"/api/v1/labs/{lab}/members/{me['id']}", headers=h)
    assert r.status_code == 400, r.text


def test_teacher_can_delete_peer_manuscript(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "MD", "code": "md1", "description": ""}, headers=h).json()["id"]
    inv_a = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "msa@example.com", "role": "student"}, headers=h).json()
    inv_b = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "msb@example.com", "role": "student"}, headers=h).json()
    client.post("/api/v1/auth/register", json={"invite_token": inv_a["token"], "email": "msa@example.com", "password": "student99", "consent": True, "display_name": "A"})
    client.post("/api/v1/auth/register", json={"invite_token": inv_b["token"], "email": "msb@example.com", "password": "student99", "consent": True, "display_name": "B"})
    ha = _h(client.post("/api/v1/auth/login", json={"email": "msa@example.com", "password": "student99"}).json()["access"])
    hb = _h(client.post("/api/v1/auth/login", json={"email": "msb@example.com", "password": "student99"}).json()["access"])
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "Peer Draft", "kind": "conference", "content_md": "body"}, headers=ha)
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    denied = client.delete(f"/api/v1/manuscripts/{mid}", headers=hb)
    assert denied.status_code == 403
    ok = client.delete(f"/api/v1/manuscripts/{mid}", headers=h)
    assert ok.status_code == 200, ok.text


def test_literature_ids_and_citation_delete(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "AR", "code": "ar1", "description": ""}, headers=h).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Transformer", "arxiv_id": "1706.03762", "pubmed_id": "123", "mark_read": True},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    assert lit.json()["arxiv_id"] == "1706.03762"
    assert lit.json()["pubmed_id"] == "123"
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "S", "kind": "survey"}, headers=h).json()
    cite = client.post(f"/api/v1/manuscripts/{ms['id']}/citations", json={"literature_item_id": lit.json()["id"], "citation_key": "vaswani2017"}, headers=h)
    assert cite.status_code == 200, cite.text
    gone = client.delete(f"/api/v1/manuscripts/{ms['id']}/citations/{cite.json()['id']}", headers=h)
    assert gone.status_code == 200, gone.text
    listed = client.get(f"/api/v1/manuscripts/{ms['id']}/citations", headers=h)
    assert listed.json() == []


def test_unread_count_and_glossary_delete(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "UC", "code": "uc1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "ucstu@example.com", "role": "student"}, headers=h).json()
    client.post("/api/v1/auth/register", json={"invite_token": inv["token"], "email": "ucstu@example.com", "password": "student99", "consent": True, "display_name": "U"})
    hs = _h(client.post("/api/v1/auth/login", json={"email": "ucstu@example.com", "password": "student99"}).json()["access"])
    sid = client.get("/api/v1/auth/me", headers=hs).json()["id"]
    client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "Ping", "assignee_id": sid}, headers=h)
    n = client.get("/api/v1/notifications/unread-count", headers=hs)
    assert n.status_code == 200, n.text
    assert n.json()["unread"] >= 1
    term = client.post(f"/api/v1/labs/{lab}/glossary", json={"src": "U-Net", "tgt": "", "do_not_translate": True}, headers=h)
    assert term.status_code == 200, term.text
    d = client.delete(f"/api/v1/glossary/{term.json()['id']}", headers=h)
    assert d.status_code == 200, d.text
    left = client.get(f"/api/v1/labs/{lab}/glossary", headers=h).json()
    assert not any(x["id"] == term.json()["id"] for x in left)


def test_guide_hrefs_and_student_omits_teacher_steps(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "GH", "code": "gh1", "description": ""}, headers=h).json()["id"]
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    guide = home.json()["guide"]
    assert guide
    for item in guide:
        assert item["href"].startswith(f"/labs/{lab}"), item
        assert item["path"]
    invite = next(x for x in guide if x["key"] == "invite")
    assert invite["href"].endswith("/members")
    assert invite["done"] is False
    cockpit = next(x for x in guide if x["key"] == "cockpit")
    assert cockpit["done"] is False
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "ghstu@example.com", "role": "student"}, headers=h).json()
    client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv["token"],
            "email": "ghstu@example.com",
            "password": "student99",
            "consent": True,
            "display_name": "G",
        },
    )
    hs = _h(client.post("/api/v1/auth/login", json={"email": "ghstu@example.com", "password": "student99"}).json()["access"])
    stu_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=hs)
    assert stu_home.status_code == 200, stu_home.text
    keys = {x["key"] for x in stu_home.json()["guide"]}
    assert "invite" not in keys
    assert "lab_key" not in keys
    assert {"import_lit", "mark_read", "manuscript", "cockpit"} <= keys
    teacher_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    assert next(x for x in teacher_home["guide"] if x["key"] == "cockpit")["done"] is True
    assert next(x for x in teacher_home["guide"] if x["key"] == "invite")["done"] is True

