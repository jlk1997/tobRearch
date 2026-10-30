"""C219 快卡、C220 中文检索词、C221 理解层不串味。"""

from fengsuan.app.services.agent.loop import _compose_ask_back
from fengsuan.app.services.agent.parse import (
    clarify_should_ask,
    turn_cache_key,
    understanding_note,
    validate_turn,
)
from fengsuan.app.services.analysis import (
    FAST_CARD_CHARS,
    FULL_COVERAGE,
    fast_card_batch,
    partial_running_card,
    remaining_card_batches,
)
from fengsuan.app.services.frontier import _relevant, topic_terms
from fengsuan.app.services.sweep import _overlap, _topic_tokens
from fengsuan.app.services.terms import derive_terms


def test_c219_fast_batch_is_front_plus_method_and_capped():
    pages = ["摘要" * 2000, "引言" * 2000, "别的" * 2000, "方法细节" * 2000, "实验" * 2000]
    structure = {"sections": [{"title": "Method", "page": 4}]}
    fast = fast_card_batch(pages, structure)
    assert fast
    assert sum(len(text) for _, text in fast) <= FAST_CARD_CHARS
    assert any(no == 4 for no, _ in fast)
    assert fast[0][0] == 1
    selected = [[(1, pages[0])], [(4, pages[3])], [(5, pages[4])]]
    rest = remaining_card_batches(selected, fast, pages)
    assert all(any(no != 1 for no, _ in batch) for batch in rest)


def test_c219_partial_card_marks_fast():
    card = partial_running_card(
        {"problem": {"text": "先看摘要", "pages": [1]}},
        source="pdf",
        warning="",
        done=1,
        total=3,
        fast=True,
        coverage="先读了摘要和方法开头，方法和实验细节还在补。",
    )
    assert card["fast"] is True
    assert card["status"] == "running"
    assert "还在写" in card["warning"]
    assert "先读了摘要" in card["coverage"]
    assert FULL_COVERAGE == "整篇读完。"


def test_c220_topic_en_beats_lexicon_and_bigrams_keep_chinese():
    assert "learned image compression" in topic_terms("图片神经压缩")
    assert derive_terms("水印", topic_en=["digital watermarking"]) == ["digital watermarking"]
    grams = _topic_tokens("图片神经压缩")
    assert "图片" in grams
    assert "神经" in grams
    paper = {"title": "面向图片神经压缩的端到端方法", "abstract": "讨论图片神经压缩"}
    assert _relevant(paper, ["图片神经压缩"]) is True
    assert _overlap("图片神经压缩", paper) > 0
    english = {"title": "A survey of unrelated plants", "abstract": "botany"}
    assert _relevant(english, ["图片神经压缩"]) is False


def test_c221_cache_key_splits_users_and_phrase_check_drops_invented():
    q = "水印方向有什么论文"
    a = turn_cache_key("user-a", "lab", q, [], {"topic": "水印"})
    b = turn_cache_key("user-b", "lab", q, [], {"topic": "水印"})
    c = turn_cache_key("user-a", "lab", q, [], {"person": {"name": "秦川"}, "topic": "水印"})
    assert a != b
    assert a != c
    out = validate_turn(
        q,
        [],
        {},
        {
            "task": "field_reading",
            "topic": {"zh": "水印", "en": ["watermark"]},
            "inferred": ["再看看近两年代表作", "张三丰还写过什么"],
            "summary": "要找水印方向的论文",
            "clarify": "按水印这个方向",
        },
    )
    assert out["topic_en"] == ["watermark"]
    assert out["inferred"] == ["再看看近两年代表作"]
    assert "水印" in out["summary"]
    assert understanding_note(out) == "我按水印这个方向理解的。"
    wide = {"clarify": "这个主题太宽", "person": {}}
    assert clarify_should_ask(wide) is True
    assert _compose_ask_back({"kind": "clarify", "question": "这个主题太宽", "options": []}, type("S", (), {"items": []})()) == "这个主题太宽？"
