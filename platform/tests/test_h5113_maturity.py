from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.services.reproduce import load_improve_diagnosis
from openlens_ai.agents.paper_reproducer.pipeline import (
    _dispatch_external,
    card_implement_brief,
    diagnose,
    implement_prompt,
    improve_hint,
    normalize_actual_metrics,
    run_until_terminal,
    try_implement,
)


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


def test_h5113_stub_try_implement_not_docker():
    out = try_implement(
        {
            "dataset_path": ".",
            "method_card_json": {"title": "T", "problem": "p"},
            "claimed_metrics": {"acc": 0.99},
        }
    )
    assert out["error_class"] == "stub"
    assert out["error_class"] != "docker"
    assert out.get("actual_metrics_json") == {}


def test_h5113_http_stub_or_honest_stop(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h5113st")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "CPU Only Paper", "abstract": "We train a linear model on CPU."},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "StubSet"}, headers=h)
    assert ds.status_code == 200, ds.text
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds.json()["id"],
            "dataset_path": str(tmp_path),
            "claimed_metrics": {"acc": 0.9},
        },
        headers=h,
    )
    assert run.status_code == 200, run.text
    body = run.json()
    assert not (body.get("actual_metrics") or {})
    assert body["status"] in {"failed", "awaiting_gpu", "blocked"}
    assert body.get("error_class") != "docker"
    if body["status"] == "failed":
        assert body.get("error_class") in {"stub", "parse", "need_data", "no_docker", "no_gpu"}
        assert "成功完成实验" not in (body.get("gap_report_md") or "")


def test_h5114_copied_claimed_is_not_run_failed(tmp_path):
    state = run_until_terminal(
        {
            "current_stage": "ingest",
            "method_card_json": {"title": "T", "problem": "p"},
            "dataset_path": str(tmp_path),
            "gpu_mode": "off",
            "claimed_metrics": {"acc": 0.99},
            "implement_fn": lambda st: {"actual_metrics_json": {"acc": 0.99}, "logs": "echo claimed"},
        }
    )
    assert state["status"] == "failed"
    assert (state.get("diagnosis") or {}).get("kind") == "not_run"
    assert not (state.get("actual_metrics_json") or {})
    assert "成功完成实验" not in (state.get("gap_report_md") or "")
    d = diagnose(
        {"rows": [{"metric": "acc", "claimed": 0.9, "actual": 0.9, "rel_error": 0}]},
        claimed={"acc": 0.9},
        actual={"acc": 0.9},
    )
    assert d["kind"] == "not_run"


def test_h5115_illegal_actual_file_rejected(tmp_path):
    assert normalize_actual_metrics({"acc": 0.2, "f1": 1}) == {"acc": 0.2, "f1": 1}
    assert normalize_actual_metrics(["not", "schema"]) == {}
    assert normalize_actual_metrics({"acc": [0.1, 0.2]}) == {}
    assert normalize_actual_metrics({"notes": "ok"}) == {}
    dest = tmp_path / "workspace"
    dest.mkdir()
    (dest / "actual_metrics.json").write_text('["not", "a", "schema"]', encoding="utf-8")
    loaded = json.loads((dest / "actual_metrics.json").read_text(encoding="utf-8"))
    assert normalize_actual_metrics(loaded) == {}
    state = run_until_terminal(
        {
            "current_stage": "ingest",
            "method_card_json": {"title": "T", "problem": "p"},
            "dataset_path": str(tmp_path),
            "gpu_mode": "off",
            "claimed_metrics": {"acc": 0.9},
            "implement_fn": lambda st: {"actual_metrics_json": loaded, "logs": "illegal file"},
        }
    )
    assert state["status"] == "failed"
    assert not (state.get("actual_metrics_json") or {})
    assert state.get("error_class") == "parse"
    assert (state.get("diagnosis") or {}).get("kind") == "not_run"


def test_h5116_improve_eats_diagnosis_and_card_fields(tmp_path, monkeypatch):
    brief = card_implement_brief(
        {
            "title": "T",
            "problem": {"text": "分类问题"},
            "methods": {"text": "线性模型"},
            "metrics": [{"name": "acc", "value": 0.99}],
        }
    )
    assert "problem:" in brief and "分类问题" in brief
    assert "methods:" in brief and "线性模型" in brief
    assert "metrics:" in brief and "acc" in brief
    hint = improve_hint(
        {
            "diagnosis": {
                "kind": "not_run",
                "summary": "尚未得到实测指标",
                "next_step": "改进一轮补实测",
                "suggested_action": "improve",
            }
        }
    )
    assert "not_run" in hint
    assert "尚未得到实测" in hint
    assert "改进一轮补实测" in hint
    dest = tmp_path / "run"
    dest.mkdir()
    (dest / "diagnosis.json").write_text(
        json.dumps({"kind": "not_run", "summary": "尚未得到实测指标", "next_step": "改进一轮补实测"}, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_improve_diagnosis(SimpleNamespace(save_path=str(dest), error="旧错误"))
    assert loaded["kind"] == "not_run"
    assert "尚未得到实测" in (loaded.get("summary") or "")
    prompt = implement_prompt(
        {
            "method_card_json": {
                "title": "T",
                "problem": {"text": "分类问题"},
                "methods": {"text": "线性模型"},
                "metrics": [{"name": "acc", "value": 0.99}],
            },
            "claimed_metrics": {"acc": 0.99},
            "improve_hint": hint,
        }
    )
    assert "problem:" in prompt and "分类问题" in prompt
    assert "methods:" in prompt and "线性模型" in prompt
    assert "metrics:" in prompt
    assert "尚未得到实测" in prompt
    assert "改进一轮补实测" in prompt


def test_h5117_external_claimed_copy_rejected(tmp_path):
    class _Resp:
        def read(self):
            return json.dumps({"actual_metrics": {"acc": 0.99}, "logs": "echo"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    with patch("urllib.request.urlopen", lambda *a, **k: _Resp()):
        out = _dispatch_external(
            {
                "dataset_path": str(tmp_path),
                "method_card_json": {"title": "ext"},
                "save_path": str(tmp_path),
                "claimed_metrics": {"acc": 0.99},
                "diagnosis": {"kind": "not_run", "next_step": "再改进一轮"},
            },
            "http://worker.example/run",
        )
    assert out["actual_metrics_json"] == {}
    assert out["error_class"] == "parse"

    class _Bad:
        def read(self):
            return json.dumps({"actual_metrics": ["nope"], "logs": "bad"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    with patch("urllib.request.urlopen", lambda *a, **k: _Bad()):
        bad = _dispatch_external(
            {"claimed_metrics": {"acc": 0.1}, "save_path": str(tmp_path)},
            "http://worker.example/run",
        )
    assert bad["actual_metrics_json"] == {}
    assert bad["error_class"] == "parse"


def test_h5113_no_docker_class(tmp_path, monkeypatch):
    monkeypatch.delenv("FENGSUN_SKIP_HEAVY", raising=False)
    monkeypatch.delenv("FENGSUN_LLM_STUB", raising=False)
    monkeypatch.setattr("shutil.which", lambda n: None)
    out = try_implement(
        {
            "dataset_path": str(tmp_path),
            "save_path": str(tmp_path),
            "gpu_mode": "off",
            "method_card_json": {"title": "T", "problem": "p"},
        }
    )
    assert out["error_class"] == "no_docker"
    assert out["actual_metrics_json"] == {}
