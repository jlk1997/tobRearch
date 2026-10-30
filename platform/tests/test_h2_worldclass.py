from __future__ import annotations

from openlens_ai.agents.paper_reproducer.pipeline import extract_method_card, run_until_terminal
from fengsuan.app.services.analysis import (
    _normalize_card,
    claim_mentioned_in_body,
    claim_rows,
    citation_key_in_body,
    excerpt_in_pages,
    first_card_page,
    ground_card,
    readable_method_card,
)
from fengsuan.app.services.literature import score_against_corpus, tokenize, literature_open_href


def test_score_against_corpus_prefers_lab_tokens():
    corpus = "graph neural network convolution 图学习"
    tokens = tokenize(corpus)
    s1, why = score_against_corpus("Graph convolution networks", "We propose convolution on graphs", corpus, tokens)
    s2, _ = score_against_corpus("Cooking recipes", "How to bake bread at home tonight", corpus, tokens)
    assert s1 > s2
    assert "graph" in why or "convolution" in why


def test_citation_key_in_body_patterns():
    assert citation_key_in_body("graphone", "我们引用 graphone。")
    assert citation_key_in_body("graphone", r"see \cite{graphone}")
    assert citation_key_in_body("graphone", r"see \citep{foo,graphone,bar}")
    assert citation_key_in_body("graphone", "[@graphone] says")
    assert not citation_key_in_body("graph", "graphone is related")
    assert citation_key_in_body("", "prefix Paper One Graph suffix", title="Paper One Graph")
    assert claim_mentioned_in_body(
        "Convolution on graphs improves node classification.",
        "Convolution on graphs improves node classification in this draft.",
    )
    assert not claim_mentioned_in_body("stub", "stub appears in the draft")


def test_claim_rows_keep_pages():
    card = {
        "claims": [{"text": "Convolution on graphs improves node classification.", "pages": [3, 4]}],
        "methods": {"text": "GCN", "pages": [2]},
    }
    rows = claim_rows(card, "Convolution on graphs improves node classification in this draft.")
    assert rows[0]["page_no"] == 3
    assert rows[0]["pages"] == [3, 4]
    assert rows[0]["mentioned_in_body"] is True
    assert first_card_page(card) == 3
    assert first_card_page({}) is None


def test_literature_open_href_page():
    assert literature_open_href("abc") == "/literature/abc"
    assert literature_open_href("abc", 3) == "/literature/abc?page=3"
    assert literature_open_href("abc", 0) == "/literature/abc"
    assert literature_open_href(None) == "/literature"
    assert literature_open_href("abc", 4, note_id="n1") == "/literature/abc?page=4&note=n1"
    assert literature_open_href("abc", None, note_id="n1") == "/literature/abc?note=n1"


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


