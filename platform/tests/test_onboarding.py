"""新生入门与成员档案（AGENT_ERA_PLAN 工作流 C）。

技术选型是混合：结构化事实（方向、成员、节点）走 Agent 工具直查数据库，
非结构化知识走现有 KB。这里锁住两条都能用，且权限仍是 teacher/student 两档。
"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": "做图学习与检索增强"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _student(client, h, lab_id: str, email: str) -> tuple[str, dict]:
    inv = client.post(f"/api/v1/labs/{lab_id}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "studentpass1",
            "display_name": email.split("@")[0],
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    return me.json()["id"], sh


def _world(client, h, code: str) -> tuple[str, str]:
    lab_id = _lab(client, h, code)
    parent = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "图学习", "description": "图神经网络与表征", "keywords": ["graph", "gnn"]},
        headers=h,
    )
    assert parent.status_code == 200, parent.text
    child = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "节点分类", "description": "半监督节点分类", "parent_id": parent.json()["id"]},
        headers=h,
    )
    assert child.status_code == 200, child.text
    lit = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Semi-supervised classification with GCN", "abstract": "Graph convolution.", "language": "en"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    client.post(
        f"/api/v1/labs/{lab_id}/kb/documents",
        files={"file": ("intro.md", "本组研究图神经网络，新生先读 GCN。组会每周三。".encode("utf-8"), "text/markdown")},
        headers=h,
    )
    return lab_id, parent.json()["id"]


def test_onboarding_card_answers_the_four_questions(client):
    h = _h(_admin(client))
    lab_id, direction_id = _world(client, h, "onbcard")
    uid, _sh = _student(client, h, lab_id, "newbie@example.com")
    assert uid

    r = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lab"]["name"] == "onbcard"
    assert body["empty"] is False
    # 1 这个组做什么
    assert [d["name"] for d in body["directions"]] == ["图学习"]
    assert [c["name"] for c in body["directions"][0]["children"]] == ["节点分类"]
    assert body["direction_count"] == 2
    # 2 先读哪几篇
    assert body["starter_reading"] and body["starter_reading"][0]["href"].startswith(f"/labs/{lab_id}/literature/")
    assert isinstance(body.get("manuscripts"), list)
    # 3 谁负责什么
    assert any(t["role"] == "teacher" for t in body["teachers"])
    assert any(s["display_name"] == "newbie" for s in body["students"])
    # 4 时间节点 + 追问入口
    assert "milestones" in body
    assert body["ask_href"] == f"/labs/{lab_id}/kb?tab=ask&scope=onboarding"
    assert len(body["suggested_questions"]) == 4
    assert body["next_step"]["title"]
    assert len(body["next_step"]["steps"]) <= 2, "入门卡最多两条下一步，不堆待办"
    assert direction_id


def test_onboarding_is_membership_scoped(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbperm")
    _uid, oh = _student(client, h, lab_id, "insider@example.com")
    other = _lab(client, h, "onbother")
    ok = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=oh)
    assert ok.status_code == 200, ok.text
    denied = client.get(f"/api/v1/labs/{other}/onboarding", headers=oh)
    assert denied.status_code == 403


def test_member_profile_self_edit_and_teacher_override(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbprof")
    uid, sh = _student(client, h, lab_id, "profkid@example.com")

    mine = client.get(f"/api/v1/labs/{lab_id}/members/{uid}/profile", headers=sh)
    assert mine.status_code == 200, mine.text
    assert mine.json()["profile_complete"] is False
    assert mine.json()["can_edit"] is True

    patched = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid}/profile",
        json={
            "bio": "研一，做图上的半监督学习。",
            "interests": ["graph neural network", "自监督"],
            "skills": ["pytorch"],
            "homepage": "https://example.com/me",
            "enrolled_at": "2026-09-01",
        },
        headers=sh,
    )
    assert patched.status_code == 200, patched.text
    got = patched.json()
    assert got["interests"] == ["graph neural network", "自监督"]
    assert got["profile_complete"] is True
    assert got["enrolled_at"].startswith("2026-09-01")
    assert "bio" in got["changed"]

    # 导师可以改任何人，并能指定导师关系。
    admin_me = client.get("/api/v1/auth/me", headers=h).json()
    as_teacher = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid}/profile",
        json={"advisor_user_id": admin_me["id"]},
        headers=h,
    )
    assert as_teacher.status_code == 200, as_teacher.text
    assert as_teacher.json()["advisor_name"]

    # 学生改不了别人的。
    other_uid, _oh = _student(client, h, lab_id, "profkid2@example.com")
    denied = client.patch(
        f"/api/v1/labs/{lab_id}/members/{other_uid}/profile",
        json={"bio": "不该写进去"},
        headers=sh,
    )
    assert denied.status_code == 403

    # 导师必须是本实验室成员。
    bad = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid}/profile",
        json={"advisor_user_id": "not-a-member"},
        headers=h,
    )
    assert bad.status_code == 400


def test_onboarding_ask_uses_agent_tools_not_just_rag(client):
    """「谁做什么」是精确查询：必须走结构化工具，而不是塞进向量库靠检索猜。"""
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbask")
    _student(client, h, lab_id, "asker@example.com")
    r = client.post(
        f"/api/v1/labs/{lab_id}/kb/query",
        json={"q": "这个组在做什么方向，谁负责什么？", "scope": "onboarding"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] is True
    assert body["retrieval_mode"] == "agent"
    tools = {s["tool"] for s in body["steps"]}
    assert "get_direction_tree" in tools
    assert "get_lab_members" in tools
    kinds = {s["kind"] for s in body["sources"]}
    assert "direction" in kinds and "member" in kinds
    assert body["session_id"], "会话 id 要回传，支持无限追问"
    # 老的单轮检索问答仍然可用，没有被拿掉。
    plain = client.post(f"/api/v1/labs/{lab_id}/kb/query", json={"q": "图神经网络"}, headers=h)
    assert plain.status_code == 200, plain.text
    assert not plain.json().get("agent")


def test_onboarding_reading_and_manuscripts_use_tools(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbread")
    ms = client.post(
        f"/api/v1/labs/{lab_id}/manuscripts",
        json={"title": "图卷积综述草稿", "kind": "survey", "content_md": "## 引言\n草稿。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    read = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该从哪几篇文献读起？", "scope": "onboarding"},
        headers=h,
    )
    assert read.status_code == 200, read.text
    tools = {s["tool"] for s in read.json().get("steps") or []}
    assert "get_starter_reading" in tools, tools
    drafts = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里在写什么手稿？", "scope": "onboarding"},
        headers=h,
    )
    assert drafts.status_code == 200, drafts.text
    tools = {s["tool"] for s in drafts.json().get("steps") or []}
    assert "get_lab_manuscripts" in tools, tools
    titles = {s.get("title") for s in drafts.json().get("sources") or []}
    assert "图卷积综述草稿" in titles
    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=h)
    assert card.status_code == 200, card.text
    assert "图卷积综述草稿" in {m.get("title") for m in card.json().get("manuscripts") or []}


def test_onboarding_meetings_use_schedule_not_rag(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbmtg")
    mt = client.post(f"/api/v1/labs/{lab_id}/meetings", json={"title": "周三例会"}, headers=h)
    assert mt.status_code == 200, mt.text
    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "最近组会什么时候开？", "scope": "onboarding"},
        headers=h,
    )
    assert ask.status_code == 200, ask.text
    tools = {s["tool"] for s in ask.json().get("steps") or []}
    assert "get_recent_meetings" in tools, tools
    titles = {s.get("title") for s in ask.json().get("sources") or []}
    assert "周三例会" in titles
    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=h)
    assert card.status_code == 200, card.text
    meetings = card.json().get("meetings") or []
    assert any(m.get("title") == "周三例会" for m in meetings)
    assert meetings[0]["href"].startswith(f"/labs/{lab_id}/meetings?open=")


def test_onboarding_profile_resolves_name_and_self(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbwho")
    uid, sh = _student(client, h, lab_id, "alicebio@example.com")
    patch = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid}/profile",
        json={"bio": "做图卷积压缩", "interests": ["剪枝"]},
        headers=sh,
    )
    assert patch.status_code == 200, patch.text
    named = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "介绍一下 alicebio 的档案", "scope": "onboarding"},
        headers=h,
    )
    assert named.status_code == 200, named.text
    tools = {s["tool"] for s in named.json().get("steps") or []}
    assert "get_member_profile" in tools, tools
    blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in named.json().get("sources") or [])
    assert "alicebio" in blob
    assert "剪枝" in blob or "图卷积压缩" in blob
    mine = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我的档案里写了什么兴趣？", "scope": "onboarding"},
        headers=sh,
    )
    assert mine.status_code == 200, mine.text
    tools = {s["tool"] for s in mine.json().get("steps") or []}
    assert "get_member_profile" in tools, tools
    blob = " ".join((s.get("excerpt") or "") for s in mine.json().get("sources") or [])
    assert "剪枝" in blob


def test_onboarding_advisor_is_own_profile_not_roster(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbadv")
    uid, sh = _student(client, h, lab_id, "advkid@example.com")
    admin = client.get("/api/v1/auth/me", headers=h)
    assert admin.status_code == 200, admin.text
    aid = admin.json()["id"]
    linked = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid}/profile",
        json={"advisor_user_id": aid},
        headers=h,
    )
    assert linked.status_code == 200, linked.text
    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    me = card.json().get("me") or {}
    assert me.get("advisor_user_id") == aid
    assert me.get("advisor_name")
    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我的导师是谁？", "scope": "onboarding"},
        headers=sh,
    )
    assert ask.status_code == 200, ask.text
    tools = {s["tool"] for s in ask.json().get("steps") or []}
    assert "get_member_profile" in tools, tools
    blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in ask.json().get("sources") or [])
    assert me["advisor_name"] in blob or "导师" in blob


def test_onboarding_ask_is_multi_turn(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbturn")
    first = client.post(
        f"/api/v1/labs/{lab_id}/kb/query",
        json={"q": "这个组在做什么方向？", "scope": "onboarding"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    sid = first.json()["session_id"]
    second = client.post(
        f"/api/v1/labs/{lab_id}/kb/query",
        json={"q": "那节点分类这个子课题谁在做？", "scope": "onboarding", "session_id": sid},
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert second.json()["session_id"] == sid
    db = SessionLocal()
    try:
        from fengsuan.app.services.agent.trace import session_steps

        rows = session_steps(db, sid)
        assert len([r for r in rows if r["kind"] == "answer"]) >= 2, "同一会话应留下两轮追踪"
    finally:
        db.close()


def test_kb_documents_carry_source_kind(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbkind")
    docs = client.get(f"/api/v1/labs/{lab_id}/kb/documents", headers=h)
    assert docs.status_code == 200, docs.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.kb import KbDocument, KnowledgeBase

        kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
        assert kb is not None
        rows = db.query(KbDocument).filter(KbDocument.kb_id == kb.id).all()
        assert rows
        assert all(r.source_kind for r in rows), "入门语料与论文要能区分来源类型"
    finally:
        db.close()


def test_onboarding_defaults_to_member_primary_direction(client):
    """入门卡/入门追问默认本人主方向；该方向没有对象时入门卡回退全组。知识库问答不自动收窄。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbdir")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    db_ = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "注意力"}, headers=h)
    dc = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "空方向"}, headers=h)
    assert da.status_code == 200 and db_.status_code == 200 and dc.status_code == 200
    aid, bid, cid = da.json()["id"], db_.json()["id"], dc.json()["id"]
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Paper A Graph", "abstract": "graph", "direction_ids": [aid]},
        headers=h,
    )
    pb = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Paper B Attention", "abstract": "attn", "direction_ids": [bid]},
        headers=h,
    )
    assert pa.status_code == 200 and pb.status_code == 200
    ms_a = client.post(
        f"/api/v1/labs/{lab_id}/manuscripts",
        json={"title": "稿A", "kind": "survey", "content_md": "## 引言\nA", "direction_ids": [aid]},
        headers=h,
    )
    ms_b = client.post(
        f"/api/v1/labs/{lab_id}/manuscripts",
        json={"title": "稿B", "kind": "survey", "content_md": "## 引言\nB", "direction_ids": [bid]},
        headers=h,
    )
    assert ms_a.status_code == 200 and ms_b.status_code == 200

    _uid, sh = _student(client, h, lab_id, "dirkid@example.com")
    picked = client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh)
    assert picked.status_code == 200, picked.text

    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["me"]["direction_id"] == aid
    assert body["reading_scope"] == "direction"
    assert body["manuscript_scope"] == "direction"
    titles = {p["title"] for p in body["starter_reading"]}
    assert "Paper A Graph" in titles
    assert "Paper B Attention" not in titles
    drafts = {m["title"] for m in body["manuscripts"]}
    assert "稿A" in drafts
    assert "稿B" not in drafts
    assert body["ask_href"] == f"/labs/{lab_id}/kb?tab=ask&scope=onboarding&direction_id={aid}"

    admin_card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=h)
    assert admin_card.status_code == 200, admin_card.text
    assert admin_card.json()["ask_href"] == f"/labs/{lab_id}/kb?tab=ask&scope=onboarding"
    admin_titles = {p["title"] for p in admin_card.json()["starter_reading"]}
    assert "Paper A Graph" in admin_titles and "Paper B Attention" in admin_titles

    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该从哪几篇文献读起？", "scope": "onboarding"},
        headers=sh,
    )
    assert ask.status_code == 200, ask.text
    assert ask.json()["direction_id"] == aid
    src = {s.get("title") for s in ask.json().get("sources") or []}
    assert "Paper A Graph" in src
    assert "Paper B Attention" not in src

    lab_ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "Paper B Attention", "scope": "lab"},
        headers=sh,
    )
    assert lab_ask.status_code == 200, lab_ask.text
    assert not lab_ask.json().get("direction_id"), "全库问答不得因本人主方向自动收窄"

    _euid, empty_sh = _student(client, h, lab_id, "emptydir@example.com")
    empty_pick = client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": cid}, headers=empty_sh)
    assert empty_pick.status_code == 200, empty_pick.text
    empty_card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=empty_sh)
    assert empty_card.status_code == 200, empty_card.text
    assert empty_card.json()["me"]["direction_id"] == cid
    assert empty_card.json()["reading_scope"] == "lab"
    empty_titles = {p["title"] for p in empty_card.json()["starter_reading"]}
    assert "Paper A Graph" in empty_titles and "Paper B Attention" in empty_titles
    assert f"direction_id={cid}" in empty_card.json()["ask_href"]
    empty_ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该从哪几篇文献读起？", "scope": "onboarding", "direction_id": cid},
        headers=empty_sh,
    )
    assert empty_ask.status_code == 200, empty_ask.text
    empty_src = {s.get("title") for s in empty_ask.json().get("sources") or []}
    assert "Paper A Graph" in empty_src, "入门追问在空方向上应回退全组，与入门卡同一口径"


