"""助手笔记：摘要也能进库，方法卡带大白话。"""

from fengsuan.app.services.analysis import _normalize_card
from fengsuan.app.services.agent.registry import stub_plan
from fengsuan.app.services.llm import _stub_agent_answer


def test_plain_block_is_kept():
    card = _normalize_card(
        {
            "problem": "分类",
            "claims": [{"text": "更准", "pages": [2]}],
            "plain": {
                "one_line": {"text": "这篇是在教电脑认图", "pages": [1]},
                "terms": [{"word": "卷积", "plain": "一小块一小块地看"}],
                "start_pages": [1, 3],
            },
        },
        source="pdf",
    )
    assert card["plain"]["one_line"]["text"] == "这篇是在教电脑认图"
    assert card["plain"]["terms"][0]["word"] == "卷积"
    assert card["plain"]["start_pages"] == [1, 3]


def test_stub_plan_keeps_direction_tree_and_routes_frontier():
    assert stub_plan("onboarding", "这个组在做什么方向？")[0] == "get_direction_tree"
    assert stub_plan("lab", "外面的前沿论文和相关作者")[0] == "get_direction_frontier"
    assert stub_plan("lab", "用大白话介绍这个实验室")[0] == "get_lab_overview"
    assert stub_plan("lab", "张三在做什么")[0] == "get_member_work"


def test_stub_answer_reads_chinese_materials():
    text = "问题：组里做什么\n\n查到的材料：\n[S1] 图神经网络\n节点分类\n\n回答要求：用中文"
    answer = _stub_agent_answer(text)
    assert "[S1]" in answer
    assert "Looking at" not in answer