def test_credential_kind_embed_rerank_masked(client):
    token = _admin(client)
    h = _h(token)
    put = client.put(
        "/api/v1/me/llm-credentials",
        json={
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-onprem-9999",
            "model_chat": "qwen2.5",
            "model_embed": "bge-m3",
            "model_rerank": "bge-reranker-v2-m3",
            "kind": "onprem",
        },
        headers=h,
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["kind"] == "onprem"
    assert body["model_embed"] == "bge-m3"
    assert body["model_rerank"] == "bge-reranker-v2-m3"
    assert body["api_key_masked"].endswith("9999")
    assert "sk-onprem-9999" not in put.text
    got = client.get("/api/v1/me/llm-credentials", headers=h)
    assert got.status_code == 200
    assert got.json()["kind"] == "onprem"
    bad = client.put(
        "/api/v1/me/llm-credentials",
        json={"provider": "openai", "api_key": "sk-x", "kind": "cluster"},
        headers=h,
    )
    assert bad.status_code == 422
    plat = client.put(
        "/api/v1/admin/llm-credentials",
        json={"provider": "vllm", "base_url": "http://127.0.0.1:8000/v1", "api_key": "sk-plat-4321", "kind": "onprem", "model_embed": "emb"},
        headers=h,
    )
    assert plat.status_code == 200, plat.text
    assert plat.json()["kind"] == "onprem"
    assert plat.json()["model_embed"] == "emb"
    assert plat.json()["api_key_masked"].endswith("4321")


def test_kb_embed_fallback_hash_in_stub(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbemb")
    empty = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "anything"}, headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json().get("empty_reason") == "no_indexed"
    assert empty.json().get("embed_fallback") == "hash"
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("a.txt", b"AlphaOnlyToken lives in this chunk for retrieval.", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h)
    assert q.status_code == 200, q.text
    assert q.json().get("embed_fallback") == "hash"
    hits = q.json().get("hits") or []
    assert hits
    for hit in hits:
        assert hit["citation"] in hit["content"]


def test_excerpt_grounding_drops_ungrounded():
    pages = ["This is the real page text about transformers and attention."]
    assert excerpt_in_pages("real page text about transformers", pages, [1]) is True
    assert excerpt_in_pages("hallucinated excerpt that is not here", pages, [1]) is False
    card = ground_card(
        {
            "methods": {"text": "hallucinated excerpt that is not here", "pages": [9]},
            "claims": [{"text": "This is the real page text about transformers and attention.", "pages": [1]}],
        },
        pages,
    )
    assert card["methods"]["ungrounded"] is True
    assert card["methods"]["pages"] == []
    assert card["methods"]["text"] == ""
    assert "hallucinated excerpt" in (card["methods"].get("said") or "")
    assert not card["claims"][0].get("ungrounded")
    assert card["claims"][0]["pages"] == [1]


def test_readable_method_card_lifts_claim_without_json_dump():
    raw = _normalize_card(
        {
            "problem": {"statement": "unused"},
            "claims": [
                {
                    "claim": "Small objects are sparsely distributed.",
                    "support": "Each image occupies 8.1% of pixels.",
                    "pages": [1],
                }
            ],
            "methods": {"text": ""},
        },
        source="pdf",
    )
    assert raw["claims"][0]["text"] == "Small objects are sparsely distributed."
    shown = readable_method_card(
        {
            "problem": {"text": "", "pages": [], "ungrounded": True},
            "claims": [
                {
                    "claim": "Small objects are sparsely distributed.",
                    "support": "Each image occupies 8.1% of pixels.",
                }
            ],
        }
    )
    assert shown["problem"]["text"] == ""
    body = shown["claims"][0]["text"]
    assert "Small objects" in body
    assert "{" not in body
    assert shown["claims"][0].get("ungrounded") is True


def test_analyze_abstract_only_and_tutor(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "tut1")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Paper", "abstract": "We propose a convolution method on graphs.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    iid = lit.json()["id"]
    ana = client.post(f"/api/v1/literature/{iid}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert ana.status_code == 200, ana.text
    card = ana.json()["result"]
    assert card.get("source") == "abstract_only"
    assert card.get("warning")
    tut = client.post(
        f"/api/v1/literature/{iid}/tutor",
        json={"question": "这一页方法在做什么？", "page_no": 1, "write_note": True},
        headers=h,
    )
    assert tut.status_code == 200, tut.text
    assert tut.json().get("answer")
    assert tut.json().get("note_id")
    detail = client.get(f"/api/v1/literature/{iid}/detail", params={"include": "tutor"}, headers=h)
    assert detail.status_code == 200, detail.text
    types = {a.get("analysis_type") for a in detail.json().get("analyses") or []}
    assert "method_card" in types
    assert "tutor" in types
    missing = client.post("/api/v1/literature/not-a-real-item/tutor", json={"question": "x"}, headers=h)
    assert missing.status_code == 404
    assert "文献不存在" in missing.text


def test_tutor_cross_lab_forbidden(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "tuta")
    lab_b = _lab(client, h, "tutb")
    lit = client.post(
        f"/api/v1/labs/{lab_a}/literature",
        json={"title": "Secret", "abstract": "hidden abstract text here"},
        headers=h,
    ).json()
    inv = client.post(f"/api/v1/labs/{lab_b}/invites", json={"email": "tutb@example.com", "role": "student"}, headers=h).json()
    stu = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv["token"],
            "email": "tutb@example.com",
            "password": "student99",
            "consent": True,
            "display_name": "B",
        },
    )
    assert stu.status_code == 200, stu.text
    denied = client.post(
        f"/api/v1/literature/{lit['id']}/tutor",
        json={"question": "leak?"},
        headers=_h(stu.json()["access"]),
    )
    assert denied.status_code == 403