def test_onboarding_roster_and_next_step_are_not_direction_silos(client):
    """名册/下一步看全组事实；节点跟主方向，空则回退。入门卡带 direction_id 也不能把导师藏掉。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbrost")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    db_ = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "注意力"}, headers=h)
    dc = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "空方向"}, headers=h)
    assert da.status_code == 200 and db_.status_code == 200 and dc.status_code == 200
    aid, bid, cid = da.json()["id"], db_.json()["id"], dc.json()["id"]
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Roster Paper A", "abstract": "graph", "direction_ids": [aid]},
        headers=h,
    )
    assert pa.status_code == 200, pa.text
    ms_a = client.post(
        f"/api/v1/labs/{lab_id}/directions/{aid}/milestones",
        json={"kind": "proposal", "title": "开题A"},
        headers=h,
    )
    ms_b = client.post(
        f"/api/v1/labs/{lab_id}/directions/{bid}/milestones",
        json={"kind": "defense", "title": "答辩B"},
        headers=h,
    )
    assert ms_a.status_code in {200, 201} and ms_b.status_code in {200, 201}

    uid_a, sh_a = _student(client, h, lab_id, "rosta@example.com")
    uid_b, sh_b = _student(client, h, lab_id, "rostb@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh_a).status_code == 200
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": bid}, headers=sh_b).status_code == 200

    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh_a)
    assert card.status_code == 200, card.text
    names = {s["display_name"] for s in card.json()["students"]}
    assert "rosta" in names and "rostb" in names
    mark_titles = {m["title"] for m in card.json()["milestones"]}
    assert "开题A" in mark_titles
    assert "答辩B" not in mark_titles
    assert card.json()["milestone_scope"] == "direction"

    roster = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "谁负责什么，遇到问题找谁？", "scope": "onboarding", "direction_id": aid},
        headers=sh_a,
    )
    assert roster.status_code == 200, roster.text
    tools = {s["tool"] for s in roster.json().get("steps") or []}
    assert "get_lab_members" in tools, tools
    blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in roster.json().get("sources") or [])
    assert "rostb" in blob
    assert uid_b

    nxt = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该做什么？", "scope": "onboarding"},
        headers=sh_a,
    )
    assert nxt.status_code == 200, nxt.text
    tools = {s["tool"] for s in nxt.json().get("steps") or []}
    assert "get_my_next_step" in tools, tools
    titles = {s.get("title") for s in nxt.json().get("sources") or []}
    assert any("开始" in (t or "") or "这几篇" in (t or "") for t in titles)
    hrefs = {s.get("href") for s in nxt.json().get("sources") or []}
    assert any(f"/literature/{pa.json()['id']}" in (h or "") for h in hrefs)

    nodes = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "接下来有哪些时间节点？", "scope": "onboarding"},
        headers=sh_a,
    )
    assert nodes.status_code == 200, nodes.text
    tools = {s["tool"] for s in nodes.json().get("steps") or []}
    assert "get_milestones" in tools, tools
    node_titles = {s.get("title") for s in nodes.json().get("sources") or []}
    assert "开题A" in node_titles
    assert "答辩B" not in node_titles

    empty_uid, empty_sh = _student(client, h, lab_id, "rostempty@example.com")
    assert empty_uid
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": cid}, headers=empty_sh).status_code == 200
    empty_card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=empty_sh)
    assert empty_card.status_code == 200, empty_card.text
    assert empty_card.json()["milestone_scope"] == "lab"
    empty_marks = {m["title"] for m in empty_card.json()["milestones"]}
    assert "开题A" in empty_marks and "答辩B" in empty_marks


def test_onboarding_helpers_rank_advisor_and_mark_my_direction(client):
    """找谁：导师与同方向排前并标出，其他人仍在。方向树标出你的方向。不另开分工工具。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbhelp")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    db_ = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "注意力"}, headers=h)
    assert da.status_code == 200 and db_.status_code == 200
    aid, bid = da.json()["id"], db_.json()["id"]
    admin = client.get("/api/v1/auth/me", headers=h).json()
    uid_a, sh_a = _student(client, h, lab_id, "helpa@example.com")
    uid_b, _sh_b = _student(client, h, lab_id, "helpb@example.com")
    uid_c, sh_c = _student(client, h, lab_id, "helpc@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh_a).status_code == 200
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh_c).status_code == 200
    assert (
        client.patch(
            f"/api/v1/labs/{lab_id}/members/{uid_b}",
            json={"direction_id": bid},
            headers=h,
        ).status_code
        == 200
    )
    linked = client.patch(
        f"/api/v1/labs/{lab_id}/members/{uid_a}/profile",
        json={"advisor_user_id": admin["id"]},
        headers=h,
    )
    assert linked.status_code == 200, linked.text
    advisor_name = linked.json()["advisor_name"]
    assert advisor_name

    roster = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "遇到问题找谁？", "scope": "onboarding"},
        headers=sh_a,
    )
    assert roster.status_code == 200, roster.text
    tools = {s["tool"] for s in roster.json().get("steps") or []}
    assert "get_lab_members" in tools, tools
    members = [s for s in roster.json().get("sources") or [] if s.get("kind") == "member"]
    assert members
    assert "我的导师" in (members[0].get("excerpt") or "")
    assert advisor_name in (members[0].get("title") or "") or advisor_name in (members[0].get("excerpt") or "")
    blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in members)
    assert "helpb" in blob
    assert "helpc" in blob
    peer = next(s for s in members if "helpc" in ((s.get("title") or "") + (s.get("excerpt") or "")))
    other = next(s for s in members if "helpb" in ((s.get("title") or "") + (s.get("excerpt") or "")))
    assert "同方向" in (peer.get("excerpt") or "")
    assert "同方向" not in (other.get("excerpt") or "")
    assert uid_a and uid_b and uid_c

    tree = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "这个组在做什么方向？", "scope": "onboarding"},
        headers=sh_a,
    )
    assert tree.status_code == 200, tree.text
    dirs = [s for s in tree.json().get("sources") or [] if s.get("kind") == "direction"]
    mine = next(s for s in dirs if s.get("title") == "图学习")
    other = next(s for s in dirs if s.get("title") == "注意力")
    assert "你的方向" in (mine.get("excerpt") or "")
    assert "你的方向" not in (other.get("excerpt") or "")


