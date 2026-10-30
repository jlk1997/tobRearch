import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const src = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

test("c1 reader analyze shows running and keeps said", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /正在生成速读/);
  assert.match(ui, /import \{[^}]*methodItems[^}]*\} from "\.\/methodCard\.js"/);
  const toolbar = src("./pages/reader/ReaderToolbar.jsx");
  assert.match(toolbar, /正在提炼/);
  assert.match(toolbar, /status === "running"/);
  assert.match(toolbar, /result\?\.status === "running"/);
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /status === "running"/);
});

test("c2 no body uses one next step and hides empty sections tab", () => {
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /请先上传可复制 PDF/);
  assert.match(reader, /rawPanel === "sections" \? "card"/);
  assert.match(reader, /看中文、选区、辅导和提炼方法/);
  const tutor = src("./pages/reader/TutorPanel.jsx");
  const uiAsk = src("./ui.jsx");
  assert.match(uiAsk, /这篇页上没取到，可换一句或先提炼方法/);
  assert.match(tutor, /AssistantChat/);
  const pane = src("./pages/reader/PdfPane.jsx");
  assert.match(pane, /canTranslate === false/);
});

test("c3 cockpit shows page pulse and profile", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /last_page_no/);
  assert.match(cockpit, /已写方法/);
  assert.match(cockpit, /本周开读/);
  assert.match(cockpit, /profile\?\.bio/);
  assert.match(cockpit, /还有 \{data\.reading_now_more\} 篇在文献库/);
  assert.match(cockpit, /events_scope|最近动态/);
});

test("c4 gap writes the 200-paper window", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /scan_window/);
  assert.match(ui, /只看最近/);
});

test("c21-c25 abstract label note titles method hints first author search", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /仅摘要/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /仅摘要/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_note_titles/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /tab=search/);
  assert.match(graph, /first_author/);
});

test("c16-c20 finish toast reading titles sample href first author", () => {
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /已写出这篇的方法/);
  const ui = src("./ui.jsx");
  assert.match(ui, /出处没核到/);
  assert.match(ui, /x\.reason/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_reading_titles/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /sample_href/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /first_author/);
});

test("c11-c15 list running week titles hung corpus author papers", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /kind === "running"/);
  assert.match(ui, /export function WeekPaperChips/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /正在提炼/);
  const doc = src("./pages/reader/useReaderDoc.js");
  assert.match(doc, /tutor_history: prev\.tutor_history/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_card_titles/);
  assert.match(cockpit, /WeekPaperChips/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /selected\.papers/);
});

test("c6-c10 running poll hang chips and author why", () => {
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /include: "core"/);
  const doc = src("./pages/reader/useReaderDoc.js");
  assert.match(doc, /is_running/);
  assert.match(doc, /analysis_status/);
  const zh = src("./pages/reader/usePageTranslation.js");
  assert.match(zh, /翻译超时/);
  const ui = src("./ui.jsx");
  assert.match(ui, /s\.matched/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_tutor/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /query_fallback/);
  assert.match(lit, /dataIndex: "why"/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /n\.label/);
});

test("c96-c100 digest never members hang tools", () => {
  const digest = src("./pages/Digest.jsx");
  assert.match(digest, /weekNeverAi/);
  assert.match(digest, /本周没问过/);
  assert.match(digest, /aiToolsLine/);
  const collab = src("./pages/Collab.jsx");
  assert.match(collab, /week_never_ai/);
  assert.match(collab, /本周没问过/);
  const members = src("./pages/Members.jsx");
  assert.match(members, /last_ai/);
  assert.match(members, /week_never_ai/);
  assert.match(members, /AgentTraceBox/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /direction_suggestions/);
  assert.match(cockpit, /DirectionHangChips/);
  assert.match(cockpit, /aiToolsLine/);
});

