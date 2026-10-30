from __future__ import annotations

from fengsuan.app.services.literature import literature_open_href, stamp_reproduce_href


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_h530_literature_open_href_panel_and_session():
    assert literature_open_href("abc") == "/literature/abc"
    assert literature_open_href("abc", 3) == "/literature/abc?page=3"
    assert literature_open_href("abc", 3, panel="notes") == "/literature/abc?page=3&panel=notes"
    assert literature_open_href("abc", 4, note_id="n1", panel="notes") == "/literature/abc?page=4&note=n1&panel=notes"
    assert literature_open_href("abc", 2, session_id="s1", panel="tutor") == "/literature/abc?page=2&panel=tutor&session=s1"
    assert literature_open_href("abc", 1, manuscript_id="m1") == "/literature/abc?page=1&ms=m1"
    assert literature_open_href("abc", 1, panel="evil") == "/literature/abc?page=1"
    row = {"id": "lit1"}
    stamp_reproduce_href("lab9", [row])
    assert row["reproduce_href"] == "/labs/lab9/reproduces?literature_item_id=lit1"


def test_h531_write_section_stamps_href_and_reproduce(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h531w")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Section Href Paper", "abstract": "A method for graphs."},
        headers=h,
    ).json()
    prog = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 4, "page_count": 10},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Survey Href", "kind": "survey", "content_md": "引言\n\n结论"},
        headers=h,
    ).json()
    r = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={
            "heading": "相关工作",
            "notes": "对照",
            "literature_item_ids": [paper["id"]],
            "manuscript_id": ms["id"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    lit = next(x for x in (r.json().get("literature") or []) if x.get("id") == paper["id"])
    assert (lit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert "page=4" in (lit.get("href") or "")
    assert f"ms={ms['id']}" in (lit.get("href") or "")
    assert lit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    assert lit.get("last_page_no") == 4
    detail = client.get(
        f"/api/v1/literature/{paper['id']}/detail", params={"session": "s-keep", "include": "tutor"}, headers=h
    )
    assert detail.status_code == 200, detail.text
    assert "tutor_history" in detail.json()
    assert "notes" in detail.json()


def test_h532_citation_href_and_note_panel(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h532n")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Cite Panel Paper", "abstract": "We propose a convolution method on graphs."},
        headers=h,
    ).json()
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    section = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "convolution graphs", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert section.status_code == 200, section.text
    hits = [x for x in (section.json().get("citations") or []) if x.get("literature_item_id") == paper["id"]]
    assert hits
    href = hits[0].get("href") or ""
    assert href.startswith(f"/literature/{paper['id']}")
    assert "page=" in href
    assert hits[0].get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    polished = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "PANEL_NOTE", "literature_item_id": paper["id"], "write_note": True, "page_no": 5},
        headers=h,
    )
    assert polished.status_code == 200, polished.text
    nh = polished.json().get("href") or ""
    assert polished.json().get("wrote_note") is True
    assert "page=5" in nh
    assert "panel=notes" in nh
    assert "note=" in nh