def test_onboarding_pick_direction_goes_to_workbench(client):
    """学生还没选方向时，入门下一步进工作台，不进只能新建的方向列表。"""
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbpick")
    _uid, sh = _student(client, h, lab_id, "picker@example.com")
    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    step = card.json()["next_step"]
    assert step["title"] == "还没选方向"
    hrefs = [s["href"] for s in step["steps"]]
    assert hrefs == [f"/labs/{lab_id}"]
    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该做什么？", "scope": "onboarding"},
        headers=sh,
    )
    assert ask.status_code == 200, ask.text
    tools = {s["tool"] for s in ask.json().get("steps") or []}
    assert "get_my_next_step" in tools, tools
    ask_hrefs = [s.get("href") for s in ask.json().get("sources") or []]
    assert f"/labs/{lab_id}" in ask_hrefs


def test_onboarding_who_is_reading_names_people_not_just_counts(client):
    """「谁在读」出人名；主方向只列该方向篇目的读者。先读清单带姓名。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbreadwho")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    db_ = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "注意力"}, headers=h)
    assert da.status_code == 200 and db_.status_code == 200
    aid, bid = da.json()["id"], db_.json()["id"]
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Read Paper A", "abstract": "graph", "direction_ids": [aid]},
        headers=h,
    )
    pb = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Read Paper B", "abstract": "attn", "direction_ids": [bid]},
        headers=h,
    )
    assert pa.status_code == 200 and pb.status_code == 200
    _uid, sh = _student(client, h, lab_id, "onbread@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{pa.json()['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{pb.json()['id']}/mark-read", json={"status": "reading"}, headers=h).status_code == 200

    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    row_a = next(p for p in card.json()["starter_reading"] if p["title"] == "Read Paper A")
    assert "onbread" in (row_a.get("reader_names") or [])

    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里谁在读？", "scope": "onboarding"},
        headers=sh,
    )
    assert ask.status_code == 200, ask.text
    tools = {s["tool"] for s in ask.json().get("steps") or []}
    assert "get_paper_readers" in tools, tools
    titles = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in ask.json().get("sources") or [])
    assert "onbread" in titles
    assert "Read Paper A" in titles
    assert "Read Paper B" not in titles

    first = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该从哪几篇文献读起？", "scope": "onboarding"},
        headers=sh,
    )
    assert first.status_code == 200, first.text
    blob = " ".join(s.get("excerpt") or "" for s in first.json().get("sources") or [])
    assert "onbread" in blob


def test_onboarding_same_direction_includes_child_topic(client):
    """挂在父方向与子课题算同方向，名册不把师兄藏成路人。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbchild")
    parent = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    child = client.post(
        f"/api/v1/labs/{lab_id}/directions",
        json={"name": "节点分类", "parent_id": parent.json()["id"]},
        headers=h,
    )
    assert parent.status_code == 200 and child.status_code == 200
    pid, cid = parent.json()["id"], child.json()["id"]
    _uid_a, sh_a = _student(client, h, lab_id, "parentkid@example.com")
    _uid_c, sh_c = _student(client, h, lab_id, "childkid@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": pid}, headers=sh_a).status_code == 200
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": cid}, headers=sh_c).status_code == 200
    roster = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "遇到问题找谁？", "scope": "onboarding"},
        headers=sh_a,
    )
    assert roster.status_code == 200, roster.text
    members = [s for s in roster.json().get("sources") or [] if s.get("kind") == "member"]
    peer = next(s for s in members if "childkid" in ((s.get("title") or "") + (s.get("excerpt") or "")))
    assert "同方向" in (peer.get("excerpt") or "")


