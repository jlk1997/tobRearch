from __future__ import annotations

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.services import analysis as analysis_mod
from fengsuan.app.services.analysis import select_card_batches


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str):
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract or "We propose a residual detector for small objects."},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_g348_select_card_batches_keeps_head_and_tail():
    batches = [[(i, f"p{i}")] for i in range(1, 6)]
    picked = select_card_batches(batches)
    assert len(picked) == 4
    assert picked[0] is batches[0]
    assert batches[4] in picked
    assert all(b in batches for b in picked)
    assert select_card_batches(batches[:2]) == batches[:2]
    assert select_card_batches([]) == []


def test_g348_analyze_llm_502_returns_empty_card(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g348e")
    paper = _paper(client, h, lab, "ESOD empty")

    def boom(*_a, **_k):
        raise ClassifiedHTTPException(502, "模型服务返回 HTTP 502", "llm")

    monkeypatch.setattr(analysis_mod, "chat", boom)
    r = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    result = body.get("result") or {}
    assert result.get("warning")
    assert "提炼方法" in result["warning"]
    assert body.get("card_empty", {}).get("kind")


def test_g348_analyze_keeps_best_card_when_llm_fails(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g348k")
    paper = _paper(client, h, lab, "ESOD keep")
    first = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert first.status_code == 200, first.text
    kept = first.json().get("result") or {}
    assert kept

    def boom(*_a, **_k):
        raise ClassifiedHTTPException(502, "模型请求超时，请稍后重试", "llm")

    monkeypatch.setattr(analysis_mod, "chat", boom)
    second = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body.get("reused") is True
    shown = body.get("result") or {}
    assert shown.get("warning")
    assert "上次" in shown["warning"]
    if isinstance(kept.get("methods"), dict) and (kept["methods"].get("text") or "").strip():
        assert shown.get("methods", {}).get("text") == kept["methods"]["text"]


def test_g348_analyze_permission_is_not_gateway(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g348p")
    paper = _paper(client, h, lab, "ESOD key")

    def boom(*_a, **_k):
        raise ClassifiedHTTPException(502, "密钥被供应商拒绝，请测通或更换 Key", "permission")

    monkeypatch.setattr(analysis_mod, "chat", boom)
    r = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 400, r.text
    data = r.json()
    assert data.get("error_class") == "permission"
    assert "提炼方法" in (data.get("next_step") or "")
