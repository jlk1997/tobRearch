from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_analyze_abstract_only_and_related(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "AN", "code": "an1", "description": ""}, headers=h).json()["id"]
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Diffusion Medical Imaging", "abstract": "A method for diffusion models."},
        headers=h,
    ).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Diffusion Medical Imaging Survey", "abstract": "survey"},
        headers=h,
    )
    r = client.post(f"/api/v1/literature/{a}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["source"] == "abstract_only"
    assert result.get("warning")
    assert "problem" in result
    assert any(x.get("literature_item_id") for x in result.get("related_in_lab") or [])
    assert "quota_warning" in r.json()


def test_analyze_parse_classified(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "AP", "code": "anp", "description": ""}, headers=h).json()["id"]
    item = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "tmp", "abstract": "x"}, headers=h).json()["id"]
    client.patch(f"/api/v1/literature/{item}", json={"title": "", "abstract": ""}, headers=h)
    r = client.post(f"/api/v1/literature/{item}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error_class"] == "parse"
    assert "PDF" in body["detail"] or "摘要" in body["detail"]


def test_language_missing_key_classified(client, monkeypatch):
    monkeypatch.setenv("FENGSUN_LLM_STUB", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FENGSUN_API_KEY", raising=False)
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "NK", "code": "nk1", "description": ""}, headers=h).json()["id"]
    r = client.post("/api/v1/language/polish", json={"lab_id": lab, "text": "hello"}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["error_class"] == "permission"
    assert r.json()["detail"]


def test_kb_hit_has_literature_item_and_page(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "KH", "code": "kh1", "description": ""}, headers=h).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "UniqueZebraTitle Paper", "abstract": "UniqueZebraTitle methods on page one."},
        headers=h,
    ).json()["id"]
    kb = client.post(f"/api/v1/literature/{lit}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "UniqueZebraTitle"}, headers=h)
    assert q.status_code == 200, q.text
    hits = q.json()["hits"]
    assert hits
    assert any(x.get("literature_item_id") == lit for x in hits)
    assert all(x.get("citation") in x.get("content", "") for x in hits)


def test_literature_page_and_quota_warning(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "PG", "code": "pg1", "description": ""}, headers=h).json()["id"]
    client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paged One", "abstract": "a"}, headers=h)
    client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paged Two", "abstract": "b"}, headers=h)
    r = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "page_size": 1}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    full = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert isinstance(full.json(), list)


def test_key_test_classified_and_no_traceback(client):
    token = _admin(client)
    h = _h(token)
    r = client.post("/api/v1/me/llm-credentials/test", headers=h)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error_class"] == "permission"
    assert "Traceback" not in r.text
    assert "api_key" not in r.text.lower() or "masked" in r.text.lower()


def test_classified_exception_is_not_http_exception():
    from fastapi import HTTPException

    from fengsuan.app.core.errors import ClassifiedHTTPException

    assert not issubclass(ClassifiedHTTPException, HTTPException)


def test_upload_pdfs_marks_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "UP", "code": "up1", "description": ""}, headers=h).json()["id"]
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("scan.pdf", b"not-a-real-pdf", "application/pdf"))],
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1
    assert r.json()["items"][0]["parse_status"] == "failed"
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    assert listed
    assert listed[0]["my_reading"] == "reading"
    detail = client.get(f"/api/v1/literature/{listed[0]['id']}/detail", headers=h).json()
    err = (detail.get("files") or [{}])[0].get("parse_error") or ""
    assert "Traceback" not in err
    assert "extract_failed" in err or err == ""