def test_lab_ask_empty_direction_does_not_fallback(client):
    """全库问答选了没有节点的方向，不得回退成全组节点。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "labempty")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    dc = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "空方向"}, headers=h)
    assert da.status_code == 200 and dc.status_code == 200
    ms = client.post(
        f"/api/v1/labs/{lab_id}/directions/{da.json()['id']}/milestones",
        json={"kind": "proposal", "title": "开题A"},
        headers=h,
    )
    assert ms.status_code in {200, 201}
    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "接下来有哪些时间节点？", "scope": "lab", "direction_id": dc.json()["id"]},
        headers=h,
    )
    assert ask.status_code == 200, ask.text
    tools = {s["tool"] for s in ask.json().get("steps") or []}
    assert "get_milestones" in tools, tools
    titles = {s.get("title") for s in ask.json().get("sources") or []}
    assert "开题A" not in titles


def test_onboarding_kb_skips_paper_chunks(client):
    h = _h(_admin(client))
    lab_id, _d = _world(client, h, "onbskp")
    marker = "UNIQUE_PAPER_MARKER_XYZ"
    up = client.post(
        f"/api/v1/labs/{lab_id}/kb/documents",
        files={"file": ("gcn-paper.md", f"This paper only: {marker} graph convolution.".encode("utf-8"), "text/markdown")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "admin@example.com").one().id
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="onboarding",
            question=marker,
            feature="kb_query",
            seed_tools=[("search_lab_kb", {"query": marker})],
        )
        blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in out.get("sources") or [])
        assert marker not in blob
    finally:
        db.close()


def test_onboarding_invalid_direction_does_not_fallback(client):
    """胡写或已失效的方向 id 不得回退成全组文献。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbinv")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    assert da.status_code == 200
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Paper A Graph", "abstract": "graph", "direction_ids": [da.json()["id"]]},
        headers=h,
    )
    assert pa.status_code == 200
    _uid, sh = _student(client, h, lab_id, "invalkid@example.com")
    ask = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我该从哪几篇文献读起？", "scope": "onboarding", "direction_id": "not-a-real-direction"},
        headers=sh,
    )
    assert ask.status_code == 200, ask.text
    titles = {s.get("title") for s in ask.json().get("sources") or []}
    assert "Paper A Graph" not in titles

    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "invalkid@example.com").one().id
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="onboarding",
            question="组里读过哪些 graph",
            feature="kb_query",
            default_direction_id="not-a-real-direction",
            seed_tools=[("search_lab_literature", {"query": "graph"})],
        )
        src = {s.get("title") for s in out.get("sources") or []}
        assert "Paper A Graph" not in src
    finally:
        db.close()


