from __future__ import annotations

from unittest.mock import patch

from fengsuan.app.services.analysis import (
    claimed_metrics_from_card,
    excerpt_in_pages,
    filter_gap_md,
    ground_card,
    ground_tutor_answer,
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


def test_h596_metrics_and_short_excerpt_need_source():
    pages = ["The model reports accuracy 0.80 on CIFAR."]
    assert excerpt_in_pages("0.80", pages, None) is True
    assert excerpt_in_pages("0.99", pages, None) is False
    fake = ground_card({"metrics": [{"name": "acc", "value": 0.99}, {"name": "acc", "value": 0.80, "text": "accuracy 0.80"}]}, pages)
    assert fake["metrics"][0].get("ungrounded") is True
    assert claimed_metrics_from_card(fake) == {"acc": 0.80}
    short = ground_card({"methods": {"text": "quantum", "pages": [1]}}, pages)
    assert short["methods"].get("ungrounded") is True
    assert short["methods"].get("text") == ""


def test_h597_gap_md_drops_unsourced_sentences():
    aid, bid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    kept = filter_gap_md(f"A 更强（来源 {aid}）。这句没有出处。B 缺数据（{bid}）。", [aid, bid])
    assert aid in kept
    assert bid in kept
    assert "没有出处" not in kept
    assert filter_gap_md("整表顶替的综述，没有任何文献编号。", [aid]) == ""


def test_h597_compare_api_keeps_sourced_stub_gap(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h597gp")
    p1 = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Gap A", "abstract": "alpha method"}, headers=h)
    p2 = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Gap B", "abstract": "beta method"}, headers=h)
    assert p1.status_code == 200 and p2.status_code == 200
    cmp = client.post("/api/v1/literature/compare", json={"lab_id": lab, "item_ids": [p1.json()["id"], p2.json()["id"]]}, headers=h)
    assert cmp.status_code == 200, cmp.text
    gap = cmp.json().get("gap_md") or ""
    assert p1.json()["id"] in gap or p2.json()["id"] in gap
    with patch("fengsuan.app.services.analysis.chat", return_value='{"table_md":"| a | b |","gap_md":"没有编号的整段综述。","json":{}}'):
        empty = client.post(
            "/api/v1/literature/compare",
            json={"lab_id": lab, "item_ids": [p1.json()["id"], p2.json()["id"]]},
            headers=h,
        )
    assert empty.status_code == 200, empty.text
    assert empty.json().get("gap_md") == ""
    assert empty.json().get("table_md")


def test_h598_tutor_stamps_ungrounded_numbers(client):
    pages = "We propose a convolution method on graphs."
    ok = ground_tutor_answer("请结合本页与方法卡继续阅读。", page_text=pages, card={})
    assert ok["ungrounded"] is False
    assert not ok["next_question"]
    bad = ground_tutor_answer('准确率达到 99.9%，并引用「quantum fusion」。', page_text=pages, card={})
    assert bad["ungrounded"] is True
    assert "本页" in bad["next_question"]
    assert "缺少原文依据" in bad["answer"]
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h598tt")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Tutor Ground Paper", "abstract": pages},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    # 辅导走 Agent 运行时，编出来的数字必须在「实际引用到的来源」里对不上就被标记。
    with patch("fengsuan.app.services.llm.stub_complete", return_value="准确率达到 99.9%。"):
        tut = client.post(
            f"/api/v1/literature/{lit.json()['id']}/tutor",
            json={"question": "效果如何？", "page_no": 1},
            headers=h,
        )
    assert tut.status_code == 200, tut.text
    assert tut.json().get("ungrounded") is True
    assert "本页" in (tut.json().get("next_question") or "")
    live = client.post(
        f"/api/v1/literature/{lit.json()['id']}/tutor",
        json={"question": "这一页方法在做什么？", "page_no": 1},
        headers=h,
    )
    assert live.status_code == 200, live.text
    assert live.json().get("answer")
    assert live.json().get("ungrounded") is False
