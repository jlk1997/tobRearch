from __future__ import annotations

import json
from datetime import datetime, timezone
from fengsuan.app.core.concurrency import embed_worker, read_sidecar_tick, worker_enabled
from fengsuan.app.services.research_engine import classify_research_failure, engine_runtime_missing, load_engine_config
from fengsuan.app.core.config import get_settings
from fengsuan.app.services.worker import worker_snapshot


def test_sidecar_tick_makes_worker_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FENGSUN_WORKER", "0")
    monkeypatch.setenv("FILE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    assert embed_worker() is False
    assert worker_enabled() is False
    snap = worker_snapshot()
    assert snap["enabled"] is False
    assert "工人未开" in (snap.get("next_step") or "")

    (tmp_path / "worker.tick").write_text(
        json.dumps(
            {
                "last_tick_at": datetime.now(timezone.utc).isoformat(),
                "claims": 2,
                "errors": 0,
            }
        ),
        encoding="utf-8",
    )
    tick = read_sidecar_tick()
    assert tick is not None
    assert tick["claims"] == 2
    assert worker_enabled() is True
    live = worker_snapshot()
    assert live["enabled"] is True
    assert live["lock"] == "held"
    assert live["next_step"] == ""
    get_settings.cache_clear()


def test_stale_sidecar_tick_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FENGSUN_WORKER", "0")
    monkeypatch.setenv("FILE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "worker.tick").write_text(
        json.dumps({"last_tick_at": "2020-01-01T00:00:00+00:00", "claims": 1, "errors": 0}),
        encoding="utf-8",
    )
    assert read_sidecar_tick() is None
    assert worker_enabled() is False
    get_settings.cache_clear()


def test_module_not_found_is_parse_not_llm():
    assert classify_research_failure(ModuleNotFoundError("toml"), gpu_mode="off", needs_gpu=False) == "parse"


def test_engine_runtime_missing_is_honest_text():
    msg = engine_runtime_missing()
    assert isinstance(msg, str)
    if msg:
        assert "文献与写作仍可用" in msg
    else:
        assert load_engine_config() is not None