def test_onboarding_named_paper_readers_ignore_default_direction(client):
    """点名一篇谁在读时，默认方向不得把该篇读者滤空。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbnamed")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    db_ = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "注意力"}, headers=h)
    assert da.status_code == 200 and db_.status_code == 200
    aid, bid = da.json()["id"], db_.json()["id"]
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Named Paper A", "abstract": "graph", "direction_ids": [aid]},
        headers=h,
    )
    pb = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Named Paper B", "abstract": "attn", "direction_ids": [bid]},
        headers=h,
    )
    assert pa.status_code == 200 and pb.status_code == 200
    _uid, sh = _student(client, h, lab_id, "namedkid@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{pb.json()['id']}/mark-read", json={"status": "reading"}, headers=h).status_code == 200

    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "namedkid@example.com").one().id
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="onboarding",
            question="这篇谁在读？",
            feature="kb_query",
            default_direction_id=aid,
            seed_tools=[("get_paper_readers", {"item_id": pb.json()["id"]})],
        )
        blob = " ".join((s.get("excerpt") or "") + (s.get("title") or "") for s in out.get("sources") or [])
        assert "Named Paper B" in blob
        assert pb.json()["id"] in {s.get("item_id") for s in out.get("sources") or []}
    finally:
        db.close()


def test_reading_roster_omits_removed_members(client):
    """已退组的人不该再出现在「谁在读」。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbleft")
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Left Paper", "abstract": "graph"},
        headers=h,
    )
    assert pa.status_code == 200
    pid = pa.json()["id"]
    uid, sh = _student(client, h, lab_id, "leftkid@example.com")
    assert client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    gone = client.delete(f"/api/v1/labs/{lab_id}/members/{uid}", headers=h)
    assert gone.status_code == 200, gone.text

    db = SessionLocal()
    try:
        from fengsuan.app.services.literature import reading_roster

        rows = reading_roster(db, lab_id, item_id=pid)
        assert uid not in {r["user_id"] for r in rows}
        assert "leftkid" not in {r["display_name"] for r in rows}
        from fengsuan.app.services.onboarding import starter_reading

        first = next((p for p in starter_reading(db, lab_id) if p["id"] == pid), None)
        assert first is not None
        assert first["readers"] == 0
        assert "leftkid" not in (first.get("reader_names") or [])
    finally:
        db.close()