test("c111-c115 method card home last-ai reproduce", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function MethodCard/);
  assert.match(ui, /AiResultBar/);
  assert.match(ui, /复制速读/);
  assert.match(ui, /再生成一次/);
  const method = src("./methodCard.js");
  assert.match(method, /export function methodCardText/);
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /速读/);
  assert.match(reader, /这一栏这次没打开/);
  assert.match(src("./pages/reader/SectionsPanel.jsx"), /asNode/);
  assert.match(reader, /PdfPane/);
  const toolbar = src("./pages/reader/ReaderToolbar.jsx");
  assert.match(toolbar, /fs-analyze-run/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /last_ai/);
  assert.match(home, /week_never_ai/);
  assert.match(home, /接着上次问的/);
  assert.match(home, /本周还没问过/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /onRetry/);
  assert.match(runs, /api\.analyze/);
});

test("c106-c110 write lang compare review meeting", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function AiResultBar/);
  assert.match(ui, /你可以接着做/);
  const writing = src("./pages/Writing.jsx");
  assert.match(writing, /AiResultBar/);
  assert.match(writing, /再跑大纲|再写本章/);
  const lang = src("./pages/Language.jsx");
  assert.match(lang, /AiResultBar/);
  assert.match(lang, /再跑上一句/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /AiResultBar/);
  assert.match(lit, /再对比所选/);
  const ms = src("./pages/Manuscripts.jsx");
  assert.match(ms, /AiResultBar/);
  assert.match(ms, /再跑一轮/);
  const collab = src("./pages/Collab.jsx");
  assert.match(collab, /AiResultBar/);
  assert.match(collab, /再生成纪要/);
});

test("c101-c105 ask thread follow copy tools", () => {
  const kb = src("./pages/KB.jsx");
  assert.match(kb, /askTurns/);
  assert.match(kb, /history/);
  assert.match(kb, /新开一轮/);
  assert.match(kb, /复制回答/);
  assert.match(kb, /你可以接着问/);
  assert.match(kb, /AskToolSteps/);
  assert.match(kb, /AgentTraceBox/);
  const tutor = src("./pages/reader/TutorPanel.jsx");
  assert.match(tutor, /AssistantChat/);
  assert.match(tutor, /写进笔记/);
  assert.match(tutor, /这篇在解决什么？/);
  const ui = src("./ui.jsx");
  assert.match(ui, /export function AskToolSteps/);
  assert.match(ui, /你可以接着问|hint/);
});

test("c91-c95 stats leftover home hang never-asked", () => {
  const collab = src("./pages/Collab.jsx");
  assert.match(collab, /week_empty_ai/);
  assert.match(collab, /week_degraded_ai/);
  assert.match(collab, /last_ai/);
  assert.match(collab, /本周没查到材料/);
  assert.match(collab, /本周这次降级了/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /data\?\.keyword_hints/);
  assert.match(lit, /leftoverHintBar/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /direction_suggestions/);
  assert.match(home, /DirectionHangChips/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_never_ai/);
  assert.match(cockpit, /本周没问过/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /week_never_ai/);
  assert.match(dirs, /本周没问过/);
});

test("c86-c90 degraded hints graph search", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_degraded_ai/);
  assert.match(cockpit, /本周这次降级了/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /week_degraded_ai/);
  assert.match(dirs, /本周这次降级了/);
  const digest = src("./pages/Digest.jsx");
  assert.match(digest, /week_degraded_ai/);
  assert.match(digest, /本周这次降级了/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /keyword_hints/);
  assert.match(graph, /点一下写进当前方向/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /searchHints/);
  assert.match(lit, /leftoverHintBar/);
  assert.match(lit, /点一下写进当前方向/);
});

test("c81-c85 dir empty-ai graph hang search cited rec hints", () => {
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /week_empty_ai/);
  assert.match(dirs, /本周没查到材料/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /direction_suggestions/);
  assert.match(graph, /DirectionHangChips/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /cited_in_lab/);
  assert.match(lit, /组内这篇的方法写过/);
  assert.match(lit, /keyword_hints/);
  assert.match(lit, /点一下写进当前方向/);
  const digest = src("./pages/Digest.jsx");
  assert.match(digest, /week_empty_ai/);
  assert.match(digest, /本周没查到材料/);
});

