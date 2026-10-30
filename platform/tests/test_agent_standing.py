"""组里助手：名册写成中文，导师可写说话方式和一篇要记住的事。"""

from fengsuan.app.services.agent.loop import (
    _compose_activity,
    _compose_catalog,
    _compose_glossary,
    _compose_meetings,
    _compose_counts,
    _compose_intro,
    _compose_members,
    _is_intro_question,
    _compose_tasks,
    _compose_work,
    _followups,
    _needs_zh_rewrite,
    narration_to_zh,
)


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_english_aside_is_rewritten_even_with_a_few_hanzi():
    q = "这个组导师是谁？"
    answer = (
        "1. adminControl ·成员\n"
        "The materials show 6 members but none of them are identified as 导师."
    )
    assert _needs_zh_rewrite(q, answer)
    assert narration_to_zh(q, answer).startswith("这次写成了英文")
    assert narration_to_zh("Write the method", "The method uses a transformer and reports accuracy.") == (
        "The method uses a transformer and reports accuracy."
    )


def test_compose_members_names_the_teacher():
    sources = type("S", (), {})()
    sources.items = [
        {"id": "member:1", "kind": "member", "role": "teacher", "title": "吴雪", "member_title": "教授", "label": "S1"},
        {"id": "member:2", "kind": "member", "role": "student", "title": "qin", "member_title": "", "label": "S2"},
    ]
    text = _compose_members("这个组导师是谁？", sources)
    assert text.startswith("名册里标成导师的是吴雪")
    assert "The materials" not in text
    assert "[S1]" in text


def test_compose_intro_quotes_the_lab_note():
    sources = type("S", (), {})()
    sources.items = [
        {"id": "kb:1", "kind": "lab_intro", "title": "组里要记住的", "excerpt": "对外称吴雪为导师。组会每周三。", "label": "S1"},
    ]
    text = _compose_intro(sources)
    assert text.startswith("组里写过：对外称吴雪为导师")
    assert "[S1]" in text
    assert "。。" not in text
    chips = _followups("组里怎么称呼？", [], [], [{"kind": "lab_intro", "title": "组里要记住的", "id": "kb:1"}])
    assert any("组里还写了什么" in c for c in chips)


def test_compose_intro_quotes_the_matching_section():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "kb:1",
            "kind": "lab_intro",
            "title": "组里要记住的",
            "excerpt": "【称呼】\n对外称吴雪为导师。\n\n【约定】\n组会每周三。\n\n【前提】\n没进组的论文要单独说。",
            "label": "S1",
        }
    ]
    called = _compose_intro(sources, "组里怎么称呼？")
    assert "对外称吴雪为导师" in called
    assert "组会每周三" not in called
    agreed = _compose_intro(sources, "组里约定是什么？")
    assert "组会每周三" in agreed
    assert "对外称吴雪" not in agreed
    missing = _compose_intro(
        type("S", (), {"items": [{"id": "kb:1", "kind": "lab_intro", "excerpt": "【约定】\n组会每周三。", "label": "S1"}]})(),
        "组里怎么称呼？",
    )
    assert missing == "组里还没写称呼。"
    roster = type("S", (), {})()
    roster.items = [
        {"id": "member:1", "kind": "member", "role": "teacher", "title": "吴雪", "label": "S1"},
        {
            "id": "kb:1",
            "kind": "lab_intro",
            "excerpt": "【称呼】\n对外称张老师为导师。\n\n【约定】\n吴雪负责组会。",
            "label": "S2",
        },
    ]
    named = _compose_members("这个组导师是谁？", roster)
    assert "吴雪" in named
    assert "张老师" in named


def test_premise_and_voice_are_not_taken_from_another_section():
    assert _is_intro_question("组里的前提是什么")
    assert not _is_intro_question("这篇论文的前提是什么")
    sources = type("S", (), {})()
    sources.items = [
        {"id": "kb:old", "kind": "lab_intro", "excerpt": "称呼：对外称吴雪为导师。", "label": "S1"},
        {
            "id": "kb:1",
            "kind": "lab_intro",
            "excerpt": "称呼：对外称吴雪为导师。",
            "intro_address": "对外称吴雪为导师。",
            "intro_custom": "组会每周三。",
            "intro_premise": "没进组的论文要单独说。",
            "agent_voice": "先说结论，不要客套。",
            "label": "S2",
        },
    ]
    premise = _compose_intro(sources, "组里的前提是什么？")
    assert "没进组的论文要单独说" in premise
    assert "吴雪" not in premise
    speak = _compose_intro(sources, "组里怎么说话？")
    assert "先说结论" in speak
    assert "吴雪" not in speak
    quiet = _compose_intro(
        type("S", (), {"items": [{"id": "kb:1", "kind": "lab_intro", "intro_address": "对外称吴雪为导师。", "label": "S1"}]})(),
        "组里怎么说话？",
    )
    assert quiet == "还没写怎么说话。"


