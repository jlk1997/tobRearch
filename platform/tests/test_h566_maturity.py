from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openlens_ai.agents.paper_reproducer.pipeline import _dispatch_external, diagnose, write_gap_report

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import execute_research_run


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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


class _FakeCfg:
    def __init__(self):
        self.question = ""
        self.dataset_path = ""
        self.domain = ""
        self.language = ""
        self.save_root = ""
        self.thread_id = ""
        self.save_path = ""
        self.llm = SimpleNamespace(
            chat=SimpleNamespace(api_key="", base_url="", model=""),
            vision=SimpleNamespace(api_key="", base_url="", model=""),
        )

    def save_toml(self, path):
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")


def _park_research_awaiting(client, h, lab: str, question: str) -> str:
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=AssertionError("no graph")),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "awaiting_gpu", (row.status, row.error_class, row.error)
    finally:
        db.close()
    return rid


def test_h566_event_href_and_actor_bell(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h566ev")
    sh = _invite_student(client, h, lab, "h566s@example.com")
    rid = _park_research_awaiting(client, sh, lab, "Train a CUDA graph model")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    ev = next(
        x
        for x in cockpit.json().get("recent_events") or []
        if x.get("object_id") == rid and x.get("verb") == "run.awaiting_gpu"
    )
    assert ev.get("href") == f"/runs/{rid}"
    notes = client.get("/api/v1/notifications", headers=sh)
    assert notes.status_code == 200, notes.text
    mine = [x for x in notes.json() if x.get("content") == "run.awaiting_gpu" and x.get("object_id") == rid]
    assert mine, notes.text
    assert mine[0].get("href") == f"/runs/{rid}"
    assert all(x.get("content") != "run.failed" for x in notes.json())
    teacher_notes = client.get("/api/v1/notifications", headers=h)
    assert teacher_notes.status_code == 200, teacher_notes.text
    assert any(x.get("content") == "run.awaiting_gpu" and x.get("href") == f"/runs/{rid}" for x in teacher_notes.json())


def test_h567_diagnosis_next_step_and_external_hint(client, tmp_path):
    gap = {"rows": [{"metric": "acc", "claimed": 0.99, "actual": None, "rel_error": None}]}
    d = diagnose(gap)
    assert d["kind"] == "not_run"
    assert d["suggested_action"] == "improve"
    assert "禁止" in (d.get("next_step") or "")
    assert d.get("metric_gaps")
    md = write_gap_report(
        method_card={"title": "t", "needs_gpu": True},
        env={"status": "awaiting_gpu", "missing": []},
        gap={"rows": []},
        diagnosis={"kind": "gpu", "summary": "管理员未开启 GPU，停止在 awaiting_gpu，不伪造指标。"},
    )
    assert "## 下一步" in md
    assert "恢复" in md
    captured = {}

    class _Resp:
        def read(self):
            return json.dumps({"actual_metrics": {"acc": 0.99}, "logs": "echo"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    with patch("urllib.request.urlopen", fake_urlopen):
        out = _dispatch_external(
            {
                "dataset_path": str(tmp_path),
                "method_card_json": {"title": "ext"},
                "save_path": str(tmp_path),
                "improve_hint": "调学习率，禁止把声称当实测",
                "claimed_metrics": {"acc": 0.99},
                "diagnosis": {"kind": "hyperparams_or_data", "next_step": "再改进一轮"},
            },
            "http://worker.example/run",
        )
    assert captured["body"]["improve_hint"] == "调学习率，禁止把声称当实测"
    assert captured["body"]["claimed_metrics"] == {"acc": 0.99}
    assert captured["body"]["diagnosis"]["next_step"] == "再改进一轮"
    assert out["actual_metrics_json"] == {}

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h567dg")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Diag Abstract Only", "abstract": "We propose without a PDF."},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json={"literature_item_id": lit.json()["id"]}, headers=h)
    assert run.status_code == 200, run.text
    detail = client.get(f"/api/v1/reproduce-runs/{run.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    diag = detail.json().get("diagnosis") or {}
    assert diag.get("suggested_action") == "upload_pdf"
    assert diag.get("next_step")
    assert "## 下一步" in (detail.json().get("gap_report_md") or "")
    assert not (detail.json().get("actual_metrics") or {})
    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == run.json()["id"])
    assert (row.get("diagnosis") or {}).get("suggested_action") == "upload_pdf"


def test_h568_jump_compare_claims_home_honesty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h568jp")
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Compare Empty A", "abstract": "abs a"},
        headers=h,
    )
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Compare Empty B", "abstract": "abs b"},
        headers=h,
    )
    assert a.status_code == 200 and b.status_code == 200
    aid, bid = a.json()["id"], b.json()["id"]
    cmp = client.post("/api/v1/literature/compare", json={"lab_id": lab, "item_ids": [aid, bid]}, headers=h)
    assert cmp.status_code == 200, cmp.text
    crow = next(x for x in cmp.json().get("items") or [] if x["id"] == aid)
    assert (crow.get("card_empty") or {}).get("kind") == "abstract_only"
    assert "panel=card" in (crow.get("card_href") or "")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Claim Empty", "kind": "other", "content_md": "see [@emptya]"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cite = client.post(
        f"/api/v1/manuscripts/{ms.json()['id']}/citations",
        json={"literature_item_id": aid, "citation_key": "emptya"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    claims = client.get(f"/api/v1/manuscripts/{ms.json()['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    cl = next(x for x in claims.json().get("items") or [] if x.get("literature_item_id") == aid)
    assert (cl.get("card_empty") or {}).get("kind") == "abstract_only"
    cites = client.get(f"/api/v1/manuscripts/{ms.json()['id']}/citations", headers=h)
    assert cites.status_code == 200, cites.text
    cited = next(x for x in cites.json() if x.get("literature_item_id") == aid)
    assert (cited.get("card_empty") or {}).get("kind") == "abstract_only"
    jlit = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Compare Empty A"}, headers=h)
    assert jlit.status_code == 200, jlit.text
    lit_hit = next(x for x in jlit.json().get("items") or [] if x.get("id") == aid)
    assert (lit_hit.get("card_empty") or {}).get("kind") == "abstract_only"
    rid = _park_research_awaiting(client, h, lab, "Fine-tune CUDA kernels")
    jumped = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "CUDA"}, headers=h)
    assert jumped.status_code == 200, jumped.text
    hit = next(x for x in jumped.json().get("items") or [] if x.get("id") == rid)
    assert hit["type"] == "research_run"
    assert hit["href"] == f"/runs/{rid}"
    assert hit.get("status") == "awaiting_gpu"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    row = next(x for x in home.json().get("runs") or [] if x["id"] == rid)
    assert row.get("error_class") == "awaiting_gpu"
    assert row.get("href") == f"/runs/{rid}"
    assert "恢复" in (row.get("next_step") or "")