test("c76-c80 member last-ai cited hang digest empty-ai", () => {
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /last_ai/);
  assert.match(dirs, /最近/);
  assert.match(dirs, /AgentTraceBox/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /cited_in_lab/);
  assert.match(lit, /组内这篇的方法写过/);
  assert.match(lit, /already_in_library && row\.direction_suggestions/);
  assert.match(lit, /DirectionHangChips/);
  const digest = src("./pages/Digest.jsx");
  assert.match(digest, /last_ai/);
  assert.match(digest, /原话，不是模型改写/);
  assert.match(digest, /AgentTraceBox/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_empty_ai/);
  assert.match(cockpit, /本周没查到材料/);
});

test("c71-c75 last-ai edge titles leftover hints related hang", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /last_ai/);
  assert.match(cockpit, /ground_why/);
  assert.match(cockpit, /最近/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /target_title/);
  assert.match(graph, /source_title/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /keyword_hints/);
  assert.match(dirs, /对不上的篇里反复出现/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /labRelated/);
  assert.match(lit, /direction_suggestions/);
  assert.match(lit, /DirectionHangChips/);
});

test("c61-c70 cache hang tools via unmatched recent-ai", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /这次用的是上次检索/);
  assert.match(lit, /这次是新检索/);
  assert.match(lit, /citation_count/);
  assert.match(lit, /dataIndex: "journal"/);
  assert.match(lit, /外面来源有/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /可能属于这里/);
  assert.match(dirs, /DirectionHangChips/);
  assert.match(dirs, /题名对不上现有方向/);
  assert.match(dirs, /这个方向最近的问 \/ 辅导/);
  assert.match(dirs, /重新检索外面的论文/);
  assert.match(dirs, /AgentTraceBox/);
  const ui = src("./ui.jsx");
  assert.match(ui, /这次还能用/);
  assert.match(ui, /availableTools/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /这篇的方法里的书目/);
  assert.match(graph, /VIA\[e\.via\]/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /toolLabel/);
  assert.match(cockpit, /用过：/);
});

test("c56-c60 profile unread-repro ask grounding fail", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /expected_graduate_at/);
  assert.match(cockpit, /profile\.skills/);
  assert.match(cockpit, /\/labs\/\$\{id\}\/members/);
  assert.match(cockpit, /unread_reproduce_titles/);
  assert.match(cockpit, /在读未复现/);
  assert.match(cockpit, /week_fail_titles/);
  assert.match(cockpit, /炼失败/);
  const ui = src("./ui.jsx");
  assert.match(ui, /ground_weak/);
  assert.match(ui, /degraded/);
  const err = src("./err.js");
  assert.match(err, /ask\.lab/);
});

test("c51-c55 mentor ai traces direction recommend", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /ai_calls/);
  assert.match(cockpit, /recent_ai/);
  assert.match(cockpit, /AgentTraceBox/);
  assert.match(cockpit, /members_counts_note/);
  assert.match(cockpit, /direction_id: directionId/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /query_via/);
  assert.match(lit, /检索来自方向词/);
  const ui = src("./ui.jsx");
  assert.match(ui, /direction_name/);
  assert.match(ui, /row\.score/);
});

test("c46-c50 batch list kb analyze honesty", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /后台正在提炼/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /AnalyzeChip row=\{row\}/);
  const kb = src("./pages/KB.jsx");
  assert.match(kb, /AnalyzeChip/);
  assert.match(kb, /has_method_card/);
});

test("c41-c45 writing reproduce tutor upload analyze", () => {
  const writing = src("./pages/Writing.jsx");
  assert.match(writing, /AnalyzeChip/);
  assert.match(writing, /n\.item_id/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /AnalyzeChip/);
  const tutor = src("./pages/reader/TutorPanel.jsx");
  assert.match(tutor, /AssistantChat/);
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /panel: "card"/);
  assert.match(lit, /后台正在提炼/);
});

