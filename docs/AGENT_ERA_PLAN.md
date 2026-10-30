# 蜂算科研：Agent 纪元施工合同（A 系列）

> 给 coding agent 与人类共同维护。产品名 **蜂算科研**。OpenLens 只作为引擎。
>
> **本文件是 G347 之后的唯一施工合同。** 不重写 `docs/PLATFORM_UPGRADE_PLAN.md`、`docs/WORLDCLASS_ROADMAP.md`、`docs/FRONTIER_MATURITY_PLAN.md`。
>
> 原则仍是：**只升级现有对象。禁止平行实现。** 新需求必须先回答「改哪个现有 endpoint / 表 / 页面」。
>
> 与前三份合同的区别只有一条：**本纪元承认「Agent 运行时」是缺失的底座，不是新产品面。** 运行时是内部层，UI 仍挂现有 IA。

***

## 0. 为什么要开这一纪元（事实对齐）

G 系列把指挥面做诚实了，但两处底座仍然缺失，靠加深现有对象无法补：

1. **平台侧没有 Agent。** `platform/fengsuan/app/services/llm.py` 只有 `chat()` / `iter_chat_deltas()`，是单次 prompt→文本。全仓 `platform/` 内没有任何 tool-calling。方法卡、辅导、对比、大纲、章节、KB 问答、组会纪要、模拟评审、周报、推荐、语言共十余个「AI 功能」都是 markdown 提示词 + 事后正则接地（`ground_against_corpus` / `assert_excerpt`）。模型只能「说」，不能「查、算、跑、改」。再怎么改提示词也不会变成 Agent。
2. **阅读卡顿是服务端全量重解析，不是前端慢。** `item_page_excerpt` → `extract_pdf_path` → `read_bytes()` → GROBID（25s 超时）→ `_pypdf_pages`（解析全部页），只为返回单页 4000 字，且**没有任何缓存**。翻一页解析一次，问一句再解析一次。

这两条不解决，「世界级前沿 Agent」只是文案。

***

## 1. 产品负责人授权的三项解禁（其余禁令全部继承）

| 编号 | 授权内容 | 边界 |
| --- | --- | --- |
| **A-AGENT** | 允许在 `platform/fengsuan/app/services/agent/` 新增 Agent 运行时：工具注册表、多轮 tool-calling 循环、编排、追踪、预算与降级。现有 AI 功能逐个切到运行时。 | `chat()` 保留为单轮兜底，不删除。接口仍在 `/api/v1`，只加字段与子路径，禁止 `/v2`。**禁止新增侧栏分组或聊天页**：运行时只在现有三个入口露出（工作台入门卡 / 知识库 `?tab=ask` / 阅读器 `?panel=tutor`）。 |
| **A-PROFILE** | 允许新增成员档案与实验室入门语料两类数据。 | 档案走 `lab_memberships` **加列**，禁止建 `member_profiles` 影子表。入门语料走现有 `kb_documents` 加 `source_kind`，禁止第二套 KB。档案渲染在现有成员页与进度成员卡，**禁止独立档案页**。权限仍 `teacher` \| `student` 两档。 |
| **A-ENGINE** | 允许改 `openlens_ai/build_graph.py` 的边语义：恢复 `ALTER_PLAN` 反馈边、引入 LangGraph checkpointer、`literature_reviewer` 与 `data_analyzer` 并行。 | `D-HONEST` 的**事实判定一个不改**：无 GPU 不伪造指标、扫描件 `unusable`、无实测不得 `succeeded`、缺 `plan.md` / 综述不得 `succeeded`、未点名须写「未使用：标题」。禁止把 `paper_reproducer` 并进主图当成「已并图」，只允许统一编排抽象。 |

### 继承的禁令（不得以「Agent 需要」为由突破）

Sci-Hub、付费墙绕过、官方查重、排行红灯、暗色主题、键鼠监控、第三角色、`/pi` `/atlas` `/degree`、第三条「AI 写论文」、把 Streamlit 当主 UI、`D-KEEPAPI`、`D-KEEPTABLE`（除 A-PROFILE 明列的加列与 `agent_traces` 审计表）、`D-KEEPUI`。

**新增一条 Agent 专属禁令 —— A-NOBURN：** Agent 不得在未经用户明确确认的情况下发起重型作业（`research_run` / `reproduce_run`）、不得改手稿正文、不得改他人笔记。写入类工具一律先返回「待确认」再由用户点一次。

***

## 2. 阶段与验收