def test_reproduce_heuristic_off_success_path():
    leftover = extract_method_card("We train with CUDA GPU kernels.", title="toy")
    assert leftover.get("needs_gpu") is True
    state = run_until_terminal(
        {
            "current_stage": "ingest",
            "title": "toy",
            "pdf_text": "We train with CUDA GPU kernels.",
            "dataset_path": ".",
            "gpu_mode": "off",
            "claimed_metrics": {"acc": 0.99},
            "method_card_json": {},
        }
    )
    assert state["status"] == "failed"
    assert state["error_class"] == "parse"
    assert state.get("actual_metrics_json") in (None, {}, {})
    gpu = run_until_terminal(
        {
            "current_stage": "ingest",
            "title": "toy-gpu",
            "dataset_path": ".",
            "gpu_mode": "off",
            "claimed_metrics": {"acc": 0.99},
            "method_card_json": {"title": "toy-gpu", "problem": "train", "needs_gpu": True},
        }
    )
    assert gpu["status"] == "awaiting_gpu"
    assert gpu.get("actual_metrics_json") in (None, {}, {})
    assert "不伪造" in (gpu.get("gap_report_md") or "")


def test_direction_overview_and_guide(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dirhub")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉", "description": "cv", "keywords": ["vision"]},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Vision Paper", "abstract": "cnn", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    body = ov.json()
    assert body["name"] == "视觉"
    assert any(p["id"] == lit.json()["id"] for p in body.get("literature") or [])
    assert f"direction_id={did}" in (body.get("literature_href") or "")
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"direction_id": did}, headers=h)
    assert listed.status_code == 200
    items = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or listed.json()
    if isinstance(items, dict):
        items = items.get("items") or []
    assert any((x.get("id") == lit.json()["id"]) for x in items)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200
    keys = {x["key"] for x in home.json()["guide"]}
    assert "pick_direction" in keys
    assert "open_direction_papers" in keys


def test_cockpit_metrics_and_progress(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "cmet")
    me = client.get("/api/v1/auth/me", headers=h).json()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    members = cockpit.json()["members"]
    assert members
    card = members[0]
    for key in (
        "reading_count",
        "want_read_count",
        "avg_progress_pct",
        "method_card_count",
        "manuscript_dwell_days",
        "awaiting_gpu_count",
        "blocked_count",
    ):
        assert key in card
    prog = client.get(f"/api/v1/labs/{lab}/progress", params={"user_id": me["id"]}, headers=h)
    assert prog.status_code == 200, prog.text
    assert "items" in prog.json()
    assert "total" in prog.json()
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200
    for row in st.json().get("by_direction") or []:
        assert "method_card_count" in row


def test_write_section_returns_literature(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "wlit")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Cited Work", "abstract": "A method for graphs."},
        headers=h,
    ).json()
    r = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "对照", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    lit = r.json().get("literature") or []
    assert any(x.get("id") == paper["id"] for x in lit)
    for hit in r.json().get("citations") or []:
        assert hit["citation"] in hit["content"]


def test_cited_by_published_and_add_to_library(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "arch1")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Cited By Pub", "abstract": "graph method body"},
        headers=h,
    ).json()
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Uncited Paper", "abstract": "other"},
        headers=h,
    ).json()
    draft = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Draft MS", "kind": "conference", "content_md": "引言 结论"},
        headers=h,
    ).json()
    blocked = client.post(f"/api/v1/manuscripts/{draft['id']}/add-to-library", headers=h)
    assert blocked.status_code == 400
    pub = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Published Survey", "kind": "survey", "content_md": "引言\n\n我们总结图方法。\n\n结论"},
        headers=h,
    ).json()
    client.patch(f"/api/v1/manuscripts/{pub['id']}", json={"status": "published"}, headers=h)
    cite = client.post(
        f"/api/v1/manuscripts/{pub['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "cited"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"cited_by_published": True}, headers=h)
    assert listed.status_code == 200, listed.text
    ids = {x["id"] for x in listed.json()} if isinstance(listed.json(), list) else {x["id"] for x in listed.json().get("items") or []}
    assert paper["id"] in ids
    assert other["id"] not in ids
    added = client.post(f"/api/v1/manuscripts/{pub['id']}/add-to-library", params={"add_to_kb": True}, headers=h)
    assert added.status_code == 200, added.text
    assert added.json().get("literature_item_id")
    assert added.json().get("added_to_kb") is True
    lib_id = added.json()["literature_item_id"]
    got = client.get(f"/api/v1/literature/{lib_id}", headers=h)
    assert got.status_code == 200
    assert "Published Survey" in got.json()["title"]
    again = client.post(f"/api/v1/manuscripts/{pub['id']}/add-to-library", params={"add_to_kb": True}, headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["literature_item_id"] == lib_id
    assert again.json().get("already_in_library") is True


def test_manuscripts_direction_filter_and_overview_hrefs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "msdir")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "NLP", "keywords": ["nlp"]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "NLP Draft", "kind": "conference", "content_md": "x", "direction_ids": [d["id"]]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"direction_id": d["id"]}, headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == ms.json()["id"] for x in listed.json())
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert ov.status_code == 200
    assert f"direction_id={d['id']}" in (ov.json().get("literature_href") or "")
    assert f"direction_id={d['id']}" in (ov.json().get("manuscripts_href") or "")
    assert f"direction_id={d['id']}" in (ov.json().get("graph_href") or "")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Dir Unread Paper", "abstract": "nlp paper", "direction_ids": [d["id"]]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": d["id"]}, headers=h)
    assert rec.status_code == 200, rec.text
    unread_ids = {x["id"] for x in rec.json().get("lab_unread") or []}
    assert paper.json()["id"] in unread_ids
    assert all(x.get("reason") == "本方向未读" for x in rec.json().get("lab_unread") or [])
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": d["id"]}, headers=h)
    assert g.status_code == 200, g.text
    gids = {n["id"] for n in g.json().get("nodes") or []}
    assert paper.json()["id"] in gids
    assert ms.json()["id"] in gids
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d["id"]}, headers=h)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    hrefs = {x["key"]: x.get("href") for x in home.json()["guide"]}
    assert d["id"] in (hrefs.get("open_direction_papers") or "")