test("c36-c40 recommend claims why home compare analyze", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /AnalyzeChip/);
  const ms = src("./pages/Manuscripts.jsx");
  assert.match(ms, /AnalyzeChip/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /AnalyzeChip/);
  const ui = src("./ui.jsx");
  assert.match(ui, /p\.why/);
});

test("c31-c35 week written weak titles analyze chip", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function AnalyzeChip/);
  assert.match(ui, /去提炼/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /weak_card_titles/);
  assert.match(cockpit, /AnalyzeChip/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /AnalyzeChip/);
  assert.match(graph, /看这篇的方法/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /AnalyzeChip/);
});

test("c26-c30 honest card upload week ms draft graph", () => {
  const toolbar = src("./pages/reader/ReaderToolbar.jsx");
  assert.match(toolbar, /shouldAnalyzeAfterUpload/);
  assert.match(toolbar, /uploadPdf/);
  assert.match(toolbar, /runAnalyze/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /week_ms_titles/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /card_empty/);
  assert.match(graph, /has_method_card/);
  const ui = src("./ui.jsx");
  assert.match(ui, /draft\.keywords/);
  assert.match(ui, /这篇的方法/);
});

test("c5 recommend authors and graph author nodes", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /recAuthors/);
  assert.match(lit, /该方向相关作者/);
  assert.match(lit, /检索入库/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /type === "author"/);
  assert.match(graph, /wrote/);
  assert.match(graph, /检索该作者/);
});

test("c116-c120 survey partial card notice kb tutor", () => {
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /review_md/);
  assert.match(runs, /复制综述/);
  assert.match(runs, /再跑一次/);
  assert.match(runs, /问这篇综述/);
  const ui = src("./ui.jsx");
  assert.match(ui, /已写出一部分/);
  assert.match(ui, /还在写/);
  assert.match(ui, /这版还是原文条目/);
  assert.match(ui, /正在换成中文速读/);
  const shell = src("./shell.jsx");
  assert.match(shell, /n\.followups/);
  const collab = src("./pages/Collab.jsx");
  assert.match(collab, /row\.followups/);
  const kb = src("./pages/KB.jsx");
  assert.match(kb, /askSessions/);
  assert.match(kb, /kbMeta/);
  assert.match(kb, /以前问过/);
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /is-tutor-main/);
  assert.match(reader, /收起 PDF/);
  assert.match(reader, /hasChineseLead/);
  assert.match(reader, /askCardLine/);
  assert.match(src("./index.css"), /\.fs-object\.is-tutor \.fs-object-side/);
  assert.match(src("./assistant/AssistantChat.jsx"), /onOpenSource/);
  assert.match(src("./pages/reader/TutorPanel.jsx"), /onOpenSource=\{onOpenSource\}/);
  assert.match(src("./pages/reader/NotesPanel.jsx"), /改文字/);
  assert.match(src("./pages/reader/NotesPanel.jsx"), /记在这一页/);
  assert.match(ui, /先问这篇/);
});

test("c121-c125 continue ask history and survey", () => {
  const kb = src("./pages/KB.jsx");
  assert.match(kb, /askTurns\.slice\(-2\)/);
  assert.doesNotMatch(kb, /askScope === "kb" && askTurns/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /这篇综述还缺什么/);
  assert.match(runs, /复制回答/);
  assert.match(runs, /已写好的综述/);
  const ui = src("./ui.jsx");
  assert.match(ui, /export function ContinueAskChip/);
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /ContinueAskChip/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /ContinueAskChip/);
  const members = src("./pages/Members.jsx");
  assert.match(members, /ContinueAskChip/);
  const stats = src("./pages/Collab.jsx");
  assert.match(stats, /ContinueAskChip/);
});

test("c126-c130 continue from member digest survey home", () => {
  const cockpit = src("./pages/Cockpit.jsx");
  assert.match(cockpit, /data\.recent_ai/);
  assert.match(cockpit, /ContinueAskChip last=\{row\}/);
  const digest = src("./pages/Digest.jsx");
  assert.match(digest, /最近问过/);
  assert.match(digest, /ContinueAskChip last=\{row\}/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /get\("ask"\)/);
  assert.match(runs, /kbMeta/);
  assert.match(runs, /已写好的综述/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /n\.followups/);
});

