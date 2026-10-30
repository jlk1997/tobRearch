"""C239：检索主路径合并预印本；阅读习惯只留给本人，不进公开档案。"""

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.lab import LabMembership
from fengsuan.app.services.agent.habits import user_habits


def _admin(client):
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"}).json()["access"]
    return {"Authorization": f"Bearer {token}"}


def test_c239_search_merges_preprint_and_published(client, monkeypatch):
    from fengsuan.app.services import literature as lit

    def fake(*_a, **_k):
        return [
            {
                "title": "Learned Compression",
                "authors": ["Ada Lovelace"],
                "year": 2023,
                "arxiv_id": "2301.1",
                "venue": "arXiv",
                "source": "arxiv",
            },
            {
                "title": "Learned Compression",
                "authors": [{"name": "Ada Lovelace"}],
                "year": 2024,
                "doi": "10.1000/lc",
                "venue": "CVPR",
                "source": "crossref",
            },
        ], False

    monkeypatch.setattr(lit, "cached_search_sources", fake)
    h = _admin(client)
    lab = client.post("/api/v1/labs", json={"name": "c239", "code": "c239", "description": ""}, headers=h).json()["id"]
    found = client.post(f"/api/v1/labs/{lab}/literature/search", json={"q": "compression", "sources": ["arxiv"]}, headers=h)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["version_merged"] == 1
    assert "正式版" in body["version_note"]
    assert len(body["items"]) == 1
    assert body["items"][0]["doi"] == "10.1000/lc"


def test_c239_habits_stay_off_the_public_profile(client):
    h = _admin(client)
    lab = client.post("/api/v1/labs", json={"name": "c239h", "code": "c239h", "description": ""}, headers=h).json()["id"]
    me = client.get("/api/v1/auth/me", headers=h).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=me,
                feature="agent",
                kind="tool",
                tool="get_direction_frontier",
                args_json=json.dumps({"topic": "水印", "years": 2}, ensure_ascii=False),
            )
        )
        db.commit()
        got = user_habits(db, me, lab)
        db.commit()
        assert "水印" in got["note"]
        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab, LabMembership.user_id == me).one()
        stored = json.loads(mem.academic_json or "{}")
        assert stored["_habits"]["note"] == got["note"]
    finally:
        db.close()
    profile = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me}", headers=h).json()["profile"]
    assert "_habits" not in (profile.get("academic") or {})