def test_progress_pagination_and_platform_ping(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "prog2")
    me = client.get("/api/v1/auth/me", headers=h).json()
    page1 = client.get(f"/api/v1/labs/{lab}/progress", params={"user_id": me["id"], "page": 1, "page_size": 2}, headers=h)
    assert page1.status_code == 200
    assert "total" in page1.json()
    ping = client.post("/api/v1/admin/llm-credentials/test", headers=h)
    assert ping.status_code in {200, 400}
    client.put(
        "/api/v1/admin/llm-credentials",
        json={"provider": "openai", "api_key": "sk-plat-test", "kind": "cloud"},
        headers=h,
    )
    ping2 = client.post("/api/v1/admin/llm-credentials/test", headers=h)
    assert ping2.status_code == 200, ping2.text
    assert ping2.json()["ok"] is True
    assert ping2.json()["mode"] == "stub"
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200
    assert "lab_unread" in rec.json()


def test_h4_tutor_session_and_claims_and_compare_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h4a")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图学习", "keywords": ["graph"], "description": "初稿"},
        headers=h,
    ).json()
    p1 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper One Graph", "abstract": "We propose a convolution method on graphs.", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    p2 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper Two Graph", "abstract": "We propose a convolution method on graphs."},
        headers=h,
    ).json()
    ana = client.post(f"/api/v1/literature/{p1['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert ana.status_code == 200, ana.text
    t1 = client.post(f"/api/v1/literature/{p1['id']}/tutor", json={"question": "方法是什么？", "page_no": 1}, headers=h)
    assert t1.status_code == 200, t1.text
    sid = t1.json().get("session_id")
    assert sid
    t2 = client.post(
        f"/api/v1/literature/{p1['id']}/tutor",
        json={"question": "和上一问有何关系？", "page_no": 1, "session_id": sid},
        headers=h,
    )
    assert t2.status_code == 200, t2.text
    assert t2.json().get("session_id") == sid
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Survey", "kind": "survey", "content_md": "引言\n\n我们引用 graphone。\n\n结论"},
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": p1["id"], "citation_key": "graphone"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    claims = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    row = next(x for x in claims.json()["items"] if x["literature_item_id"] == p1["id"])
    assert row["cited_in_body"] is True
    assert row["has_method_card"] is True
    assert claims.json().get("citation_count") == 1
    assert claims.json().get("cited_in_body_count") == 1
    assert isinstance(claims.json().get("unmentioned_claims"), list)
    cmp = client.post(
        "/api/v1/literature/compare",
        json={"lab_id": lab, "item_ids": [p1["id"], p2["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    assert cmp.json().get("gap_md")
    ap = client.patch(
        f"/api/v1/directions/{d['id']}",
        json={"append_note": "## 文献对比与差距\n\n" + cmp.json()["gap_md"]},
        headers=h,
    )
    assert ap.status_code == 200, ap.text
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert "文献对比" in (ov.json().get("description") or "")
    assert ov.json().get("method_card_count") >= 1
    ap2 = client.patch(
        f"/api/v1/directions/{d['id']}",
        json={"append_note": "## 文献对比与差距\n\n" + cmp.json()["gap_md"]},
        headers=h,
    )
    assert ap2.status_code == 200, ap2.text
    ov2 = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert (ov2.json().get("description") or "").count("文献对比与差距") == 1
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": d["id"]}, headers=h)
    assert rec.status_code == 200, rec.text
    unread = next(x for x in (rec.json().get("lab_unread") or []) if x["id"] == p1["id"])
    assert unread.get("reason") == "本方向未读"
    assert unread.get("has_method_card") is True
    assert rec.json().get("rerank") == "lab_corpus"
    assert "arxiv" in (rec.json().get("sources_used") or [])
    assert "pubmed" in (rec.json().get("sources_used") or [])
    related_ids = {x["id"] for x in rec.json().get("lab_related") or []}
    assert p2["id"] in related_ids
    assert p1["id"] not in related_ids
    sec = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "literature_item_ids": [p2["id"]], "manuscript_id": ms["id"]},
        headers=h,
    )
    assert sec.status_code == 200, sec.text
    assert sec.json().get("citations_added") >= 1
    listed = client.get(f"/api/v1/manuscripts/{ms['id']}/citations", headers=h)
    assert any(x["literature_item_id"] == p2["id"] for x in listed.json())
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d["id"]}, headers=h)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    guide = {x["key"]: x for x in home["guide"]}
    assert guide["open_direction_papers"]["done"] is False
    client.post(f"/api/v1/literature/{p1['id']}/mark-read", json={"status": "reading"}, headers=h)
    home2 = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    guide2 = {x["key"]: x for x in home2["guide"]}
    assert guide2["open_direction_papers"]["done"] is True
    rec2 = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": d["id"]}, headers=h)
    assert p1["id"] not in {x["id"] for x in (rec2.json().get("lab_unread") or [])}
    kb = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert kb.status_code == 200, kb.text
    assert "embed_fallback" in kb.json()
    repros = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"status": "pending"}, headers=h)
    assert repros.status_code == 200
    assert isinstance(repros.json(), list)
    by_ds = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"dataset_id": "missing-dataset"}, headers=h)
    assert by_ds.status_code == 200
    assert by_ds.json() == []