test("c131-c135 ask week survey writing language", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function AskWeekChip/);
  assert.match(ui, /tab=ask/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /kb\?tab=ask/);
  const runs = src("./pages/Runs.jsx");
  assert.match(runs, /steps=\{t\.steps\}/);
  assert.match(runs, /traceId=\{t\.trace_id\}/);
  const writing = src("./pages/Writing.jsx");
  assert.match(writing, /writingLatest/);
  assert.match(writing, /上次大纲较长/);
  const lang = src("./pages/Language.jsx");
  assert.match(lang, /languageLatest/);
  assert.match(lang, /上次改过的文字较长/);
});

test("c136-c140 compare section review minutes", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /compareLatest/);
  assert.match(lit, /上次对比较长/);
  const writing = src("./pages/Writing.jsx");
  assert.match(writing, /writingSectionLatest/);
  assert.match(writing, /上次章节较长/);
  const ms = src("./pages/Manuscripts.jsx");
  assert.match(ms, /reviewLatest/);
  assert.match(ms, /上次评审提示较长/);
  const collab = src("./pages/Collab.jsx");
  assert.match(collab, /open\?\.my_trace_id/);
  assert.match(collab, /traceId=\{open\.my_trace_id\}/);
});

test("assistant desk is a conversation not a direction wall", () => {
  const page = src("./pages/Assistant.jsx");
  assert.match(page, /问问这个组/);
  assert.match(page, /fs-ask-scope/);
  assert.match(page, /session: row\.session_id/);
  assert.doesNotMatch(page, /改个中文名/);
  assert.doesNotMatch(page, /fs-ask-dirs/);
  const chat = src("./assistant/AssistantChat.jsx");
  assert.match(chat, /aria-label=\{busy \? "停止" : "发送"\}/);
  assert.match(chat, /正在查：/);
  assert.match(chat, /fs-ask-tools-hover/);
  assert.match(chat, /last && turn\.followups/);
  assert.match(chat, /回到最新/);
  assert.match(chat, /已停止/);
  assert.match(chat, /replace: true/);
  assert.match(page, /还没有问过/);
  assert.match(page, /fs-ask-past-time/);
  assert.match(page, /昨天/);
  assert.match(page, /fs-ask-past-a/);
  assert.match(page, /谁在做什么？/);
  assert.match(page, /组里有哪些文献？/);
  assert.match(page, /最近做了什么？/);
  assert.match(page, /组里一览/);
  assert.match(page, /看全部/);
  assert.match(chat, /锁定不译/);
  assert.match(chat, /task_status/);
  assert.match(chat, /my_reading/);
  assert.match(page, /这个助手/);
  assert.match(page, /让助手认识这个组/);
  assert.match(page, /去填写/);
  assert.match(page, /对外怎么称呼/);
  assert.match(page, /回答前要知道的前提/);
  assert.match(page, /名册上谁是导师/);
  assert.match(page, /对学生直呼名字/);
  assert.match(page, /到成员页改角色/);
  assert.match(page, /my_role === "teacher"/);
  assert.match(chat, /kindLabel/);
  assert.match(chat, /row.kind === "member"/);
  assert.match(chat, /fs-ask-people/);
  assert.match(src("./ui.jsx"), /c\.role === "teacher" \? "导师"/);
  assert.match(src("./pages/Literature.jsx"), /ReadTag status=\{row\.my_reading\}/);
  assert.match(chat, /发送/);
  const shell = src("./shell.jsx");
  assert.match(shell, /fs-ask-welcome is-compact/);
  assert.match(shell, /问这个组在做什么。/);
  assert.match(shell, /谁在做什么？/);
  assert.match(shell, /组里有哪些文献？/);
  assert.match(shell, /最近做了什么？/);
  assert.match(shell, /组里/);
  assert.match(shell, /去填写组里要记住的/);
  assert.match(src("./pages/Literature.jsx"), /组里在读/);
  assert.match(src("./pages/Literature.jsx"), /aria-label="文献范围"/);
  assert.match(src("./pages/LiteratureReader.jsx"), /if \(!doc\)/);
  assert.match(src("./pages/reader/SelectionPopover.jsx"), /问这句/);
  assert.match(src("./pages/reader/SelectionPopover.jsx"), /记笔记/);
  assert.match(src("./pages/Literature.jsx"), /我在读/);
  assert.match(src("./pages/LabGlance.jsx"), /aria-label="组里"/);
  assert.match(src("./pages/LabGlance.jsx"), /paper\.href/);
  assert.match(src("./pages/LabGlance.jsx"), /还没放进这个方向/);
  assert.match(src("./pages/LabGlance.jsx"), /第 \{paper\.last_page_no\} 页/);
  assert.match(src("./pages/LabGlance.jsx"), /LabReaderBits/);
  assert.match(src("./pages/Literature.jsx"), /LabReaderBits/);
  assert.match(src("./pages/LiteratureReader.jsx"), /LabReaderBits/);
  assert.match(src("./pages/LiteratureReader.jsx"), /onPage=\{setPage\}/);
  assert.match(src("./pages/Directions.jsx"), /LabReaderBits/);
  assert.match(src("./pages/reader/ReaderToolbar.jsx"), /next\?\.key/);
  assert.match(src("./pages/Directions.jsx"), /组里在读/);
  assert.match(src("./pages/Cockpit.jsx"), /defaultActiveKey=\{\["nums"\]\}/);
  assert.match(src("./App.jsx"), /CockpitTabRedirect/);
  assert.match(page, /组里要记住的 \{introLen\}\/4000/);
  assert.match(shell, /最近问过/);
  assert.match(shell, /ask_sessions/);
  assert.match(src("./assistant/AssistantChat.jsx"), /确认收进/);
  assert.match(src("./assistant/AssistantChat.jsx"), /把留下的收进组里/);
  assert.match(src("./assistant/AssistantChat.jsx"), /这次没写上/);
  assert.match(src("./assistant/AssistantChat.jsx"), /还不是这个组的成员/);
  assert.match(src("./assistant/AssistantChat.jsx"), /确认收进这批/);
  assert.match(src("./assistant/AssistantChat.jsx"), /还没放进这个组/);
  assert.match(src("./assistant/AssistantChat.jsx"), /没有标来源/);
  assert.match(src("./assistant/AssistantChat.jsx"), /先别当成已经查实的/);
  assert.doesNotMatch(src("./assistant/AssistantChat.jsx"), /有几句没在这一页找到出处/);
  assert.match(src("./assistant/AssistantChat.jsx"), /还有 \{rest\} 条/);
  assert.match(src("./assistant/AssistantChat.jsx"), /查过但没采用/);
  assert.match(src("./assistant/AssistantChat.jsx"), /asksRoster/);
  assert.match(src("./assistant/AssistantChat.jsx"), /canIngest/);
  assert.match(src("./assistant/AssistantChat.jsx"), /openalex_id/);
  assert.match(src("./assistant/AssistantChat.jsx"), /pdf_url/);
  assert.match(src("./assistant/AssistantChat.jsx"), /只有题录/);
  assert.match(src("./assistant/AssistantChat.jsx"), /组里已有/);
  assert.match(src("./assistant/AssistantChat.jsx"), /检索说明/);
  assert.match(src("./assistant/AssistantChat.jsx"), /是这位，写进档案/);
  assert.match(src("./assistant/AssistantChat.jsx"), /不是这位/);
  assert.match(src("./assistant/useAssistant.js"), /confirm_tools/);
  assert.match(src("./pages/Members.jsx"), /给助手看的/);
  assert.match(src("./pages/Members.jsx"), /让助手帮我填/);
  assert.match(src("./pages/Members.jsx"), /正在读留给助手的网站/);
  assert.match(src("./pages/Members.jsx"), /重新读取/);
  assert.match(src("./pages/Members.jsx"), /没读成/);
  assert.match(src("./pages/Cockpit.jsx"), /公开学术身份/);
});