def test_onboarding_empty_direction_search_falls_back_like_card(client):
    """入门追问在空方向上检索文献库，与入门卡同一口径回退全组。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbsefb")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    dc = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "空方向"}, headers=h)
    assert da.status_code == 200 and dc.status_code == 200
    cid = dc.json()["id"]
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Paper A Graph", "abstract": "graph", "direction_ids": [da.json()["id"]]},
        headers=h,
    )
    assert pa.status_code == 200
    _euid, empty_sh = _student(client, h, lab_id, "searchfb@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": cid}, headers=empty_sh).status_code == 200

    db = SessionLocal()
    try:
        from fengsuan.app.models.identity import User
        from fengsuan.app.services.agent import agent_answer

        uid = db.query(User).filter(User.email == "searchfb@example.com").one().id
        out = agent_answer(
            db,
            user_id=uid,
            lab_id=lab_id,
            scope="onboarding",
            question="组里读过哪些 graph",
            feature="kb_query",
            default_direction_id=cid,
            seed_tools=[("search_lab_literature", {"query": "graph"})],
        )
        src = {s.get("title") for s in out.get("sources") or []}
        assert "Paper A Graph" in src
    finally:
        db.close()


def test_onboarding_read_does_not_write_direction_links(client):
    """入门卡读名册不得回填挂靠表，避免 GET 与并发插入撞唯一约束。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbrw")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    assert da.status_code == 200
    aid = da.json()["id"]
    uid, sh = _student(client, h, lab_id, "readonly@example.com")
    db = SessionLocal()
    try:
        from fengsuan.app.models.lab import LabMembership, MembershipDirectionLink

        mem = (
            db.query(LabMembership)
            .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == uid)
            .one()
        )
        mem.direction_id = aid
        db.query(MembershipDirectionLink).filter(
            MembershipDirectionLink.lab_id == lab_id, MembershipDirectionLink.user_id == uid
        ).delete()
        db.commit()
        before = (
            db.query(MembershipDirectionLink)
            .filter(MembershipDirectionLink.lab_id == lab_id, MembershipDirectionLink.user_id == uid)
            .count()
        )
    finally:
        db.close()

    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    assert card.json()["me"]["direction_id"] == aid

    db = SessionLocal()
    try:
        from fengsuan.app.models.lab import MembershipDirectionLink

        after = (
            db.query(MembershipDirectionLink)
            .filter(MembershipDirectionLink.lab_id == lab_id, MembershipDirectionLink.user_id == uid)
            .count()
        )
        assert after == before == 0
    finally:
        db.close()


