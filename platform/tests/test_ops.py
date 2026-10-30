"""A14：/metrics 与参数化 release 入口。"""

from __future__ import annotations

from fengsuan.app.services.observability import inc, render_prometheus


def test_metrics_endpoint_exposes_agent_and_worker(client):
    inc("agent_steps", 2)
    r = client.get("/metrics")
    assert r.status_code == 200, r.text
    body = r.text
    assert "fengsuan_up 1" in body
    assert "fengsuan_agent_steps_total" in body
    assert "fengsuan_agent_pending_confirm_total" in body
    assert "fengsuan_agent_degraded_total" in body
    assert "fengsuan_worker_busy" in body


def test_prometheus_text_is_stable():
    text = render_prometheus()
    assert text.endswith("\n")
    assert "fengsuan_up 1" in text


def test_release_script_forbids_docker_cp():
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath("deploy", "release.py").read_text(encoding="utf-8")
    assert "docker cp " not in text
    assert "docker compose" in text
    assert "FS_RELEASE_FILES" in text
    assert "modules/file1agent" in text
    assert "requirements-engine.txt" in text
    assert ".dockerignore" in text
