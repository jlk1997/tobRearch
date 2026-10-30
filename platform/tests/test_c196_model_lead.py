"""C196–C200：模型输出能解析，理解不接受编造，判断必须带原文。"""

from fengsuan.app.services.agent.loop import _accept_written, _invented_person
from fengsuan.app.services.agent.parse import validate_turn
from fengsuan.app.services.agent.understand import guard_person, recipe_for
from fengsuan.app.services.llm import extract_json, stream_visible
from fengsuan.app.services.web.person_match import _apply_llm, accept_model_yes


def test_extract_json_ignores_think_fence_and_preamble():
    fenced = '<think>The user wants Qin.</think>\n```json\n{"person": "秦川", "affiliation": "上海理工大学"}\n```'
    assert extract_json(fenced)["person"] == "秦川"
    preamble = 'Let me fill in the JSON:\n{"intent": "person_works", "person": "秦川"}'
    assert extract_json(preamble)["intent"] == "person_works"
    assert extract_json("no json here") == {}


def test_stream_holds_an_open_think():
    raw = ""
    visible = ""
    shown = []
    for token in ["<think>", "The user wants ", "秦川", "\n", "</think>", "\n结论在这里"]:
        raw += token
        vis = stream_visible(raw)
        if len(vis) > len(visible):
            shown.append(vis[len(visible) :])
            visible = vis
    text = "".join(shown)
    assert "The user" not in text
    assert "结论在这里" in text
    assert "think" not in stream_visible("<think>还没写完").lower() or stream_visible("<think>还没写完") == ""


def test_validate_turn_drops_an_invented_name():
    out = validate_turn(
        "水印方向有什么论文",
        [],
        {},
        {
            "task": "field_reading",
            "person": {"name": "张三丰", "affiliation": "不存在的大学"},
            "topic": {"zh": "水印", "en": ["watermark"]},
            "plan": {"scholar": ["张三丰 水印", "watermark survey"]},
            "summary": "要找水印方向的论文",
        },
    )
    assert out["person_name"] == ""
    assert out["affiliation"] == ""
    assert out["topic_zh"] == "水印"
    assert "张三丰 水印" not in (out["plan"].get("scholar") or [])
    assert "watermark survey" in out["plan"]["scholar"]
    assert out["intent"] == "frontier"


def test_followup_name_can_come_from_the_cited_hit():
    memory = {"hits": [{"n": 3, "person": "秦川", "affiliation": "上海理工大学", "title": "秦川导师介绍"}]}
    out = validate_turn("是S3 你上面查询的这个", [], memory, {"task": "person_works", "refers_to": "S3", "person": {}})
    assert out["person_name"] == "秦川"
    assert out["affiliation"] == "上海理工大学"


def test_whole_sentence_is_not_a_person_name(monkeypatch):
    assert guard_person("上海理工大学 秦川 学术成果有什么") == ""
    assert guard_person("秦川") == "秦川"

    def _bad(*_args, **_kwargs):
        return {
            "intent": "web_person",
            "person": "上海理工大学 秦川 学术成果有什么",
            "affiliation": "上海理工大学",
            "member_hint": False,
            "topic": "",
            "topic_en": [],
            "name_en": [],
            "wants_people": False,
            "wants_papers": True,
            "years": None,
            "papers": [],
            "ordinals": [],
            "memory_person": {},
            "pronoun": False,
            "sweep_id": "",
            "broad": False,
        }

    monkeypatch.setattr("fengsuan.app.services.agent.understand.understand", _bad)
    assert recipe_for("上海理工大学 秦川 学术成果有什么") == []


def test_model_plan_names_the_person():
    turn = {
        "from_model": True,
        "intent": "web_person",
        "person_name": "秦川",
        "affiliation": "上海理工大学",
        "name_en": ["Chuan Qin"],
        "plan": {"scholar": ["Chuan Qin University of Shanghai"]},
        "wants": ["papers"],
    }
    steps = recipe_for("随便一句", None, turn)
    assert steps[1][0] == "scholar_find_person"
    assert steps[1][1]["name"] == "秦川"
    assert steps[1][1]["affiliation"] == "上海理工大学"
    assert steps[1][1]["name_en"] == ["Chuan Qin"]


def test_model_yes_needs_a_span_from_the_candidate():
    row = {"title": "光电学院-秦川导师介绍", "excerpt": "秦川，上海理工大学", "_verdict": "unsure", "_reason": "名字对上了，单位没写", "_key": "p1"}
    forms = {"latin": ["Chuan Qin"]}
    assert accept_model_yes(row, {"evidence": "这句话不在页面里"}, "秦川", forms) is False
    assert accept_model_yes(row, {"evidence": "秦川，上海理工大学"}, "秦川", forms) is True
    _apply_llm([row], {"p1": {"verdict": "yes", "evidence": "不在原文", "reason": "像"}}, "秦川", forms)
    assert row["_verdict"] == "unsure"
    _apply_llm([row], {"p1": {"verdict": "yes", "evidence": "秦川，上海理工大学", "reason": "主页写了单位"}}, "秦川", forms)
    assert row["_verdict"] == "yes"
    blocked = {"title": "秦川平", "excerpt": "秦川平", "_verdict": "no", "_reason": "名字对不上", "_key": "p2"}
    _apply_llm([blocked], {"p2": {"verdict": "yes", "evidence": "秦川平", "reason": "差不多"}}, "秦川", forms)
    assert blocked["_verdict"] == "no"