def test_review_simulate_replaces_prior_and_flags_unused_key(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5rev")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Cited Ghost", "abstract": "graph paper"},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "Clean Structure",
            "kind": "conference",
            "content_md": "引言\n\n方法\n\n实验\n\n结论\n",
        },
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "ghostkey"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    first = client.post(f"/api/v1/manuscripts/{ms['id']}/review-simulate", headers=h)
    assert first.status_code == 200, first.text
    c1 = client.get(f"/api/v1/manuscripts/{ms['id']}/comments", headers=h)
    assert c1.status_code == 200
    n1 = len(c1.json())
    assert n1 >= 1
    blob = " ".join(x.get("body") or "" for x in c1.json())
    assert "ghostkey" in blob
    second = client.post(f"/api/v1/manuscripts/{ms['id']}/review-simulate", headers=h)
    assert second.status_code == 200, second.text
    c2 = client.get(f"/api/v1/manuscripts/{ms['id']}/comments", headers=h)
    assert len(c2.json()) == n1
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["content_md"]
    assert "引言" in body and "ghostkey" not in body


def test_h51_dedup_capture_direction_alias(client):
    from fengsuan.app.services.capture import parse_paper_html

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h51a")
    cnki = parse_paper_html(
        "<html><head><title>图神经网络综述 - 中国知网</title></head></html>",
        "https://kns.cnki.net/kcms/detail/detail.aspx?filename=ABC123",
    )
    assert cnki["site_adapter"] == "cnki"
    assert cnki["title"] == "图神经网络综述"
    assert cnki["cnki_id"] == "ABC123"
    ieee = parse_paper_html(
        '<html><head><meta name="citation_title" content="GCN Paper" />'
        '<meta name="citation_doi" content="10.1109/TEST.2020.1" /></head></html>',
        "https://ieeexplore.ieee.org/document/1234567",
    )
    assert ieee["site_adapter"] == "ieee"
    assert ieee["doi"] == "10.1109/TEST.2020.1"
    first = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Arxiv Twin", "arxiv_id": "1706.03762", "doi": "10.5555/ABC"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    again = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Different Title", "arxiv_id": "1706.03762v7", "doi": "10.5555/abc"},
        headers=h,
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json().get("already_in_library") is True
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "GNN", "keywords": ["gnn"]},
        headers=h,
    ).json()
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}", headers=h)
    assert ov.status_code == 200, ov.text
    assert ov.json()["id"] == d["id"]
    assert "literature_href" in ov.json()
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == d["id"] for x in listed.json())
    cap = client.post(
        "/api/v1/literature/import-capture",
        json={
            "lab_id": lab,
            "page_url": "https://arxiv.org/abs/1706.03762",
            "page_html": (
                "<html><head>"
                '<meta name="citation_title" content="Arxiv Twin" />'
                '<meta name="citation_doi" content="10.5555/ABC" />'
                "</head></html>"
            ),
            "mark_read": True,
        },
        headers=h,
    )
    assert cap.status_code == 200, cap.text
    assert cap.json()["id"] == first.json()["id"]
    assert cap.json().get("already_in_library") is True
    doi_url = parse_paper_html(
        '<html><head><meta name="dc.identifier" content="https://doi.org/10.5555/xyz" /></head></html>',
        "https://example.com/p",
    )
    assert doi_url["doi"] == "10.5555/xyz"