| 阶段 | 改哪里 | 禁止 | 验收要点 |
| --- | --- | --- | --- |
| **A0** | 本文件 + 两份旧合同加互指 + 性能基线脚本 | 重写 H1/H2/F/G 正文 | 四份文档互指；`scripts/perf_reader.py` 可复算翻页 / detail / PDF 首屏 |
| **A1** | 页文本落库：`literature_file_pages` + `item_page_excerpt` 读缓存 + worker 后台抽取 | 请求线程里解析全量 PDF | 第二次翻页不再解析 PDF；缓存未命中才解析并回写；`parse_status` 口径不变 |
| **A2** | PDF 传输：`ETag` / `Cache-Control` / 304；nginx 缓存与 Range | 把 PDF 读进内存再返回 | `If-None-Match` 命中返回 304；前端不再整份 blob 下载 |
| **A3** | `/detail` 瘦身：`?include=tutor`、消除 `note_dto` N+1、`latest_reproduce_runs` 加 limit、复合索引 | 改 DTO 字段名、删字段 | 默认响应不含 `tutor_history`；老客户端传 `include=tutor` 仍拿到 |
| **A4** | 阅读器前端拆分 + react-query 统一 | TS 全站重写、第二套阅读产品 | `LiteratureReader` 拆为 pane 级组件；翻页不重渲染侧栏 |
| **A5** | `services/agent/`：`loop` / `registry` / `trace` / `budget` | 新聊天页、提示词管理后台 | 工具在自身内部做权限校验；步数与预算可配；工具失败降级为单轮并标明 |
| **A6** | 首批工具（检索 / 事实 / 文档 / 写入 / 作业） | 重写已有 service 逻辑 | 每个工具都是现有 service 的薄封装；返回片段带 `source_id` |
| **A7** | `test_agent_eval.py` + golden 夹具 | 新评测台页面 | 工具选择正确率 / 引用可追溯率 / 拒答率 / 步数分布进 CI 阈值 |
| **A8** | 辅导切运行时；再扩 `scope=lab` 跨篇 | `/tutor` 页、跨文献记忆表 | `tutor_scope` 可为 `paper` \| `lab`；跨篇引用可点回对应文献对应页 |
| **A9** | 结构树持久化 + 章节图表导航 + 锚定高亮 + 自动预读 + 译文落库 | 新翻译表、第四侧栏、新分析页 | 高亮锚点含原文子串校验；GROBID 缺失时降级为页级，不假装有结构 |
| **A10** | 档案加列 + `source_kind` + `GET /labs/{id}/onboarding` + `scope=onboarding` | 独立档案页、新 onboarding 应用 | 入门卡在工作台；追问进现有知识库问答栏；结构化事实走工具不走向量 |
| **A11** | 去压力化呈现 | 删后端诚实字段、改 `D-HONEST` 判定 | 学生端下一步 ≤2 条；导师卡住清单默认折叠；列表与铃铛不再撒催办芯片 |
| **A12** | 其余 AI 功能切运行时 | 第三条写作 / 新 RAG / 官方查重 | 方法卡、对比、大纲、章节、KB 问答、组会纪要走同一 `run_agent` |
| **A13** | 引擎：checkpointer + 并行 + 反馈边 | 伪造指标、把 `paper_reproducer` 塞进主图 | 暂停恢复走 `thread_id` 而非 `sys.exit(0)`；并行后收尾闸口径不变 |
| **A14** | 底座：部署收敛、CI 镜像、可观测、e2e | 新监控台产品 | `deploy/release.py` 取代 `_cloud_g*` 热补丁；`/metrics` 暴露 Agent 与解析指标 |

***

## 3. 允许的新子路径与新字段（仅这些）

**新子路径：**

- `POST /labs/{lab_id}/ask` — 实验室级 Agent 会话（`scope=onboarding|lab`）。落在现有知识库问答栏，**不是新页面**。
- `GET /labs/{lab_id}/onboarding` — 聚合现有表（方向树 / 成员分工 / 导师负责 / 核心文献 / 近期组会 / 入门语料），只读。
- `GET|PATCH /labs/{lab_id}/members/{user_id}/profile` — 成员档案读写，挂现有成员页。
- `GET /literature/{item_id}/structure` — 章节与图表树，来自持久化的 GROBID 结果。
- `GET /agent/traces/{trace_id}` — 追溯某次回答用了哪些工具，导师与管理员可见。

**新表（仅一张，审计用途）：** `agent_traces`。禁止再建业务影子表。

**加列：**