def test_roster_page_is_not_his_research_topic():
    from fengsuan.app.services.agent.loop import _compose_person
    from fengsuan.app.services.web.person_match import is_roster

    assert is_roster("光电学院2026年博士研究生招生考核名单公示") is True
    sources = type("S", (), {})()
    sources.items = [
        {
            "kind": "identity",
            "title": "江李铠",
            "label": "S1",
            "identity": {
                "affiliations": ["上海理工大学"],
                "bio": "江李铠的名字出现在学校这份名单里：光电学院2026年博士研究生招生考核名单公示",
                "topics": ["光电学院2026年博士研究生招生考核名单公示"],
                "confirmed": False,
            },
        }
    ]
    text = _compose_person("江李铠 上海理工大学", sources, [])
    assert "名单里" in text
    assert "主要做" not in text


def test_author_homepage_is_not_presented_as_a_paper():
    from fengsuan.app.services.agent.loop import _compose_person
    from fengsuan.app.services.web.person_match import looks_like_profile

    row = {
        "title": "江李铠-上海理工大学",
        "url": "https://wap.cnki.net/author",
        "excerpt": "江李铠 上海理工大学 H指数 1 被引次数 13",
    }
    assert looks_like_profile(row, "江李铠") is True
    sources = type("S", (), {})()
    sources.items = [
        {
            "kind": "identity",
            "title": "江李铠",
            "label": "S1",
            "identity": {
                "affiliations": ["上海理工大学"],
                "bio": "江李铠，英文名 Likai Jiang，上海理工大学人工智能在读博士。",
                "confirmed": False,
            },
            "excerpt": "可能是这位。",
        },
        {
            "kind": "external",
            "title": "江李铠-上海理工大学",
            "url": "https://wap.cnki.net/author",
            "excerpt": "江李铠 上海理工大学 H指数 1 被引次数 13",
            "label": "S2",
            "matched_person": True,
        },
        {
            "kind": "web",
            "title": "江李铠｜上海理工大学在读博士，蚁算智能",
            "url": "https://genvidtrade.cn/a",
            "site": "genvidtrade.cn",
            "excerpt": "江李铠，英文名 Likai Jiang，上海理工大学人工智能在读博士。公司是蚁算智能。",
            "label": "S3",
        },
        {
            "kind": "scholar",
            "title": "Aggregation-induced emission",
            "authors": ["Other Person"],
            "label": "S4",
            "excerpt": "cadmium",
        },
    ]
    text = _compose_person("江李铠 上海理工大学", sources, [])
    assert "在读博士" in text
    assert "《江李铠-上海理工大学》" not in text
    assert "Aggregation-induced emission" not in text
    assert sources.items[3].get("drop_reason") == "名字对不上"
    assert "还不是这个组的成员" in text


def test_checked_english_paper_counts_as_this_person():
    from fengsuan.app.services.agent.loop import _compose_person

    sources = type("S", (), {})()
    sources.items = [
        {
            "kind": "identity",
            "title": "秦川",
            "label": "S1",
            "identity": {"affiliations": ["上海理工大学"], "topics": ["研究方向：信息隐藏"], "confirmed": False, "alternatives": ["Chuan Qin"]},
            "excerpt": "可能是这位。上海理工大学。研究方向：信息隐藏",
        },
        {
            "kind": "external",
            "title": "Constructive Data Hiding",
            "label": "S2",
            "authors": ["Chuan Qin"],
            "year": 2024,
            "matched_person": True,
            "excerpt": "a data hiding method",
            "external": True,
        },
    ]
    text = _compose_person("上海理工大学 秦川 学术成果有什么", sources, [])
    assert "主要做研究方向：信息隐藏" in text
    assert "Constructive Data Hiding" in text
    assert "先别当成他的" not in text
    assert "还不是这个组的成员" in text


def test_covered_search_does_not_run_again():
    from fengsuan.app.services.agent.understand import followups

    nxt = followups(
        "scholar_find_person",
        {"covered": True, "picked": {"name": "秦川", "openalex_id": "A1"}, "name": "秦川", "affiliation": "上海理工大学"},
    )
    assert nxt == [("scholar_person_profile", {"name": "秦川", "openalex_id": "A1"})]
    missed = followups("scholar_find_person", {"covered": True, "miss": True, "name": "秦川", "affiliation": "上海理工大学"})
    assert missed == []


def test_written_answer_can_cite_part_of_the_note():
    note = "可能是秦川，还不是这个组的成员。公开记录对上了 [S1]。论文在 [S2]。"
    sources = type("S", (), {})()
    sources.items = [
        {"label": "S1", "title": "秦川导师介绍", "excerpt": "秦川，上海理工大学"},
        {"label": "S2", "title": "Constructive Data Hiding", "excerpt": "data hiding"},
    ]
    short = "秦川在上海理工大学做信息隐藏，还不是这个组的成员。主页对上了 [S1]。"
    assert _accept_written(note, short, sources, note + "秦川") is True


def test_written_answer_falls_back_when_it_invents_a_person():
    note = "可能是秦川，还不是这个组的成员。公开记录对上了 [S1]。"
    sources = type("S", (), {})()
    sources.items = [{"label": "S1", "title": "秦川导师介绍", "excerpt": "秦川，上海理工大学"}]
    good = "秦川还不是这个组的成员。学校主页对上了这位老师 [S1]。"
    assert _invented_person(good, note + "秦川") is False
    assert _accept_written(note, good, sources, note + "秦川导师介绍") is True
    invented = "张三老师还不是这个组的成员。公开记录对上了 [S1]。"
    assert _accept_written(note, invented, sources, note + "秦川") is False
    missing = "秦川还不是这个组的成员。另一份记录对上了 [S9]。公开记录对上了 [S1]。"
    assert _accept_written(note, missing, sources, note + "秦川") is False