def test_h52_reading_口径_digest_recommend(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h52a")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "GNN", "keywords": ["graph"]},
        headers=h,
    ).json()
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d["id"]}, headers=h)
    want = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Want Read Graph", "abstract": "graph", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    reading = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Now Reading Graph", "abstract": "graph conv", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    client.post(f"/api/v1/literature/{want['id']}/mark-read", json={"status": "want_read"}, headers=h)
    client.post(f"/api/v1/literature/{reading['id']}/mark-read", json={"status": "reading"}, headers=h)
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    body = ov.json()
    assert "tab=recommend" in (body.get("recommend_href") or "")
    assert f"direction_id={d['id']}" in body["recommend_href"]
    by_id = {p["id"]: p for p in body.get("literature") or []}
    assert by_id[want["id"]]["reading_count"] == 0
    assert by_id[reading["id"]]["reading_count"] == 1
    assert body.get("active_reading_count") == 1
    assert body.get("want_read_count") == 1
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200
    row = next(x for x in st.json().get("by_direction") or [] if x["direction_id"] == d["id"])
    assert row["reading_count"] == 1
    assert row.get("want_read_count") == 1
    me = client.get("/api/v1/auth/me", headers=h).json()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(x for x in cockpit["members"] if x["user_id"] == me["id"])
    assert card["reading_count"] == 1
    assert card.get("want_read_count") == 1
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    rec = digest.json().get("recommend_href") or ""
    assert "tab=recommend" in rec
    assert d["id"] in rec
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200
    assert "want_read_count" in csv.text
    rec_api = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": d["id"]}, headers=h)
    assert rec_api.status_code == 200, rec_api.text
    assert rec_api.json().get("llm_rerank") is False
    assert rec_api.json().get("rerank") == "lab_corpus"