- `literature_files`：`text_hash`（页文本缓存指纹）
- `literature_notes`：`anchor_json`（锚定高亮）
- `lab_memberships`：`bio` / `interests_json` / `skills_json` / `advisor_user_id` / `enrolled_at` / `expected_graduate_at` / `homepage`
- `kb_documents`：`source_kind`

**新明细表（缓存，非业务对象）：** `literature_file_pages`（页文本）、`literature_page_translations`（页译文）。二者都可安全清空重建，不得作为业务事实来源。

**禁止：** `/v2`、`/tutor` 独立页、`/translate` 页、第四个阅读侧栏、`member_profiles` 表、第二套 KB、提示词管理后台、Agent 自动烧算力。

***

## 4. Agent 运行时合同（实现者必读）

### 4.1 调用约定

```
run_agent(db, *, user_id, lab_id, scope, messages, feature,
          max_steps=..., item_id=None, direction_id=None) -> Iterator[dict]
```

逐步产出事件，事件形状固定：

- `{"step": n, "tool": name, "args": {...}}` — 准备调用
- `{"step": n, "tool": name, "ok": bool, "summary": str, "source_ids": [...]}` — 调用结果
- `{"delta": str}` — 可见增量文本
- `{"done": True, "text": ..., "sources": [...], "ungrounded": bool, "trace_id": ...}` — 收尾

### 4.2 硬约束

1. **权限在工具内部强制。** 每个工具的第一步都要复验 `lab_id` 归属与成员身份，不靠提示词、不靠调用方。
2. **记账口径不变。** 沿用 `assert_lab_quota` 与 `_ledger`；失败仍记一次尝试（G80）。一次 `run_agent` 的多轮调用合并记账，不得按轮数放大成多笔。
3. **接地改为 `source_id` 追溯。** 工具返回的每个片段带 `source_id`；输出中的引用必须能指回某个 `source_id`，否则该句标 `ungrounded`。事后正则（`ground_against_corpus`）保留作为无工具路径的兜底。
4. **降级必须可见。** 工具连续失败、超预算、模型不支持 tool-calling 时降级为单轮 `chat()`，响应里写明 `degraded_to=chat` 与原因，禁止静默。
5. **`<think>` 仍剥。** 复用 `strip_model_reasoning`，Agent 的中间推理不进用户界面（工具调用摘要可进）。
6. **A-NOBURN。** 写入与作业类工具返回 `{"pending_confirm": {...}}`，由前端出一次确认再提交。

### 4.3 工具作用域

- `paper` — 阅读器辅导。只能看这一篇 + 本人笔记 + 本篇复现证据。
- `lab` — 知识库问答、跨篇提问。能看本实验室 KB、文献库、方向、复现脊柱。
- `onboarding` — 新生入门。`lab` 的只读子集 + 成员档案与分工，**不含写入类工具**。
- `writing` — 写作与译润。`lab` + 手稿写回（仍受 `expected_updated_at` 冲突保护）。

***

## 5. 新生入门的技术口径（关键决策，不要改成全量 RAG）

**结构化事实走工具直查数据库，非结构化知识走现有 KB 的 RAG。**

理由：「谁做什么 / 导师负责什么 / 方向怎么分」是**精确查询**，不是模糊检索；而且人员与方向每周都在变，塞进向量库必然过期且不可复算。

- **走工具（永远最新、权限精确、可复算）：** 方向树（`research_directions.parent_id`，两级）、成员与多方向挂靠（`membership_direction_links`）、职称年级（`lab_memberships.title`）、成员档案、学位节点（`degree_milestones`）、在读与手稿。
- **走 RAG（复用 `services/kb.py` 已有的向量 + 词法 + MMR + 术语扩写 + rerank + 反馈先验）：** 方向说明、组会纪要、入门手册、已发表手稿、核心文献方法卡。通过 `kb_documents.source_kind` 区分。
- **禁止：** 把成员名单与方向树写进向量库；新建第二套 RAG 栈；用 LLM 猜测谁做什么。

***

## 6. 去压力化口径（A11，不改事实判定）

后端字段与 `D-HONEST` 判定**一个不动**。只改「谁看到、看到几条、什么措辞」：

- 学生端 `next_steps` / `AttentionBar` / `FeedbackStrip` 最多 2 条，只谈本人，中性陈述式。
- 导师端卡住清单收进默认折叠面板，展开才出全量与题名芯片。
- 列表页与铃铛移除催办芯片，保留过滤器本身。
- 措辞由「还没做完 / 还没写到 / 无人在读」改为进度陈述。
- `docs/UI.md` 同步改写，相关 pytest **改断言不删测试**——字段仍须存在，只是不再要求出现在学生首屏。

