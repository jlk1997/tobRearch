from typing import Annotated, Sequence
import json
import os

from loguru import logger
import asyncio

from ..utils.llm_provider import build_chat_model
from langgraph.graph import StateGraph, END, START
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_core.tools.structured import StructuredTool
from langchain_core.messages.utils import count_tokens_approximately, get_buffer_string
from langchain_core.messages import ToolMessage, HumanMessage, AIMessage
from langchain_tavily import TavilySearch

from file1agent.file_manager import FileManager

from ..state import State, track_node_call
from ..chatbot import chatbot_with_context_manager, react_pre_model_wrapper
from ..tools.reports import ReportWriterTool

# from ..tools.literature import PaperSearchTool
from ..tools.tool_utils import route_by_file_existence, BasicToolNode
from ..tools.paper_search_tool import (
    SearchArxivTool,
    SearchPubMedTool,
    ReadArxivPaperTool,
    ReadMedRxivPaperTool,
    SearchMedRxivTool,
    SearchSemanticTool,
    ReadSemanticPaperTool,
    SearchBioRxivTool,
    SearchIACRTool,
    DummySearchTool
)
from ..state import prepare_state
from ..utils.config import Config


# Initialize prompt variables
report_prompt_template = None
search_prompt_template = None


# Function to load prompt files based on domain configuration
def load_prompt_file(config, filename):
    """Load prompt file from the appropriate domain directory."""
    domain = getattr(config, "domain", "general")
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", domain, filename)
    logger.debug(f"Loading prompt file: {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def build_literature_review_subgraph(config: Config, file_manager: FileManager):
    """
    Build a subgraph for literature review using paperscraper to search for papers and RAG to generate a literature review report.
    """

    from ..utils.job_context import bind_job_save_path

    bind_job_save_path(getattr(config, "save_path", "") or "")

    # Load prompts based on domain configuration
    global report_prompt_template, search_prompt_template

    report_prompt_template = load_prompt_file(config, "literature_review_report.md")
    search_prompt_template = load_prompt_file(config, "literature_search.md")

    # 初始化语言模型
    search_llm = build_chat_model(config.llm.chat, enable_thinking=True)
    write_llm = build_chat_model(config.llm.chat, enable_thinking=True)
    search_tools = [
        SearchArxivTool(),
        ReadArxivPaperTool(),
        SearchPubMedTool(),
        SearchMedRxivTool(),
        ReadMedRxivPaperTool(),
        SearchSemanticTool(),
        ReadSemanticPaperTool(),
        SearchBioRxivTool(),
        SearchIACRTool(),
        TavilySearch(max_results=10),
    ]
    # Do not use search in e2e test to avoid cost
    if config.workflow.e2e_test:
        logger.warning("Skipping search in e2e test to avoid cost.")
        search_tools = [
            DummySearchTool()
        ]

    llm_react = create_react_agent(search_llm, search_tools, pre_model_hook=react_pre_model_wrapper(config.question, config))

    report_tools = [ReportWriterTool(config.save_path, file_name="literature_review.md")]
    llm_report_writer = write_llm.bind_tools(report_tools)

    # 使用项目中的chatbot接口
    literature_search_chatbot = chatbot_with_context_manager(
        config, llm_react, search_prompt_template, context_manage="vector_search", calling_subgraph="literature_reviewer"
    )
    write_report_chatbot = chatbot_with_context_manager(
        config,
        llm_report_writer,
        report_prompt_template,
        context_manage="token_cnt_large",
        calling_subgraph="literature_reviewer",
    )

    @track_node_call("literature_reviewer")
    def clear_literature_state(state: State):
        from ..utils.literature_bib import finalize_literature_review, valid_bib_titles, write_literature_evidence

        try:
            write_literature_evidence(config.save_path)
        except Exception as exc:
            logger.warning("literature_evidence write failed: {}", exc)
        bib_path, literature_review_str = finalize_literature_review(config.save_path, state)
        state["messages"] = []
        state["literature_report"] = literature_review_str
        try:
            titles = valid_bib_titles(bib_path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("literature_review.bib unreadable: {}", exc)
            titles = []
        if not titles:
            logger.warning("literature_review.bib has no valid titles; platform gate will refuse succeeded")
        if not literature_review_str:
            logger.warning("literature_review.md missing or empty at clear; platform gate will refuse succeeded")
        return state

    # 创建文献调研子图
    graph = StateGraph(State)

    from ..utils.literature_coverage import literature_search_should_stop

    route_by_tool_counter = lambda state: "REACHED_TOOL_LIMIT" if literature_search_should_stop(state, config) else "CONTINUE"
    route_by_report = route_by_file_existence(os.path.join(config.save_path, "workspace", "literature_review.md"))
    report_tool_node = BasicToolNode(report_tools, config)

    # 添加节点
    graph.add_node("literature_search_chatbot", literature_search_chatbot)
    graph.add_node("write_report_chatbot", write_report_chatbot)
    graph.add_node("report_tool_node", report_tool_node)
    graph.add_node("clear_literature_state", clear_literature_state)

    # 添加边
    graph.add_edge(START, "literature_search_chatbot")
    graph.add_conditional_edges(
        "literature_search_chatbot", route_by_tool_counter, {"CONTINUE": "literature_search_chatbot", "REACHED_TOOL_LIMIT": "write_report_chatbot"}
    )
    graph.add_edge("write_report_chatbot", "report_tool_node")
    graph.add_conditional_edges("report_tool_node", route_by_report, {"FILE_NOT_FOUND": "write_report_chatbot", "FILE_EXISTS": "clear_literature_state"})
    graph.add_edge("clear_literature_state", END)

    return graph.compile()


if __name__ == "__main__":
    init_state, config, save_path = prepare_state(
        "literature_search_test_20250813",
        "What are the latest advancements in medical AI agents that can analyze multi-format datasets, answer research questions, and produce experimental reports?",
        "",
    )

    graph = build_literature_review_subgraph(config)
    graph.invoke(init_state)
