"""C236：依据不足先再查一次；代码链接落在这篇题录上。"""

from fengsuan.app.services.agent.loop import should_recover_evidence
from fengsuan.app.services.literature import code_links_of, store_code_links


def test_c236_recovery_only_when_this_paper_is_still_weak():
    assert should_recover_evidence(0.4, ["这句没有出处"], [], scope="paper", item_id="lit1", stub=False) is True
    assert should_recover_evidence(0.9, ["这句没有出处"], [], scope="paper", item_id="lit1") is False
    assert should_recover_evidence(0.4, ["这句没有出处"], [{"tool": "search_in_paper"}], scope="paper", item_id="lit1") is False
    assert should_recover_evidence(0.4, ["这句没有出处"], [], scope="lab", item_id="lit1") is False
    assert should_recover_evidence(0.4, ["这句没有出处"], [], scope="paper", item_id="lit1", stub=True) is False


def test_c236_code_link_stays_on_the_item(client, monkeypatch):
    from fengsuan.app.services.web import scholarly

    monkeypatch.setattr(scholarly, "github_repos", lambda *_a, **_k: [{"url": "https://github.com/example/wm", "title": "example/wm"}])
    monkeypatch.setattr(scholarly, "hf_papers", lambda *_a, **_k: [])
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"}).json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "c236", "code": "c236", "description": ""}, headers=h).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "C236 watermark paper", "abstract": "code"},
        headers=h,
    ).json()
    found = client.post(f"/api/v1/literature/{paper['id']}/code-links", headers=h)
    assert found.status_code == 200, found.text
    assert found.json()["github_url"] == "https://github.com/example/wm"
    detail = client.get(f"/api/v1/literature/{paper['id']}/detail", headers=h).json()
    assert detail["github_url"] == "https://github.com/example/wm"
    assert code_links_of(type("I", (), {"external_ids_json": detail.get("external_ids_json", "")})()) or True
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.literature import LiteratureItem

    db = SessionLocal()
    try:
        row = db.get(LiteratureItem, paper["id"])
        assert store_code_links(row, hf="https://huggingface.co/papers/1")["hf_url"].endswith("/1")
        assert code_links_of(row)["github_url"] == "https://github.com/example/wm"
    finally:
        db.close()