***

## 7. 明确不做

Sci-Hub、付费墙绕过、官方查重、排行红灯、暗色主题、键鼠监控、第三角色、`/pi` `/atlas` `/degree`、第三条「AI 写论文」、Streamlit 当主 UI、无 GPU 伪造实验指标、Agent 未经确认烧算力、提示词管理后台、跨实验室合并驾驶舱、企微、会议 ASR。

***

## 8. B-ASSIST 助手授权（产品负责人本次解禁）

A-AGENT 曾禁止新聊天页。本条只为「组里助手」放开下面这些，其余禁令仍在。

**解禁的入口：**

- 页面 `/labs/:id/assistant`。侧栏师生第一项叫「助手」。版式是一条对话纸面，不算第五张版式。
- 顶栏「问助手」按钮，打开右侧抽屉。阅读器页这个按钮打开现有 `?panel=tutor`，不另开第二个聊天框。
- 旧的 `/kb?tab=ask` 跳到助手页。知识库页只留文档和术语。

**仍禁止：** 新业务影子表、`/v2`、第二套知识库、付费墙绕过。

**只加列：**

- `research_directions.brief_json` — 方向简报
- `research_directions.frontier_json` — 向外看的前沿
- `labs.brief_json` — 组里一页纸
- `lab_memberships.work_brief_json` — 每人在做什么

每篇助手笔记写进现有 `literature_analyses` 方法卡的 `result_json.plain`，一次模型调用同时出方法卡和大白话。

**新子路径：**

- `GET /labs/{id}/assistant/home`
- `GET /labs/{id}/directions/{dir_id}/frontier`，`POST` 同路径刷新
- `POST /labs/{id}/literature/digest-all`
- `GET /labs/{id}/literature/digest-status`
- `POST /labs/{id}/ask` 增加字段 `stream`、`context`

诚实字段（`ungrounded`、`has_card`、`core_missing` 等）一个不改名。A-NOBURN 不变：写笔记、改稿、提交作业仍要人点一次确认。

## 9. B-SCALE 几千篇底座授权（产品负责人本次解禁）

为了一个组放几千篇论文还能分清作者和引用，本条只放开下面这些，其余禁令照旧（`/v2`、第二套知识库、付费墙绕过、Agent 未经确认烧算力仍禁止）。

**只许三张新表：**

- `authors` — 一个人一行，**按组各存一份**（导师的拆开、合并只影响本组，不改别的组），只存公开信息：显示名、名字归一键、ORCID、OpenAlex 作者编号、Semantic Scholar 编号、别名、单位、被并到哪条。
- `authorships` — 哪篇论文署了谁：篇、作者、排第几、原文写法、当时单位、ORCID / 外部编号、认人依据（orcid / openalex / s2 / coauthor / affiliation / name_only / manual）。
- `literature_references` — 这篇引了什么：篇、序号、原文、题名、年份、DOI、arXiv 号、第一作者、归一键、对上的组内那篇、来源（grobid / openalex / crossref）。

**只许一个新服务：** GROBID（读论文结构和参考文献），限 3GB 内存，失败时退回按页切。

**只加列：**

- `kb_chunks`：`lab_id`、`literature_item_id`、`kind`、`section`、`token_count`、`embed_model`（兜底向量记 `hash`，有真向量服务后自动补）
- `literature_items`：`read_level`、`read_score`、`cited_by_count`、`openalex_id`、`paper_embedding_json`、`enrich_state`（书目补全到哪步）
- `literature_files.structure_state` — GROBID 结构解析做没做、成没成
- `lab_memberships.author_id` — 成员认领自己在论文上的署名
- Postgres 上另建索引（不是新表）：`kb_chunks.embedding` 半精度 HNSW、`kb_chunks.content` 与 `literature_items` 题名/摘要/作者的 trigram 索引

**规矩：**

- 只凭名字一样绝不合并两个作者；合并要有 ORCID、外部作者编号，或名字加合作者、单位、方向里至少一条证据。导师可以拆开或合并，改过的以人为准。
- 论文之间的关系（引用、被引、同一作者、共被引、参考文献重合、主题相近、同一方向）只从数据里算，不让模型猜。
- 后台分层读：先书目，再摘要笔记，再全文笔记，最后完整方法卡；每月预算到 90% 停，停在哪层如实写出。
- `authors_json` 保留作展示与兼容；老接口字段不删不改名。