def test_compose_counts_uses_the_library_numbers():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "stats:lab:all",
            "kind": "biblio",
            "title": "组里论文的数字",
            "excerpt": "组里一共 12 篇，8 篇有 PDF。",
            "label": "S1",
        }
    ]
    text = _compose_counts(sources)
    assert text.startswith("组里一共 12 篇")
    assert "[S1]" in text
    assert "The library" not in text
    empty = _compose_counts(type("S", (), {"items": []})(), [{"tool": "get_library_stats", "result_summary": "组里还没有论文。"}])
    assert empty == "组里还没有论文。"
    chips = _followups("组里有多少篇论文？", [], [], [{"kind": "biblio", "id": "stats:lab:all", "title": "组里论文的数字"}])
    assert any("年份" in c for c in chips)
    assert all("被谁引用" not in c for c in chips)


def test_compose_catalog_names_papers_and_reading():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "litcat:1",
            "kind": "literature",
            "title": "水印稳健性",
            "year": 2024,
            "my_reading": "reading",
            "label": "S1",
        },
        {"id": "litcat:lab", "kind": "literature", "title": "还没有文献", "label": "S2"},
    ]
    text = _compose_catalog("组里有哪些文献？", sources)
    assert text.startswith("文献库里先列出这几篇。")
    assert "水印稳健性（2024，在读） [S1]" in text
    assert "还没有文献" not in text
    empty = _compose_catalog("组里有哪些文献？", type("S", (), {"items": []})(), [{"tool": "get_lab_literature", "result_summary": "还没有文献。"}])
    assert empty == "还没有文献。"


def test_compose_work_quotes_the_brief():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "member:1",
            "kind": "member",
            "title": "吴雪",
            "role": "teacher",
            "excerpt": "吴雪：在做水印；在读《稳健性》",
            "label": "S1",
        }
    ]
    text = _compose_work(sources)
    assert "吴雪：在做水印" in text
    assert "[S1]" in text


def test_compose_tasks_and_activity_use_the_rows():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "task:1",
            "kind": "task",
            "title": "写周报",
            "task_status": "todo",
            "assignee": "吴雪",
            "due_label": "未定截止",
            "label": "S1",
        },
        {"id": "tasks:lab", "kind": "task", "title": "还没有任务", "label": "S2"},
    ]
    text = _compose_tasks("我的任务有哪些？", sources)
    assert text.startswith("派给你的有这几条。")
    assert "写周报（待办，吴雪，未定截止） [S1]" in text
    assert "还没有任务" not in text
    empty = _compose_tasks("我的任务有哪些？", type("S", (), {"items": []})(), [{"tool": "get_lab_tasks", "result_summary": "还没有任务。"}])
    assert empty == "还没有任务。"
    act = type("S", (), {})()
    act.items = [
        {"id": "wlog:1", "kind": "work_log", "title": "读完水印那篇", "actor": "吴雪", "label": "S1"},
    ]
    said = _compose_activity("最近做了什么？", act)
    assert said.startswith("最近记下的有这几条，不是排名。")
    assert "读完水印那篇（吴雪） [S1]" in said
    chips = _followups("最近做了什么？", [], [], [{"kind": "work_log", "id": "wlog:1", "title": "读完水印那篇"}])
    assert any("任务" in c for c in chips)


def test_compose_glossary_and_meetings():
    gloss = type("S", (), {})()
    gloss.items = [
        {"id": "term:1", "kind": "glossary", "title": "水印", "term_tgt": "watermark", "locked": False, "label": "S1"},
    ]
    one = _compose_glossary("水印怎么译？", gloss)
    assert one.startswith("「水印」译成watermark")
    assert "[S1]" in one
    many = type("S", (), {})()
    many.items = [
        {"id": "term:1", "kind": "glossary", "title": "水印", "locked": True, "label": "S1"},
        {"id": "term:2", "kind": "glossary", "title": "组会", "term_tgt": "", "locked": False, "label": "S2"},
    ]
    listed = _compose_glossary("术语表里有哪些？", many)
    assert listed.startswith("术语表里先有这几条。")
    assert "「水印」锁定不译" in listed
    assert "「组会」还没定译" in listed
    empty = _compose_glossary("术语怎么译？", type("S", (), {"items": []})(), [{"tool": "get_lab_glossary", "result_summary": "还没有术语表。"}])
    assert empty == "还没有术语表。"
    meets = type("S", (), {})()
    meets.items = [
        {
            "id": "meet:1",
            "kind": "meeting",
            "title": "周三组会",
            "held_at": "2026-09-24 15:00",
            "has_minutes": False,
            "actions": "读水印",
            "label": "S1",
        }
    ]
    said = _compose_meetings(meets)
    assert said.startswith("最近的组会是这几次。")
    assert "周三组会（2026-09-24 15:00，还没有纪要，行动：读水印）" in said
    chips = _followups("最近有组会吗？", [], [], [{"kind": "meeting", "id": "meet:1", "title": "周三组会"}])
    assert any("定了" in c for c in chips)


def test_page_followup_is_not_asked_about_people():
    chips = _followups("这个组导师是谁？", [], ["名册里还没有人被标成导师。"], [{"kind": "member", "role": "teacher", "title": "吴雪", "id": "m1"}])
    assert "这段依据在哪一页？" not in chips
    assert any("方向" in c for c in chips)