def test_h53_tutor_session_page_and_reading_lists(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h53a")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Tutor Paged Paper", "abstract": "graph convolution pages"},
        headers=h,
    ).json()
    first = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "第一问会话甲", "page_no": 1},
        headers=h,
    )
    assert first.status_code == 200, first.text
    sid_a = first.json()["session_id"]
    client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "第二问会话甲", "page_no": 2, "session_id": sid_a},
        headers=h,
    )
    other = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "新会话乙", "page_no": 1, "session_id": "sess-b"},
        headers=h,
    )
    assert other.json().get("session_id") == "sess-b"
    for i in range(8):
        r = client.post(
            f"/api/v1/literature/{paper['id']}/tutor",
            json={"question": f"甲追问{i}", "page_no": 1, "session_id": sid_a},
            headers=h,
        )
        assert r.status_code == 200, r.text
    page1 = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"session": sid_a, "tutor_page": 1, "tutor_page_size": 4, "include": "tutor"},
        headers=h,
    )
    assert page1.status_code == 200, page1.text
    hist = page1.json().get("tutor_history") or {}
    assert hist.get("total") >= 10
    assert len(hist.get("items") or []) == 4
    assert hist.get("has_more") is True
    assert all((x.get("session_id") == sid_a) for x in hist["items"])
    qs = " ".join(x.get("question") or "" for x in hist["items"])
    assert "新会话乙" not in qs
    page2 = client.get(
        f"/api/v1/literature/{paper['id']}/detail",
        params={"session": sid_a, "tutor_page": 2, "tutor_page_size": 4, "include": "tutor"},
        headers=h,
    )
    ids1 = {x["id"] for x in hist["items"]}
    ids2 = {x["id"] for x in (page2.json().get("tutor_history") or {}).get("items") or []}
    assert ids1 and ids2
    assert ids1.isdisjoint(ids2)
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "h53s@example.com", "role": "student"}, headers=h).json()
    stu = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "h53s@example.com", "password": "student99", "consent": True, "display_name": "S"},
    )
    assert stu.status_code == 200, stu.text
    sh = _h(stu.json()["access"])
    as_stu = client.get(f"/api/v1/literature/{paper['id']}/detail", params={"include": "tutor"}, headers=sh)
    assert as_stu.status_code == 200
    assert (as_stu.json().get("tutor_history") or {}).get("total") == 0
    types = {a.get("analysis_type") for a in as_stu.json().get("analyses") or []}
    assert "tutor" not in types
    want = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Want Home"}, headers=h).json()
    now = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Reading Home"}, headers=h).json()
    client.post(f"/api/v1/literature/{want['id']}/mark-read", json={"status": "want_read"}, headers=h)
    client.post(f"/api/v1/literature/{now['id']}/mark-read", json={"status": "reading"}, headers=h)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    titles = [x["title"] for x in home.json().get("readings") or []]
    assert "Reading Home" in titles
    assert titles.index("Reading Home") < titles.index("Want Home")
    me = client.get("/api/v1/auth/me", headers=h).json()
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200, member.text
    body = member.json()
    assert any(x["title"] == "Reading Home" for x in body.get("reading_now") or [])
    assert all(x.get("status") == "reading" for x in body.get("reading_now") or [])
    assert any(x["title"] == "Want Home" for x in body.get("want_reads") or [])
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "page_size": 20}, headers=h)
    hit = next(x for x in listed.json()["items"] if x["id"] == now["id"])
    assert hit.get("last_page_no") is None or hit.get("my_reading") == "reading"


def test_h54_claims_page_and_writing_cite_progress(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h54a")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Claim Page Paper", "abstract": "We propose a convolution method on graphs."},
        headers=h,
    ).json()
    ana = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert ana.status_code == 200, ana.text
    prog = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 2, "page_count": 10},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    assert prog.json().get("progress_pct") == 20
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Survey", "kind": "survey", "content_md": "引言\n\n我们引用 claimpg。\n\n结论"},
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "claimpg"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    claims = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    row = next(x for x in claims.json()["items"] if x["literature_item_id"] == paper["id"])
    assert row["cited_in_body"] is True
    assert row["has_method_card"] is True
    assert row.get("source_page") == 1
    assert row.get("progress_pct") == 20
    assert row.get("last_page_no") == 2
    assert row.get("my_reading") == "reading"
    claim_row = (row.get("claim_rows") or [None])[0]
    assert claim_row
    assert claim_row.get("page_no") == 1
    assert 1 in (claim_row.get("pages") or [])
    listed = client.get(f"/api/v1/manuscripts/{ms['id']}/citations", headers=h)
    assert listed.status_code == 200, listed.text
    cref = next(x for x in listed.json() if x["literature_item_id"] == paper["id"])
    assert cref.get("progress_pct") == 20
    assert cref.get("last_page_no") == 2
    section = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={
            "heading": "相关工作",
            "notes": "convolution graphs",
            "literature_item_ids": [paper["id"]],
            "manuscript_id": ms["id"],
        },
        headers=h,
    )
    assert section.status_code == 200, section.text
    hits = [x for x in (section.json().get("citations") or []) if x.get("literature_item_id") == paper["id"]]
    assert hits
    assert hits[0].get("progress_pct") == 20
    assert hits[0].get("last_page_no") == 2
    assert hits[0].get("page_no")