def test_starter_reading_keeps_older_direction_paper(client):
    """先读按方向在库内筛，不能只看全库最近 60 篇再过滤，把旧的方向文献漏掉。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbold")
    da = client.post(f"/api/v1/labs/{lab_id}/directions", json={"name": "图学习"}, headers=h)
    assert da.status_code == 200
    aid = da.json()["id"]
    old = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Old Dir Paper", "abstract": "graph", "direction_ids": [aid]},
        headers=h,
    )
    assert old.status_code == 200
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    db = SessionLocal()
    try:
        from datetime import timedelta

        from fengsuan.app.db.base import utcnow
        from fengsuan.app.models.literature import LiteratureItem

        now = utcnow()
        for i in range(60):
            db.add(
                LiteratureItem(
                    lab_id=lab_id,
                    title=f"Newer Flood {i}",
                    abstract="other",
                    added_by=admin_id,
                    created_at=now + timedelta(seconds=i + 1),
                )
            )
        db.commit()
    finally:
        db.close()

    _uid, sh = _student(client, h, lab_id, "olddir@example.com")
    assert client.put(f"/api/v1/labs/{lab_id}/me/direction", json={"direction_id": aid}, headers=sh).status_code == 200
    card = client.get(f"/api/v1/labs/{lab_id}/onboarding", headers=sh)
    assert card.status_code == 200, card.text
    titles = {p["title"] for p in card.json()["starter_reading"]}
    assert "Old Dir Paper" in titles
    assert card.json()["reading_scope"] == "direction"
    assert not any(str(t).startswith("Newer Flood") for t in titles)


def test_mark_read_double_write_keeps_one_row(client):
    """开读唯一约束：连点两次仍是一行，第二次是更新不是再插。"""
    h = _h(_admin(client))
    lab_id = _lab(client, h, "onbmk")
    pa = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Mark Once", "abstract": "graph"},
        headers=h,
    )
    assert pa.status_code == 200
    pid = pa.json()["id"]
    uid, sh = _student(client, h, lab_id, "markonce@example.com")
    assert client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "finished"}, headers=sh).status_code == 200
    db = SessionLocal()
    try:
        from fengsuan.app.models.literature import LiteratureReading

        rows = (
            db.query(LiteratureReading)
            .filter(LiteratureReading.item_id == pid, LiteratureReading.user_id == uid)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "finished"
    finally:
        db.close()