def test_teacher_saves_voice_and_intro_student_cannot(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "人设组", "code": "standing1", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    saved = client.put(
        f"/api/v1/labs/{lab_id}/assistant/standing",
        json={"voice": "称呼同学时用名字。", "intro": "本组导师对外称吴雪。"},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["voice"] == "称呼同学时用名字。"
    home = client.get(f"/api/v1/labs/{lab_id}/assistant/home", headers=h)
    assert home.status_code == 200, home.text
    body = home.json()
    assert body["my_role"] == "teacher"
    assert body["agent_voice"] == "称呼同学时用名字。"
    assert "吴雪" in body["agent_intro"]
    assert any(t.get("display_name") for t in body["teachers"])
    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        json={"email": "standing-stu@example.com", "role": "student"},
        headers=h,
    )
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": "standing-stu@example.com",
            "password": "studentpass1",
            "display_name": "学生甲",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    denied = client.put(
        f"/api/v1/labs/{lab_id}/assistant/standing",
        json={"voice": "学生改的", "intro": "不该写上"},
        headers={"Authorization": f"Bearer {reg.json()['access']}"},
    )
    assert denied.status_code == 403, denied.text


def test_intro_question_quotes_what_the_lab_wrote(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "约定组", "code": "introzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    saved = client.put(
        f"/api/v1/labs/{lab_id}/assistant/standing",
        json={"intro": "对外称吴雪为导师。组会每周三。"},
        headers=h,
    )
    assert saved.status_code == 200, saved.text
    asked = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里怎么称呼？", "scope": "lab"},
        headers=h,
    )
    assert asked.status_code == 200, asked.text
    answer = asked.json().get("answer") or ""
    assert "组里写过" in answer
    assert "吴雪" in answer
    assert "The materials" not in answer


def test_count_question_is_chinese_from_the_library(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "篇数组", "code": "countzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    asked = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里有多少篇论文？", "scope": "lab"},
        headers=h,
    )
    assert asked.status_code == 200, asked.text
    answer = asked.json().get("answer") or ""
    assert "论文" in answer
    assert "The materials" not in answer
    assert "The library" not in answer


def test_catalog_and_work_questions_stay_chinese(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "目录组", "code": "catalogzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    catalog = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "组里有哪些文献？", "scope": "lab"},
        headers=h,
    )
    assert catalog.status_code == 200, catalog.text
    catalog_answer = catalog.json().get("answer") or ""
    assert "文献" in catalog_answer
    assert "The materials" not in catalog_answer
    work = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "谁在做？", "scope": "lab"},
        headers=h,
    )
    assert work.status_code == 200, work.text
    work_answer = work.json().get("answer") or ""
    assert "The materials" not in work_answer
    assert work_answer


def test_task_and_activity_questions_stay_chinese(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "动态组", "code": "activityzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    tasks = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "我的任务有哪些？", "scope": "lab"},
        headers=h,
    )
    assert tasks.status_code == 200, tasks.text
    task_answer = tasks.json().get("answer") or ""
    assert "任务" in task_answer
    assert "The materials" not in task_answer
    activity = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "最近做了什么？", "scope": "lab"},
        headers=h,
    )
    assert activity.status_code == 200, activity.text
    activity_answer = activity.json().get("answer") or ""
    assert "The materials" not in activity_answer
    assert activity_answer


def test_glossary_and_meeting_questions_stay_chinese(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "术语组", "code": "termzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    gloss = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "术语怎么译？", "scope": "lab"},
        headers=h,
    )
    assert gloss.status_code == 200, gloss.text
    gloss_answer = gloss.json().get("answer") or ""
    assert "术语" in gloss_answer
    assert "The materials" not in gloss_answer
    meeting = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "最近有组会吗？", "scope": "lab"},
        headers=h,
    )
    assert meeting.status_code == 200, meeting.text
    meeting_answer = meeting.json().get("answer") or ""
    assert "组会" in meeting_answer
    assert "The materials" not in meeting_answer


def test_advisor_question_is_chinese_from_the_roster(client):
    h = _h(_admin(client))
    lab = client.post("/api/v1/labs", json={"name": "名册组", "code": "rosterzh", "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    asked = client.post(
        f"/api/v1/labs/{lab_id}/ask",
        json={"q": "这个组导师是谁？", "scope": "lab"},
        headers=h,
    )
    assert asked.status_code == 200, asked.text
    answer = asked.json().get("answer") or ""
    assert "名册里" in answer
    assert "The materials" not in answer
    assert "导师" in answer


def test_field_followup_does_not_ask_who_is_reading():
    chips = _followups(
        "这个领域有什么好的文章",
        [],
        [],
        [{"kind": "literature", "id": "lit:1", "title": "PCMark-NAS: Lightweight Print-Camera Resilient Watermarking"}],
    )
    assert all("谁在读" not in c for c in chips)
    named = _followups(
        "这篇文献怎么样",
        [],
        [],
        [{"kind": "literature", "id": "lit:1", "title": "PCMark-NAS: Lightweight Print"}],
    )
    assert any(c.startswith("谁在读《PCMark-NAS") for c in named)