def test_h55_deeplink_page_on_graph_digest_compare(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h55a")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "深链", "keywords": ["graph"]},
        headers=h,
    ).json()
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Deep Link Paper", "abstract": "We propose a convolution method on graphs.", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Deep Link Other", "abstract": "baselines on graphs"},
        headers=h,
    ).json()
    prog = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 3, "page_count": 10},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    hrefs = [x.get("href") or "" for x in digest.json().get("items") or []]
    assert any(paper["id"] in href and "page=3" in href for href in hrefs), hrefs
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert graph.status_code == 200, graph.text
    node = next(n for n in graph.json().get("nodes") or [] if n.get("id") == paper["id"])
    assert node.get("last_page_no") == 3
    assert "page=3" in (node.get("href") or "")
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    lit = next(x for x in ov.json().get("literature") or [] if x["id"] == paper["id"])
    assert lit.get("last_page_no") == 3
    assert "page=3" in (lit.get("href") or "")
    cmp = client.post(
        "/api/v1/literature/compare",
        json={"lab_id": lab, "item_ids": [paper["id"], other["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    citem = next(x for x in cmp.json().get("items") or [] if x["id"] == paper["id"])
    assert citem.get("last_page_no") == 3
    assert "page=3" in (citem.get("href") or "")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    ev_hrefs = [e.get("href") or "" for e in cockpit.json().get("recent_events") or []]
    assert any(paper["id"] in href and "page=3" in href for href in ev_hrefs), ev_hrefs


def test_h56_language_note_page_roundtrip(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h56a")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Note Page Paper", "abstract": "methods on page four"},
        headers=h,
    ).json()
    created = client.post(
        f"/api/v1/literature/{lit['id']}/notes",
        json={"content_md": "bound to p4", "page_no": 4},
        headers=h,
    )
    assert created.status_code == 200, created.text
    note = created.json()
    assert note["item_id"] == lit["id"]
    assert note["page_no"] == 4
    assert "page=4" in (note.get("href") or "")
    assert f"note={note['id']}" in (note.get("href") or "")
    listed = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(n for n in listed.json() if n["id"] == note["id"])
    assert "page=4" in (row.get("href") or "")
    polished = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "POLISH_NOTE_P5", "literature_item_id": lit["id"], "write_note": True, "page_no": 5},
        headers=h,
    )
    assert polished.status_code == 200, polished.text
    data = polished.json()
    assert data.get("wrote_note") is True
    assert data.get("page_no") == 5
    assert "page=5" in (data.get("href") or "")
    notes = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h).json()
    fresh = next(n for n in notes if "POLISH_NOTE_P5" in (n.get("content_md") or ""))
    assert fresh["page_no"] == 5
    keep = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "UPDATED_P4", "note_id": note["id"]},
        headers=h,
    )
    assert keep.status_code == 200, keep.text
    again = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h).json()
    old = next(n for n in again if n["id"] == note["id"])
    assert old["page_no"] == 4
    assert "UPDATED_P4" in (old.get("content_md") or "")
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    hrefs = [x.get("href") or "" for x in digest.json().get("items") or [] if x.get("verb") == "note.created"]
    assert any(lit["id"] in href and "page=5" in href for href in hrefs), digest.json().get("items")
    progress = client.get(f"/api/v1/labs/{lab}/progress", headers=h)
    assert progress.status_code == 200, progress.text
    ev = next(x for x in progress.json().get("items") or [] if x.get("verb") == "note.created" and x.get("note_id") == fresh["id"])
    assert ev.get("page_no") == 5
    assert "page=5" in (ev.get("href") or "")


def test_h57_language_manuscript_href_roundtrip(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h57a")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Lang Roundtrip", "kind": "other", "content_md": "KEEP_HEAD"},
        headers=h,
    ).json()
    idle = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "SHOULD_NOT_LAND", "manuscript_id": ms["id"]},
        headers=h,
    )
    assert idle.status_code == 200, idle.text
    assert not idle.json().get("wrote")
    assert "SHOULD_NOT_LAND" not in client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["content_md"]
    wrote = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "APPENDED_FROM_LANG", "manuscript_id": ms["id"], "write_mode": "append"},
        headers=h,
    )
    assert wrote.status_code == 200, wrote.text
    data = wrote.json()
    assert data.get("wrote") is True
    assert data.get("manuscript_id") == ms["id"]
    assert data.get("href") == f"/manuscripts/{ms['id']}"
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "KEEP_HEAD" in body["content_md"]
    assert "APPENDED_FROM_LANG" in body["content_md"]
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    hrefs = [x.get("href") or "" for x in digest.json().get("items") or [] if x.get("verb") == "manuscript.updated"]
    assert any(ms["id"] in href and "/manuscripts/" in href for href in hrefs), digest.json().get("items")

