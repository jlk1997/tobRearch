from __future__ import annotations

from openlens_ai.agents.paper_reproducer.pipeline import env_check, run_until_terminal


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def test_env_check_blocks_without_data():
    env = env_check(dataset_path="", gpu_mode="off", needs_gpu=False)
    assert env["status"] == "blocked"
    assert env["error_class"] == "need_data"


def test_pipeline_blocked_without_dataset():
    state = run_until_terminal(
        {
            "current_stage": "ingest",
            "title": "toy",
            "pdf_text": "A simple method",
            "dataset_path": "",
            "gpu_mode": "off",
            "claimed_metrics": {},
            "method_card_json": {"title": "toy", "problem": "classify", "needs_gpu": False},
        }
    )
    assert state["status"] == "blocked"
    assert state["error_class"] == "need_data"
    assert "无实测" in (state.get("gap_report_md") or "")


def test_apply_glossary_lock():
    from fengsuan.app.services.language import apply_glossary

    text = apply_glossary(
        "U-Net is used",
        [{"src": "U-Net", "tgt": "U型网络", "do_not_translate": True}],
    )
    assert "U-Net" in text
    assert "U型网络" not in text


def test_bibtex_import(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "Bib", "code": "bib1", "description": ""}, headers=h).json()["id"]
    bib = "@article{x,\n  title={Hello World Paper},\n  year={2021},\n  author={Alice}\n}"
    r = client.post("/api/v1/literature/import-bibtex", json={"lab_id": lab, "bibtex": bib}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    assert any("Hello World" in x["title"] for x in listed)


def test_capture_import(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "Cap", "code": "cap1", "description": ""}, headers=h).json()["id"]
    r = client.post(
        "/api/v1/literature/import-capture",
        json={
            "lab_id": lab,
            "page_url": "https://arxiv.org/abs/1706.03762",
            "title": "Attention Is All You Need",
            "site_adapter": "arxiv",
            "mark_read": True,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "Attention" in r.json()["title"]


def test_kb_lab_isolation(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab_a = client.post("/api/v1/labs", json={"name": "KA", "code": "kba1", "description": ""}, headers=h).json()["id"]
    lab_b = client.post("/api/v1/labs", json={"name": "KB", "code": "kbb1", "description": ""}, headers=h).json()["id"]
    up = client.post(
        f"/api/v1/labs/{lab_a}/kb/documents",
        files={"file": ("a.txt", b"UNIQUE_TOKEN_LAB_A_XYZ appears here", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    qa = client.post(f"/api/v1/labs/{lab_a}/kb/query", json={"q": "UNIQUE_TOKEN_LAB_A_XYZ"}, headers=h)
    assert qa.status_code == 200, qa.text
    assert "UNIQUE_TOKEN_LAB_A_XYZ" in str(qa.json())
    for hit in qa.json()["hits"]:
        assert hit["citation"] in hit["content"]
    qb = client.post(f"/api/v1/labs/{lab_b}/kb/query", json={"q": "UNIQUE_TOKEN_LAB_A_XYZ"}, headers=h)
    assert qb.status_code == 200
    assert "UNIQUE_TOKEN_LAB_A_XYZ" not in str(qb.json().get("hits"))


def test_reproduce_blocked_without_data(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "R", "code": "repr1", "description": ""}, headers=h).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Need Data Paper", "abstract": "A method without a public dataset path.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    r = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lit.json()["id"], "dataset_path": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "blocked"
    assert body["error_class"] == "need_data"


def test_translate_locks_glossary(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "L", "code": "lang1", "description": ""}, headers=h).json()["id"]
    g = client.post(
        f"/api/v1/labs/{lab}/glossary",
        json={"src": "U-Net", "tgt": "U型网络", "do_not_translate": True},
        headers=h,
    )
    assert g.status_code == 200, g.text
    r = client.post(
        f"/api/v1/labs/{lab}/language/translate",
        json={"text": "The U-Net model is simple.", "target": "zh"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "U-Net" in r.json()["text"]


def test_meeting_to_board(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "M", "code": "meet1", "description": ""}, headers=h).json()["id"]
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "组会", "notes_raw": "行动: 读一篇论文"}, headers=h)
    assert m.status_code == 200, m.text
    mid = m.json()["id"]
    out = client.post(f"/api/v1/meetings/{mid}/minutes", json={"notes_raw": "行动: 读一篇论文"}, headers=h)
    assert out.status_code == 200, out.text
    assert out.json()["actions"]
    tasks = client.get(f"/api/v1/labs/{lab}/tasks", headers=h).json()
    assert any("读一篇论文" in t["title"] for t in tasks)


def test_arxiv_html_meta_fixture():
    from fengsuan.app.services.literature import parse_html_meta

    html = """
    <html><head>
    <meta name="citation_title" content="Attention Is All You Need">
    <meta name="citation_author" content="Ashish Vaswani">
    <meta name="citation_doi" content="10.48550/arXiv.1706.03762">
    </head></html>
    """
    meta = parse_html_meta(html, "https://arxiv.org/abs/1706.03762")
    assert meta["title"].startswith("Attention")
    assert meta["site_adapter"] == "arxiv"
    assert meta["doi"]


def test_research_run_cancel(client):
    token = _admin(client)
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "RR", "code": "run1", "description": ""}, headers=h).json()["id"]
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "toy question", "dataset_path": ""},
        headers=h,
    )
    assert run.status_code == 200, run.text
    rid = run.json()["id"]
    c = client.post(f"/api/v1/research-runs/{rid}/cancel", headers=h)
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"
