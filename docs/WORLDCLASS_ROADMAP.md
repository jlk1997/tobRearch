# 蜂算科研：世界级加深施工合同（H2 起）

> 给 coding agent 与人类共同维护。产品名 **蜂算科研**。OpenLens 只作为引擎。
>
> **本文件是 H2 起的唯一施工合同。** H1 正文仍以 `docs/PLATFORM_UPGRADE_PLAN.md` 为准，禁止重写。
>
> 原则：**只升级现有对象。禁止平行实现。** 新需求必须先回答「改哪个现有 endpoint / 表 / 页面」。

按 **H2M0 → … → H3M3** 顺序执行。H2M0–H3M2 已通过后进入 H2M6 打磨与 H3M3 归档语义。禁止跳过阶段。禁止推翻 `PLATFORM_UPGRADE_PLAN.md` 第 1 节已拍板决策。禁止修改 `openlens_ai/agents/` 主图与 `openlens_ai/build_graph.py` 边语义（仅允许加深 `paper_reproducer/`）。

***

## 0. 执行公约

* 路径相对仓库根目录。新代码与既有代码并存，不得删除 OpenLens 流水线。
* 提交前缀：`[H2Mx]` / `[H3Mx]`。中文注释与文案。界面标题写「蜂算科研」。
* Streamlit（`webui/`）仅调试控制台。
* 不启用 Sci-Hub。不实现付费墙绕过。不实现键鼠 / 屏幕监控。不接官方查重库。
* 用户 / 实验室 API Key 加密存储，接口只回显后四位。
* 无 GPU 时禁止伪造实验指标。
* 前端对照 `docs/UI.md`。新交互先改 UI.md 再写代码；不改 IA 分组。
* 每阶段验收不过，不准进入下一阶段。

***

## 1. 反分叉公约（已锁定）

| 编号 | 规则 |
| --- | --- |
| D-NOFORK | 新需求必须先回答「改哪个现有 endpoint / 表 / 页面」。答不出就停，向用户确认。 |
| D-KEEPAPI | 现有 `/api/v1` 路径保持；只允许加字段、加子路径。禁止 `/v2`、禁止第二套 `analyze2`。 |
| D-KEEPTABLE | 不新建 literature / manuscript / run / kb 影子表。凭据不新建 `model_endpoints`；最多给 `llm_credentials` 加列。 |
| D-KEEPUI | 不新增与现有 IA 重复的侧栏分组。方向枢纽挂在「方向」；辅导挂在阅读器；算力挂在 Admin/Settings。禁止 `/atlas`、`/archive`、独立 `/tutor` 页。 |
| D-GPU2 | 推理=凭据，实验=GPUProfile。禁止混用。学生/导师不能改实验拓扑。 |
| D-DUAL | 每个里程碑必须同时有学生可点与导师可点增量，但是加深现有页。 |
| D-HONEST | 扫描件 `unusable`、无 GPU `awaiting_gpu`、无数据 `blocked`、模拟评审非官方 —— 行为不得回退。 |
| D-SEQ | 先 Agent 质量 + 凭据/KB embedding；实验室侧先方向枢纽与驾驶舱指标。H5.8–H5.174、评测夹具、Postgres 纪律与 Redis 可选队列、学位轴挂枢纽已落地。Era A–C、组内证据脊柱（E）、证据写回（F）、指挥面/出口吃证据（G）、写回/通知/过滤吃证据（H）、证据口径闭合（I）、证据出口补齐（J）与证据指挥面补齐（K）已落地；Era D 仍后置。企微 / ASR / H6 仍后置。已完成的原 H2 切片不再列入任务单。 |

旧计划第 1 节 D-* 全部继承。

***

## 2. 三方对照（禁止再做一遍）

### 原 H1 八支柱 —— 已完成，禁止重做产品面

认证邀请 RBAC、实验室/方向/成员、文献检索入库阅读笔记、手稿互看与评论版本、知识库问答与术语表、单篇分析/多篇对比、语言润色翻译拓展、写作大纲逐节写回 + Overleaf zip、发现式 `research_runs` + SSE、真复现 `reproduce_runs`、组会纪要→看板、资料、通知、审计、Compose、手册。

### 原 H2 清单 vs 仓库 vs 本期

| 原 H2 项 | 仓库 | 本期 |
| --- | --- | --- |
| 引用图谱 | `GET /labs/{id}/citation-graph` + Graph.jsx | 不新做 |
| 评审人模拟 | `POST /manuscripts/{id}/review-simulate` | 只升级规则，禁止第二套查重 |
| GROBID / OCR | `pdfparse.py` + compose profile grobid | 分析多用结构，禁止新 PDF 栈 |
| 周报邮件 | digest + 可选 email | 不新做周报产品 |
| 数据集注册 | datasets CRUD | 不新做登记处 |
| 领域提示词 | `prompts/management/` `materials/` | 可增 domain，禁止改 medical |
| 阅读进度 / 页注 | `progress_pct` `last_page_no` `notes.page_no` | 禁止重做 |
| 写作独立页 | `/labs/:id/writing` | 禁止再做一套 |
| CNKI/IEEE 捕手 | 尽力而为 + 降级 | 可打磨适配器，禁止新扩展 |
| 开题/中期/答辩 | 挂方向枢纽 | 禁止 `/degree` 导航 |
| 企业微信 | 未做 | 后期 |
| Redis 队列 | 可选 `REDIS_URL`，无则表轮询 | 已换实现，语义仍 `claim_next` |
| 会议 ASR | 未做 | 后期可选 |
| 官方查重库 | 永不做 | 永不做 |
| 多机构 SaaS | `org_id` 预留 | H6 |

`test_h16`–`test_h23` 是 H1.5 打磨，**不是**原 §17 的 H2 阶段。

### 本期真正增量

* 个人 Agent 质量：证据化精读、阅读器内辅导、写作批注挂引用、复现 internal/external 真跑。
* 算力产品化：凭据 `kind=cloud|onprem`、chat/vision/embed/rerank；KB 不再用 hash 冒充 embedding。
* 实验室可理解性：方向详情枢纽；驾驶舱可复算指标；引导清单补选方向。

***

## 3. 允许的新子路径（仅这些）

* `POST /literature/{id}/tutor` — 阅读器侧栏辅导，写回走现有 notes。`session_id` 可选，落在 `literature_analyses`。
* `GET /labs/{id}/directions/{direction_id}/overview` — 方向枢纽聚合已有表。
* `GET /labs/{id}/directions/{direction_id}` — 同上详情（可与 overview 同构）。
* `POST /manuscripts/{id}/add-to-library` — 已发表手稿进入现有文献库（可选切块进 KB）。
* `POST /admin/llm-credentials/test` — 平台默认凭据测通，与个人/实验室测通同构。
* `GET /manuscripts/{id}/claims` — 手稿侧栏主张追踪（引用 × 方法卡），禁止新对象库。
* `GET /labs/{id}/jump` — 顶栏跳转现有文献 / 手稿 / 任务。
* `GET|POST /labs/{id}/directions/{direction_id}/milestones`、`PATCH /labs/{id}/milestones/{id}` — 学位节点挂方向与手稿。

禁止：`/atlas`、`/archive`、`/tutor` 独立页、`/degree` 导航、`model_endpoints` 表、第二套 Key UI。

***

## 4. 阶段与验收（摘要）

| 阶段 | 改哪里 | 禁止 | 验收要点 |
| --- | --- | --- | --- |
| H2M0 | 本文件 + 旧计划 §17 指针 + UI.md 补交互 | 重写 H1 | 对照表与路由一致 |
| H2M1 | `llm_credentials` 加列；Admin/Settings | 新端点表 | onprem base_url 可用；密钥掩码 |
| H2M2 | `kb.py` 走 embed/rerank | 新 RAG 栈 | 失败可 hash 回退并标明；隔离测试 |
| H2M3 | `analysis.py` + 阅读器 + tutor 子路径 | 新阅读产品 | 摘录为原文子串；无 PDF 仍 abstract_only |
| H2M4 | `paper_reproducer` + reproduce.py | 新 repro 引擎 | 方法卡只来自 analyze_item；无假指标 |
| H2M5 | writing + review-simulate + comments | 新写作引擎/查重 | 批注入 manuscript_comments |
| H3M1 | Directions 详情 + GUIDE_ITEMS | /atlas | 方向钻取看到论文/成员/手稿 |
| H3M2 | cockpit/stats + 接线 `/progress` | 新进度页 | 卡片可复算；禁止排行红灯 |
| H2M6 | 已落地模块打磨：辅导历史、批注可见、测通分类、进度分页 | 新产品页 | 已有入口无断层 |
| H3M3 | 文献过滤 + 已发表手稿走现有文献/KB | /archive | 过滤器与入库，不是新库 |
| H4 | compare 写回、手稿 claims、tutor session、复现列表筛选 | 新综述引擎/主张库 | 全部挂现有页 |
| H4.1 | 主张追踪 cite 宏、append 幂等、辅导 session URL、复现 dataset 过滤 | 新产品页 | 已有入口无断层 |
| H5 | `POST .../literature/recommend` 组内语料重排；模拟评审幂等 + unused key | 新推荐引擎/查重 | 仍是文献库推荐 Tab |
| H5.1 | 捕手适配器打磨、入库去重、方向详情别名、想读不计在读 | 新扩展/新产品 | 已有入口无断层 |
| H5.2 | 驾驶舱/统计在读口径对齐；周报与方向枢纽深链推荐 Tab；`llm_rerank` 标明 | 新进度页/新推荐产品 | 已有入口无断层 |
| H5.3 | 辅导按会话分页；主页/成员在读列表不被已读挤掉；库表回上次页码 | 新聊天页/新进度产品 | 已有入口无断层 |
| H5.4 | 主张追踪带方法卡页码互跳；章节/KB 引用卡片显示在读进度；阅读器 `?ms=` 回手稿 | 新主张库/新写作引擎 | 仍用 `?page=` / 现有手稿页 |
| H5.5 | 通知/周报/图谱/对比/方向抽屉/复现进阅读器带上次页或方法卡页 | 新图谱产品 | 仍用 `?page=`；现有 `href` 字段 |
| H5.6 | 语言工作台载入笔记写回；笔记深链带绑定页；写回后回阅读器 | 独立笔记产品 | 仍用阅读器 `?page=` / `?note=`；现有笔记表 |
| H5.7 | 语言工作台 `?ms=` 载入手稿并回跳；写回返回手稿 `href` | 第三条写作引擎 | 仍用现有手稿页与 `_maybe_writeback` |
| H5.8 | 写回摘要：模式 / 标题 / 选区预览 / 版本 / 回跳 | 第三条写作引擎 | `_maybe_writeback.writeback` |
| H5.9 | 方法卡质量口径：弱卡不覆盖好卡 | 新分析库 | 仍用 `literature_analyses` + `best_method_analysis` |
| H5.10 | 发现式详情可见组内注入 | 改 `build_graph.py` | `lab_context.json` + `injected` |
| H5.11 | 顶栏跳转文献/手稿/任务 | 新搜索产品 | `GET .../jump` 落地现有详情 |
| H5.12 | 纪要行 ↔ 看板任务 | 新会议产品 | `?open=` + `?line=`；`source_line` |
| H5.13 | `membership.title` 职称/年级 | 第三角色 / 新成员产品 | 权限仍 `teacher\|student` |
| H5.14 | 手稿评论定位 | 新批注产品 | `?comment=` 现有评论表 |
| H5.15 | 捕手登录与弱 meta | 新浏览器扩展 | 仍 `browser_ext/` |
| H5.16 | 请求号 + 工人 lag；版本 0.2.0 | 新监控产品 | `/health` 加字段；`X-Request-Id` |
| H5.17 | 通知/周报带 `comment_id` 与 `href` | 新通知产品 | 现有 `notifications` + `noticeHref` |
| H5.18 | 顶栏 jump 补方向/组会/数据集 | 新搜索产品 | 仍 `GET .../jump` |
| H5.19 | 学位节点可改状态、可挂手稿、通知归属人 | `/degree` 页 | 现有 milestones 子路径 |
| H5.20 | 引导按人口径；管理页看工人/用量时间 | 新监控台 | 现有 guide + `/admin/usage` |
| H5.21 | 方向通知落到枢纽；周报同口径 | 新周报产品 | `insight._href` / `noticeHref` |
| H5.22 | 空声称从最好方法卡填入 | 新指标库 / 把声称当实测 | 现有 `claimed_metrics` + `claimed_metrics_from_card` |
| H5.23 | 发现式 `injected.literature` 带上次页 | 改 `build_graph.py` | 现有 `stamp_items_open_href` |
| H5.24 | 首页/驾驶舱风险条：学位 due + 等 GPU/缺数据 | `/degree` 页 | 现有 home / cockpit / milestones |
| H5.25 | 主张追踪挂最近复现 | 新主张库 | 现有 `GET .../claims` |
| H5.26 | 对比进复现；驾驶舱 SSE 可重连；数据集用量 | 新对比/监控/登记产品 | 现有 Literature / Cockpit / datasets |
| H5.27 | 待处理条只在有事时出现；节点带剩余天数 | 新待办产品 / `/degree` | 现有 AttentionBar + `due_label` |
| H5.28 | 主张/对比/阅读器复现入口对齐 | 新主张库 | 现有 claims / compare / `?literature_item_id=` |
| H5.29 | 数据集用量点进已筛列表；发现式可按数据集筛 | 新登记处 | 现有 datasets + `research-runs?dataset_id=` |
| H5.30 | 阅读器侧栏分栏，不堆四张卡 | 新阅读产品 / `/tutor` | 现有阅读器 `?panel=` |
| H5.31 | 写作选题文献芯片 + 写回 URL 同步 | 第三条写作引擎 | 现有 `/writing` `?lit=` `?ms=` |
| H5.32 | 语言写回结果条；章节引用带 href | 独立笔记产品 | 现有 language / `writing/section` |
| H5.33 | 手稿侧栏分栏，不堆六张卡 | 新批注/主张产品 | 现有手稿 `?tab=`；`?comment=` 先开评论 |
| H5.34 | 知识库分栏；问答/综述引用盖 href | 新 RAG / 笔记产品 | 现有 `/kb` `?tab=` + `query_kb` |
| H5.35 | 方向枢纽 / 图谱文献「去复现」 | 新图谱 / `/degree` | 现有 overview / citation-graph |
| H5.36 | 首页 / 驾驶舱阅读芯片 | 新待办 / 新进度页 | 现有 home / cockpit readings |
| H5.37 | 发现式注入与列表文献去复现 | 改 `build_graph.py` | 现有 runs `injected` / list |
| H5.38 | 顶栏 jump / 周报文献去复现 | 新搜索 / 新周报 | 现有 `jump` + digest |
| H5.39 | 文献库主表 / 推荐 Tab 去复现 | 新文献产品 | 现有 `GET .../literature` + recommend |
| H5.40 | 通知 / 动态文献去复现 | 第二套待办 | 现有 `enrich_literature_open_links` |
| H5.41 | 统计可点；手稿列表去写作 | 新统计台 / 第三条写作 | 现有 stats + manuscripts 列表 |
| H5.42 | 首页指派回纪要；进行中任务带对象 href | 新待办 / 新会议页 | 现有 home `tasks` / `runs` / `reproduces` |
| H5.43 | 数据集关联文献去复现；用量带对象 href | 新登记处 | 现有 `literature_rows` + `usages` |
| H5.44 | 手稿/写回 409 带回跳 | 选区锁 / 新协同产品 | 现有 `expected_updated_at` 冲突体 |
| H5.45 | 成员表进驾驶舱 / 方向枢纽 | 独立档案 / 第三角色 | 现有 `GET .../members` |
| H5.46 | 顶栏铃铛与通知页同一套芯片 | 第二套待办 | 现有 `notifications` + `NoticeBell` |
| H5.47 | jump / 组会 / 资料 / 日志对象 href | 新搜索 / 新会议产品 | 现有 `jump` + 列表 DTO |
| H5.48 | 发现式 `SystemExit` 正确收尾 | 改 `build_graph.py` 边 | 现有 `execute_research_run` |
| H5.49 | 组内上下文写入引擎已读路径 | 新 RAG / 改六子图 | 现有 `lab_context.json` + question |
| H5.50 | 复现 `fix`/`improve` 带诊断再实现 | 新复现引擎 / 把声称当实测 | 现有 `paper_reproducer` + `/improve` |
| H5.51 | 方法卡空态说清下一步 | 新分析产品 | 现有阅读器警告条 + `card_empty` |
| H5.52 | 发现式进度读 `states/step_*.json` | 改图边 / 猜 Starting 字符串 | 现有 `subgraph_progress` |
| H5.53 | 统计本月三卡与 `by_year` 可点 | 新统计台 / 排行红灯 | 现有 `_stats_payload` + Stats |
| H5.54 | 看板待认领 + 首页/驾驶舱芯片 | 新待办页 | 现有 `?assignee=none` + AttentionBar |
| H5.55 | `/health` 探 db / 可选 redis | 新监控产品 | 现有 `/health` 加字段 |
| H5.56 | CI 前端构建；可选 e2e / nightly | TS 重写 / 第二套 UI | 现有 `fengsuan.yml` + `web_next` |
| H5.57 | 发现式 `awaiting_gpu` | 改图边 / 伪造指标 | 现有 `execute_research_run` + `?status=` |
| H5.58 | 数据集列表对齐详情密度 | 新登记处 / 排行榜 | 现有 `GET .../datasets` + Datasets 表 |
| H5.59 | 读→析→复现空态引导 | 新分析/复现产品 | 现有 reproduce `guide` + 阅读器 |
| H5.60 | 等 GPU 后恢复 | 新队列产品；GPU 仍 off 却开跑 | 现有 `POST .../resume` |
| H5.61 | 管理页开启 GPU 后重新排队 | 新监控台 / 排行榜 | 现有 `PUT /admin/gpu-profiles` |
| H5.62 | 文献库列表全文空态 | 新文献产品 / 再撒「去复现」 | 现有 `GET .../literature` + 库表 |
| H5.63 | 首页/推荐/驾驶舱全文空态 | 新文献产品 / 再撒「去复现」 | 现有 home / recommend / cockpit readings |
| H5.64 | 复现列表引导 | 第三条复现引擎 | 现有 `GET .../reproduce-runs` `guide` |
| H5.65 | 等 GPU 进动态 | 新待办 / 把等待写成失败 | 现有 `run.awaiting_gpu` / `reproduce.awaiting_gpu` |
| H5.66 | 动态深链 + 发起人铃铛 | 新收件箱 / 等 GPU 发邮件 | 现有 `serialize_event` + `fanout_activity` |
| H5.67 | 复现诊断可执行 | 第三条复现引擎 / 把声称当实测 | 现有 `paper_reproducer` + `diagnosis.json` |
| H5.68 | jump / 对比 / 主张 / 首页诚实 | 新搜索 / 新主张产品 | 现有 jump + compare + claims + home `runs` |
| H5.69 | 队列点得开具体任务 | 新队列页 | 现有 `lab_queue.items` + AttentionBar |
| H5.70 | 管理 GPU 列出等待作业 | 新监控台 / 排行榜 | 现有 `GET|PUT /admin/gpu-profiles` `waiting` |
| H5.71 | 发现式诊断 + 驾驶舱 Agent 深链 | 改图边 / 新 Agent 页 | 现有 research DTO + cockpit `agents` + digest |
| H5.72 | GPU 开启通知发起人 | 新收件箱 / 等 GPU 发邮件 | 现有 `release_awaiting_gpu` + `run.released` |
| H5.73 | 统计/成员进展看见等待 | 新统计台 / 排行榜 | 现有 stats + cockpit member `agents` |
| H5.74 | 待处理/列表直接恢复 | 新队列页 | 现有 `can_resume` + `POST .../resume` |
| H5.75 | 工人未开时入队可恢复 | 新队列页；GPU PUT 当场全跑 | 现有 `job_can_resume` + `POST .../resume` |
| H5.76 | 方向枢纽看见成员任务 | `/atlas` / 新数据集产品 | 现有 direction `overview.agents` + dataset `usages` |
| H5.77 | 阅读器最近复现 / jump 入队 / 未读下一步 | 新阅读产品 / 再撒「去复现」 | 现有 literature `latest_reproduce` + jump + home unread |
| H5.78 | 管理/统计看见无工人入队 | 新监控台；改 awaiting 口径 | 现有 gpu-profiles / stats `pending_*` |
| H5.79 | 通知/jump 跟活状态可恢复 | 新收件箱 / 新搜索 | 现有 `stamp_job_live` + resume |
| H5.80 | 文献库/周报/探活跟活状态 | 新阅读产品 / 新监控台 | 现有 list `latest_reproduce` + digest + `/health` |
| H5.81 | 恢复结果诚实反馈 | 新 toast 产品；改 awaiting 计数/href | 现有 DTO `feedback` `{tone,title,next_step,can_resume}` |
| H5.82 | 恢复按钮防连点 + 跟返回态 | 新队列页；GPU PUT 自动全跑 | 现有 `ResumeJob` / `tellFeedback`；AttentionBar 只刷新 |
| H5.83 | 指挥面着色与详情可恢复 | 暗色主题；新 IA | 现有 `FeedbackStrip` + `runWaitHint`；复现详情看 `can_resume` |
| H5.84 | 创建任务跟返回态 | 创建时自动开跑发现式；改 awaiting 口径 | 现有 POST create + `tellFeedback` |
| H5.85 | 暂停/取消/改进跟返回态 | 新工具条产品 | 现有 pause/cancel/improve + 忙碌防连点 |
| H5.86 | 开 GPU / 额度错误诚实 | 新监控台；GPU PUT 当场全跑 | 现有 gpu-profiles `feedback` + 429 `next_step` |
| H5.87 | 恢复拒绝非法状态 | 运行中/已跑完再开跑 | 现有 `POST .../resume` + `job_can_resume` |
| H5.88 | 暂停/取消 409 跟活状态 | 新工具条；藏始终可见按钮 | 现有 pause/cancel + `can_pause`/`can_cancel` |
| H5.89 | 流式与诊断不盖活状态 | 新监控台 / 改图边 | 现有 SSE snapshot + `diagnosis_is_live` |
| H5.90 | 改进一轮拒绝非法状态 | 第三条复现引擎 | 现有 `POST .../improve` + `job_can_improve` |
| H5.91 | 结束态再入队占额度 | 改重型语义；GPU PUT 全跑 | 现有 `ensure_reenter_heavy` + 429 |
| H5.92 | 驾驶舱看见 queued | `/atlas` / 新 Agent 页 | 现有 cockpit `agents` 状态集 |
| H5.93 | HTTP 开跑 CAS，连点不双跑 | 新队列；改 claim_next 语义 | 现有 `try_begin_execute` + `execute_*` |
| H5.94 | 额度 2 次查询 + SQLite 写锁 | 新监控台；第二工人 | 现有 `count_heavy` + `lab_quota_guard` |
| H5.95 | 空闲 SSE 复用快照 | 新流产品 | 现有 research SSE；running 仍读盘 |
| H5.96 | 方法卡 metrics 落地；短摘录不再自动过关 | 新分析库 | 现有 `ground_card` / `assert_excerpt` |
| H5.97 | 对比 gap 无 item_id 则裁句 | 新综述引擎 | 现有 `POST .../compare`；禁止 `table_md` 冒充 `gap_md` |
| H5.98 | 辅导数字/摘录须为本页或最好卡子串 | `/tutor` 页 | 现有 `POST .../tutor`；否则打标并改下一问 |
| H5.99 | 大纲吃最好方法卡主张/方法/限制 | 第三条写作 | 现有 `POST .../writing/outline`；无卡标「未结合方法卡」 |
| H5.100 | 章节/KB 生成数字须为本轮 hits 子串 | 新 RAG | 现有 `write_section` / `kb_query` / summarize；否则 `ungrounded` |
| H5.101 | 引用图谱用最好卡而非最新卡 | 新图谱产品 | 现有 `citation_graph` 的 `source_page` / related |
| H5.102 | 推荐 DTO 标明 hash；pgvector 用真实距离 | 新推荐页 | 现有 `recommend_for_lab` + `query_kb` |
| H5.103 | vision 走 `cfg.llm.vision`；超预算 429 | 改图边；假装生成成功 | 现有 `resolve_llm(purpose=vision)` + `assert_lab_quota` |
| H5.104 | Era A 后半成熟度夹具 | 新产品页 | 现有 `test_h599_maturity.py` |
| H5.105 | `format_prompt` 注入 lab_context / data_report | 改图边 | 现有 `chatbot.py` 占位符 |
| H5.106 | 监督提示词：无数据报告不得装已剖析 | 改 medical 专稿 | 现有 `supervisor_plan.md` / alter |
| H5.107 | adaptor 空 file_manager 仍执行 prompt | 新编码引擎 | 现有 `openhands_adaptor.py` |
| H5.108 | 文献覆盖+必读全文；工具次数只作上限 | 改子图主文件 | 现有 `literature_search.md` + `paper_search_tool` 去重 |
| H5.109 | 缺 literature_review.md / plan.md 不得 succeeded | 改图边 | 现有 `execute_research_run` 收尾 |
| H5.110 | 未点名组内标题须写「未使用：标题」 | 新表 | 现有 `error` / `diagnosis` |
| H5.111 | 数据报告去掉 Medical 串台；列名来自 data_show | 改 medical 专稿 | 现有 `data_report.md` |
| H5.112 | 发现式夹具 | 新产品页 | 现有 `test_h5105_maturity.py` |
| H5.113 | `error_class` 区分 stub / no_docker / no_gpu / parse | 第三条复现引擎 | 现有 `try_implement` + `_dispatch_external` |
| H5.114 | 无实测或 actual==claimed 不得 succeeded | 声称写入实测 | 现有 `diagnose` + `advance_once` + 复现详情 |
| H5.115 | `actual_metrics.json` 小模式；非法文件当未跑 | 新指标库 | 现有 `normalize_actual_metrics` |
| H5.116 | 实现提示对齐方法卡字段；improve 吃诊断 | 第三条写作 | 现有 `try_implement` prompt + `load_improve_diagnosis` |
| H5.117 | 外部 GPU 回调仍禁止 claimed→actual | 改 GPU PUT 语义 | 现有 `_dispatch_external` |
| H5.118 | Era C 夹具 | 新产品页 | 现有 `test_h5113_maturity.py` |
| H5.119 | 文献详情/列表带复现证据脊柱 | 新实验台 | 现有 `latest_reproduce` + `reproduce_evidence` |
| H5.120 | 对比 gap 写入组内实测/未跑 | 第三条综述 | 现有 `compare_items`；禁止 claimed→实测 |
| H5.121 | 辅导看见复现证据；声称数字不得过关 | `/tutor` 页 | 现有 `tutor_item` + `ground_tutor_answer` |
| H5.122 | 大纲/章节吃复现差距 | 第三条写作 | 现有 outline / `write_section` |
| H5.123 | 发现式 lab_context 写入复现差距 | 改图边 | 现有 `build_lab_injection` |
| H5.124 | 改进一轮吃精读追问/手稿批注 | 第三条复现引擎 | 现有 `improve_hint` + `load_improve_lab_feedback` |
| H5.125 | 驾驶舱/统计复现诚实计数 | 新监控台 / 排行 | 现有 cockpit / stats `reproduce_spine` |
| H5.126 | Era E 夹具 | 新产品页 | 现有 `test_h5119_maturity.py` |
| H5.127 | 图谱文献节点带 `latest_reproduce` | 新图谱产品 | 现有 `citation_graph` |
| H5.128 | 方向枢纽文献行 + 方向级 `reproduce_spine` | `/atlas` | 现有 `direction_overview` |
| H5.129 | 手稿主张带 measured/not_run | 官方查重 | 现有 `GET /manuscripts/{id}/claims` |
| H5.130 | KB hits / 综述带证据 | 新 RAG | 现有 `query_kb` / `summarize_lab_kb`；`reproduce_gap_md` 单独字段 |
| H5.131 | 首页待处理看见未跑复现 | 改 awaiting href | 现有 `home` + `AttentionBar` |
| H5.132 | 发现式 sidecar `reproduce_gaps.md` | 改图边 | 现有 `write_lab_context`；unused 扫描跳过该文件 |
| H5.133 | 方向说明复现差距幂等写回 | 新方向页 | 现有 `PATCH append_note` + `merge_direction_note` |
| H5.134 | Era F 夹具 | 新产品页 | 现有 `test_h5127_maturity.py` |
| H5.135 | 模拟评审拦截「未测当成功」 | 官方查重 | 现有 `review-simulate`；`ungrounded_reproduce` |
| H5.136 | 周报带 `reproduce_spine` | 新周报产品 / 企微 | 现有 `digest` + `digest_markdown` |
| H5.137 | 统计方向行带复现诚实计数 | 排行榜 | 现有 `by_direction` / `stats.csv` |
| H5.138 | 成员进展文献事件带证据 | 新进度页 | 现有 `/progress` |
| H5.139 | 首页引导出现未跑复现 | 新引导产品 | 现有 `build_lab_guide` |
| H5.140 | 发现式综述把未测写成成功则失败 | 改图边 | 现有 `research_output_gate` |
| H5.141 | 发现式详情/工件露出差距 sidecar | 新 Agent 页 | 现有注入卡 + `reproduce_gaps.md` |
| H5.142 | Era G 夹具 | 新产品页 | 现有 `test_h5135_maturity.py` |
| H5.143 | 手稿保存警告「未测当成功」，不拦保存 | 新写作闸 | 现有 `PATCH /manuscripts/{id}`；`ungrounded_reproduce` |
| H5.144 | 通知带 `latest_reproduce` | 新通知产品 | 现有 `GET /notifications` + home `unread_items` |
| H5.145 | 文献库按诚实过滤 | 新文献站 | 现有 `GET /literature?reproduce=` |
| H5.146 | 复现列表按诚实过滤 | 改 awaiting href | 现有 `GET /reproduce-runs?honest=` |
| H5.147 | Overleaf zip/sync 带差距 sidecar | 写进 main.tex | 现有 `export-zip` / `overleaf-sync` 加 `reproduce_gaps.md` |
| H5.148 | 语言输出含成功套话则打标 | 第三条语言引擎 | 现有 polish / translate / expand |
| H5.149 | 现有页吃过滤与警告 | 新芯片堆砌「去复现」 | 文献/复现过滤；通知/首页诚实；手稿/语言/写作警告 |
| H5.150 | Era H 夹具 | 新产品页 | 现有 `test_h5143_maturity.py` |
| H5.151 | 同一文献优先最近实测 | 新实验台 | 现有 `latest_reproduce_runs` / `lab_reproduce_spine` |
| H5.152 | 「尚无实测」含缺数据 | 改 awaiting href | 现有 `?reproduce=not_run` / `?honest=not_run` |
| H5.153 | 手稿警告看见题名点名 | 官方查重 | 现有 `manuscript_success_warning` + `review-simulate` |
| H5.154 | 首页/跳转手稿带警告 | 新搜索站 | 现有 `home` / `jump` |
| H5.155 | 本库检索 stamp 证据 | 新文献站 | 现有 `search_local` |
| H5.156 | 手稿列表 `?ungrounded=` | 新写作闸 | 现有 `GET /manuscripts` |
| H5.157 | 通知/首页/铃铛露出未测当成功 | 新通知产品 | 现有通知 DTO + 现有页 |
| H5.158 | Era I 夹具 | 新产品页 | 现有 `test_h5151_maturity.py` |
| H5.159 | Overleaf sidecar 列入正文点名未测题名 | 写进 main.tex | 现有 `manuscript_reproduce_gaps_sidecar` |
| H5.160 | 语言无手稿仍点名组内未测题名 | 第三条语言引擎 | 现有 polish / translate / expand |
| H5.161 | 主张追踪顶栏警告 + 点名未引用篇 | 官方查重 | 现有 `GET /manuscripts/{id}/claims` |
| H5.162 | 驾驶舱成员手稿 + 统计未测当成功计数 | 排行榜 | 现有 cockpit / stats / `stats.csv` |
| H5.163 | 周报手稿事件 stamp 警告 | 新周报 / 企微 | 现有 `digest` + `digest_markdown` |
| H5.164 | 发现式闸读 `lab_context.json` 文献题名 | 改图边 | 现有 `dishonest_reproduce_claims` |
| H5.165 | 现有页吃统计/驾驶舱/主张/周报警告 | 新芯片堆砌「去复现」 | 现有统计卡 / 驾驶舱手稿 / 主张栏 / 周报 |
| H5.166 | Era J 夹具 | 新产品页 | 现有 `test_h5159_maturity.py` |
| H5.167 | 发现式未点名闸读 `lab_context.json` 题名 | 改图边 | 现有 `unused_injected_titles` |
| H5.168 | 动态/进展手稿事件 stamp 警告 | 新进度页 | 现有 `recent_events` / `progress` |
| H5.169 | 方向枢纽手稿带未测当成功 | `/atlas` | 现有 `direction_overview` |
| H5.170 | 图谱手稿节点带警告 | 新图谱产品 | 现有 `citation_graph` |
| H5.171 | 引导出现未测当成功手稿 | 新引导产品 | 现有 `build_lab_guide` |
| H5.172 | 写作大纲/章节与 KB 点名未测题名；已实测点名不再误伤 | 第三条写作/RAG | 现有 outline / section / kb query / summarize |
| H5.173 | 现有页吃动态/方向/图谱/引导/KB 警告 | 新芯片堆砌「去复现」 | 现有 ActivityLine / 方向 / 图谱 / GuideList / KB |
| H5.174 | Era K 夹具 | 新产品页 | 现有 `test_h5167_maturity.py` |

***

## 5. 硬约束

1. 不要改 `openlens_ai/agents/`（除加深 `paper_reproducer/`）和 `build_graph.py` 流水线语义。
2. `Config` 是唯一配置入口。
3. 不启用 Sci-Hub。
4. 不删除 `webui/`。
5. 无 GPU 时禁止伪造实验指标。
6. 发现式长文仍只走 `/research-runs`，禁止第三条「AI 写论文」。

***

## 6. 已落地状态（H2M0–H3M2）

H2M1 凭据 `kind` / `model_embed` / `model_rerank`；H2M2 KB 真向量 + `embed_fallback=hash`；H2M3 摘录子串与阅读器辅导；H2M4 复现方法卡只来自 `analyze_item`；H2M5 章节引用与导师式批注入 `manuscript_comments`；H3M1 方向 overview；H3M2 驾驶舱可复算指标与 `/progress` 分页。详细验收仍以上表为准，禁止重做产品面。

***

## 7. H2M6 已落地模块打磨（本轮）

只加深现有页与字段，禁止新产品。

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 文献库 | 过滤只进、不回写 URL；推荐未读未展示 | `direction_id` / `reading_status` / `cited_by_published` 写入查询串；推荐 Tab 展示 `lab_unread`（文案「本方向未读」） |
| 阅读器 | 辅导覆盖方法卡；无历史 | 方法卡按 `analysis_type=method_card`；辅导历史来自 `tutor` 行；`write_note` 可选 |
| 写作 | 模拟评审不展示评论 | 「导师式批注」后刷新 `manuscript_comments` |
| 手稿 | 已发表无处归档 | `POST .../add-to-library` 幂等（`external_ids.manuscript_id`），可选 KB；阅读器标明来自手稿 |
| 方向枢纽 | `recent_events` 未按方向收窄；缺手稿/图谱链 | overview 带 `manuscripts_href` / `graph_href?direction_id=`；动态按对象/成员过滤 |
| 驾驶舱 | `/progress` 未分页 | 成员时间线 `page`/`total`；`total > page_size` 翻页 |
| 凭据 | `ok:false` 被当成成功 | `reportPing`：失败走 `message.error` + `error_class`；管理员可测平台 Key |
| 知识库 | 空命中不标 hash | 无答案仍展示 `embed_fallback=hash` |
| 对比/图谱 | 最新行可能是 tutor | 对比与图谱 related 边只读 `method_card` |
| 复现 | 轮次日志不可读 | 详情轮次表展示 `log_summary`；声称 vs 实测分列 |

验收：`pytest platform/tests`；辅导后详情同时含 method_card 与 tutor；草稿 add-to-library 400；已发表第二次调用 `already_in_library=true`。

***

## 8. H3M3 归档语义（过滤器，不是新库）

- 文献列表：`?direction_id=`、`?reading_status=`、`?cited_by_published=1`。
- 手稿列表：`?direction_id=`。
- 图谱：`GET /citation-graph?direction_id=` 过滤节点，禁止 `/atlas`。
- 已发表手稿 → 现有 `literature_items`（`source_channel=manuscript`）+ 可选 `kb_documents`。
- BibTeX 导出已支持 `direction_id`。

禁止：`/archive` 产品页、第二套文献表。

***

## 9. 仍后置（不要提前、不要改名冒充 Agent）

- 企微（只做通知通道，深链仍 `noticeHref`）
- 会议 ASR
- H6：多 org / 插件 / 移动端 / 多区域。若做实验注册，只做对象目录与复现记录，**禁止排行榜**。

官方查重永不做。暗色主题不做。Sci-Hub 不做。学位轴与 Redis 队列实现已按合同落地，禁止再做成独立产品。

H5.174 之后的前沿成熟加深（大导师态势、检索主路径、相关工作、引擎书目桥接）见 **`docs/FRONTIER_MATURITY_PLAN.md`**。本文件不再追加切片。

G347 之后的 Agent 运行时、阅读性能与新生入门见 **`docs/AGENT_ERA_PLAN.md`**（A 系列）。该文件经产品负责人授权解禁三项：平台侧 Agent 运行时（A-AGENT）、成员档案与入门语料（A-PROFILE）、`build_graph.py` 边语义（A-ENGINE）。本文件第 5 节硬约束第 1 条与第 9 节「主图反馈回环后置」由 A-ENGINE 取代；其余禁令（Sci-Hub、官方查重、排行红灯、暗色主题、第三条「AI 写论文」、`D-HONEST` 事实判定）继续有效。

***

## 10. 明确不做

Sci-Hub、付费墙绕过、监控、排行红灯、官方查重、Streamlit 主 UI、学生改 GPU 拓扑、聊天框替换对象页、与现有 IA 重复的侧栏、第二套分析/写作/复现/KB/文献/凭据。

***

## 11. H4 本轮落地（挂现有入口）

| 能力 | 改哪里 | 禁止 |
| --- | --- | --- |
| 辅导续聊 | `POST /literature/{id}/tutor` 增加 `session_id`；历史写入同一 `literature_analyses` 行字段 | 独立 IM / `/tutor` 页 |
| 主张追踪 | `GET /manuscripts/{id}/claims` + 手稿侧栏 | 新 claims 表 / 查重产品 |
| 短综述写回 | compare 返回 `gap_md`；方向 `append_note`；写作 `writeback` | 第三条写论文引擎 |
| 章节补引用 | `writing/section` 选手稿时写入 `manuscript_citations` | 新参考文献库 |
| 方向枢纽 | overview 含 `has_method_card` / `method_card_count` | /atlas |
| 引导 | 「打开本方向文献」须本人在该方向有在读/已读 | 新 onboarding 应用 |
| 复现资产 | `GET /reproduce-runs?status=&literature_item_id=` | 新实验登记处 |
| KB 综述 | `embed_fallback` 与问答同一横幅 | 新 RAG |

验收：同会话两次 tutor 的 `session_id` 相同；claims 能区分正文是否点名 citation_key；compare `gap_md` 可追加进方向说明；草稿章节不改正文但可补引用。

***

## 12. H4.1 已落地加深（仍挂现有入口）

H4 验收通过后只打磨，不新开产品面。

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 文献文案 | Windows 保存把中文打成 `?????` | 接口 detail / 推荐原因改用 Unicode 转义；辅导动态摘要用提问原文 |
| 主张追踪 | `key in body` 会误伤短 key；主张未对照正文 | 词边界 + `\cite`/`\citep`/`[@key]`；`claim_rows.mentioned_in_body`；覆盖计数 |
| 对比写回 | 连点方向说明会重复粘贴 | `append_note` 已存在则跳过；手稿写回只列出本人手稿 |
| 精读辅导 | 刷新丢失会话；新旧问答混在一起 | `?session=` 写入网址；「新会话」；侧栏只显示本会话 |
| 推荐未读 | 未标明是否已有方法卡 | `lab_unread.has_method_card` |
| 复现列表 | 只能按状态/文献 | 增加 `?dataset_id=`（实验资产过滤，不是新登记处） |
| 方向枢纽 | `want_read` 显示成在读 | 想读 / 在读 / 已读分文案 |
| 章节引用 | 他人手稿 id 可被补引用 | 仅作者可改 `manuscript_citations`；跨实验室 404 |

仍后置：学位轴、企微、Redis、ASR、多 org。官方查重永不做。

***

## 13. H5 本轮落地（挂现有推荐 Tab）

| 能力 | 改哪里 | 禁止 |
| --- | --- | --- |
| 组内语料重排 | `recommend_for_lab`：方向名/关键词/说明、方法卡、笔记、手稿题录；hash 向量 + 词重叠 | 新推荐产品 / 排行红灯 |
| 不仅 arXiv | 开放源 `arxiv` + `pubmed` + `semantic`；DOI/arXiv/PubMed/题名去重 | 新搜索器、知网全文库 |
| 组内相关 | `lab_related`：已入库但未挂本方向、语料匹配的篇目；可 PATCH 挂方向 | 第二套文献表 |
| 入库挂方向 | 推荐/检索「入库并在读」带当前 `direction_id` | 新导入流 |
| 模拟评审 | 替换上次 `review_sim` 评论；unused citation key；`\citep` / `[@key]` | 第二套查重 |

验收：`rerank=lab_corpus`；未挂方向且题录匹配的篇目出现在 `lab_related`；连续两次模拟评审评论数不变；未点名的 citation key 写入评论。

***

## 14. H5.1 已落地加深（修漏洞 + 捕手，仍挂现有入口）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 入库去重 | 只比 DOI 原文与「同年同题」；arxiv 版本号 / DOI 大小写会重复入库 | `find_duplicate` 含 arXiv（去 vN）/ PubMed / CNKI / DOI 大小写；缺少年份仍可按题名合并；命中时补全空 ident |
| 捕手双栈 | `parse_html_meta` 与 `parse_paper_html` 两套 | 前者转调后者；JSON-LD / og:title / `<title>` 去站名；URL 抽 arxiv/pubmed/cnki/ieee id |
| 知网/IEEE | 无 Highwire 时只有 generic | 适配器识别 + 弱 meta 仍降级，不假装全文库 |
| 方向合同 | 允许 `GET /labs/{id}/directions/{id}` 未实现 | 与 overview 同构别名 |
| 方向枢纽 | `want_read` 计入在读人数 | 仅 `reading` |
| 图谱深链 | 抽屉回退 `/graph` 丢掉方向 | 带 `direction_id` |
| 入库反馈 | 重复入库仍提示「已入库」 | `already_in_library`；捕手扩展同步提示 |

***

## 15. H5.2 已落地加深（实验室可理解性 + 真 rerank 标明）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 驾驶舱 / 统计 | `reading_count` 把想读、已读甚至全部阅读行算进「在读」 | 仅 `status=reading`；另给 `want_read_count`；方法卡只计在读篇目；进度均分只在读 |
| 方向枢纽 | 方法卡只统计抽屉里最近 40 篇；无推荐深链 | 全方向 `method_card_count`；`active_reading_count` / `want_read_count`；`recommend_href` |
| 周报 | 无推荐 Tab 深链 | `recommend_href`（带当前成员方向）；邮件 markdown 同样写出；对象仍进阅读器 |
| 文献库 | 推荐 Tab 不能从 URL 打开 | `?tab=recommend` 写入网址并自动拉取 |
| 真 rerank | 调用失败与未调用无法区分 | stub/失败保持组内语料序，`llm_rerank=false`；成功才 `true`，禁止排行红灯 |
| 入库 | PDF 同名会按题名误合并；捕手重复写动态 | upload/manuscript 不按题名去重；重复捕手不再刷 `imported_ext` |
| DOI | `dc.identifier` 整段 URL 当 DOI | 抽出 `10.xxxx/...` |

***

## 16. H5.3 已落地加深（阅读与辅导可理解性）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 精读辅导 | 详情只带 12 条且混进他人会话；刷新丢掉本会话更早的问答 | `GET .../detail?session=&tutor_page=` 只列本人本会话；`tutor_history.has_more`；侧栏「更早的辅导」 |
| 主页最近阅读 | 按打开时间混进已读/想读，正在读被挤出 | 先在读、再想读、再已读；排除 dropped |
| 成员「正在读」 | 8 条混合状态，已读会占满 | `reading_now` / `want_reads` / `finished_readings` 分列；`readings` 仍是合集以免旧客户端丢已读 |
| 文献库 | 点行总是打开第 1 页 | `literatureHref` 带 `last_page_no`，与阅读器进度、驾驶舱深链同一口径 |
| 辅导隐私 | 组内其他人的辅导出现在侧栏 | 仅 `created_by` 本人；方法卡仍组内可见 |

***

## 17. H5.4 已落地加深（写作与主张可理解性）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 主张追踪 | `claim_rows` 只有文本，点进阅读器总在第 1 页 | 行内保留 `pages` / `page_no`；条目带 `source_page`、当前用户 `progress_pct` / `last_page_no`；侧栏主张与「去生成」走 `literatureHref(..., { ms })` |
| 手稿参考文献表 | 标题链无页码、无进度 | `GET .../citations` 挂在读字段；链到上次页并带 `?ms=` |
| 章节引用卡片 | 能进 `?page=`，看不到自己读到哪 | `query_kb` 命中挂 `progress_pct`；卡片显示「在读 n%」，选手稿时带 `?ms=` |
| 知识库引用 | 同样只跳页、不显示进度 | 综述卡片与命中表共用同一套阅读字段 |
| 阅读器回跳 | 从手稿跳来后只能再点侧栏找手稿 | `?ms=` 工具条「回手稿」；「去写作」保留 `ms`；页注跳页不再丢掉 `ms`/`session` |

***

## 18. H5.5 已落地加深（深链页码口径）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 通知 / 首页未读 | `noticeHref` 总进第 1 页 | 列表时挂当前用户 `last_page_no` 与 `href`；前端优先用服务端 `href` |
| 周报 / 驾驶舱动态 / 进度时间线 | `_href` 无页码 | 同一套 `enrich_literature_open_links` |
| 引用图谱 | 节点 `href` 无页码 | 文献节点带 `last_page_no` / `source_page`（方法卡）与 `?page=` |
| 方向抽屉 | 文献标题无页码 | `last_page_no` + `href`；动态同样补页 |
| 对比卡 | 标题与「来源」无页码 | `items` 与 json 行挂进度/`href` |
| 复现 / 发现式 / 数据集 / KB 来源 | 进阅读器丢进度 | 列表与详情挂 `last_page_no`；复现方法卡页码用 SPA `literatureHref`，不再整页跳转 |
| 方法卡组内相关 | 相关篇从第 1 页打开 | 详情里 `related_in_lab` 挂当前用户上次页 |

***

## 19. H5.6 已落地加深（语言工作台与笔记回跳）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 笔记 DTO | 无 `item_id` / `href`，语言页无法回阅读器 | `note_dto` 带 `item_id` 与 `literature_open_href(..., note_id=)` |
| 语言写回新建笔记 | 不写 `page_no`，动态总进第 1 页 | `LanguageBody.page_no`；创建绑定页；更新默认保留原页 |
| 语言页 `?note=` | 只预选、不载入正文，无 `?page=` | 首次匹配自动载入；下拉标明 `p.N`；工具条「回阅读器对应页」 |
| 阅读器去语言 / 润色 | 丢掉当前页 | `languageHref` 带 `lit` / `note` / `page`（及已有 `ms`） |
| 笔记动态 / 通知 / 周报 | `href` 被读者自己的上次页盖掉绑定页 | payload 带 `page_no`+`note_id`；笔记事件优先绑定页；阅读器 `?note=` 高亮 |

***

## 20. H5.7 已落地加深（语言工作台回手稿）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 语言页 `?ms=` | 只预选手稿，不载入正文，写回后无回跳 | 无 `?note=` 时首次载入正文并默认写回手稿；须再选写回方式才落库 |
| 语言写回手稿 | 成功无 `href`，页头只有回阅读器 | `_maybe_writeback` 返回 `/manuscripts/{id}`；页头「回手稿」 |
| 手稿 / 写作入口 | 去语言丢掉手稿 | 手稿工具条「去语言工作台」；写作链带当前 `?ms=` / `?lit=` |

***

## 21. H5.8 已落地加深（写回可理解性）

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 语言 / 写作写回 | 成功后只看见整篇变了 | `_maybe_writeback` / 笔记写回返回 `writeback`：`mode`、`heading`、`selection_preview`、`version`、人读 `summary` 与现有 `href` |
| 空写回 | 须保持不落库 | 空 `write_mode` 仍不写手稿 |

禁止第三条写作引擎。仍走现有版本快照。

***

## 22. H5.9–H5.11 已落地加深

| 模块 | 断层 | 落地 |
| --- | --- | --- |
| 方法卡 | 再分析整行 INSERT，后一次弱卡盖掉好卡 | `best_method_analysis`：优先非 `abstract_only`、少 `ungrounded`；对比 / 复现 / 辅导 / 主张 / 阅读器都用最好的卡；弱卡打标 |
| 发现式 | 组内文献/数据集只是 sidecar ID | `lab_context.json` + `injected` 展示标题；约束提示写入问题旁注；**不改图边** |
| 实验室内跳转 | 搜索分散 | `GET /labs/{id}/jump?q=`；顶栏落地现有详情 |

***

## 23. Agent 评测夹具（诚实态）

固定断言：`env_check` 无数据 → `blocked`，需 GPU 且 `off` → `awaiting_gpu`；`compare_metrics` 不得把声称填进实测；弱 PDF 摘录标 `ungrounded`。禁止假指标、禁止插入主图。

***

## 24. 生产级底座（实现替换，契约不变）

- 完整实验室以 **Postgres 16 + pgvector** 为准；SQLite 仅单人开发。
- `REDIS_URL` 可选。队列适配器 `push`/`pop`，`claim_next` 先 pop 再 SQL 兜底。语义仍是槽位 + `awaiting_gpu`。无 Redis 时测试与本机不坏。
- 版本号按合同阶段打 tag。产品常量 `PRODUCT_VERSION` 现为 **0.2.0**（覆盖 H5.8–H5.174），与 Git tag 对齐，避免永远口头「H5 已齐、版本 0.1.0」。

***

## 25. 学位轴挂枢纽（禁止 /degree）

表 `degree_milestones` 挂 `direction_id` + 可选 `manuscript_id`。入口在方向抽屉与手稿页。种类 `proposal|midterm|defense`，状态 `planned|in_progress|done`。权限仍导师 / 学生两档。

仍后置：企微、ASR、H6。H3「实验排行」否决；若以后做实验注册，只做目录，不做排行红灯。

官方查重永不做。捕手不新做浏览器扩展，只打磨现有 `browser_ext/`。

***

## 26. H5.12–H5.16 本轮落地（实验室 OS 打磨，禁止新产品）

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 组会 ↔ 看板 | `ActionItem.source_line`；`GET /meetings/{id}` 的 `lines` / `task_href`；`GET .../tasks` 的 `meeting_href`；`Collab.jsx` | 新会议库、新看板页 | 纪要行 `?line=` 高亮；行动项点到 `?open=`；任务回跳同一行。同一会议再次提取相同 `(title, assignee_id)` 不重复建任务 |
| 成员职称 | `LabMembership.title`；`PATCH .../members/{user_id}`；成员 / 方向 / 驾驶舱 | 第三角色、独立档案页 | 展示职称或年级。导师可改任何人；成员可改自己。权限仍两档 |
| 手稿评论 | `GET/POST .../comments` 的 `href`；`?comment=`；`manuscriptHref` / `noticeHref` | 新批注产品 | 写作页与时间线落到同一条评论并滚动 |
| 捕手 | `browser_ext/options.html` `popup.js` | 第二个扩展 | 邮箱登录拿 JWT；401 刷新；三段校验；知网 / IEEE 弱选择器，缺字段仍降级 |
| 可观测性 | `/health` `worker`；`X-Request-Id`；`PRODUCT_VERSION` | 新监控台、第二工人语义 | 响应带回请求号；工人 last tick / lag / 文件锁；版本 0.2.0 |

***

## 27. H5.17–H5.21 功能闭环（禁止新产品）

规划四段（写回 / 方法卡 / 注入 / 跳转 / 评测 / 底座 / 学位轴 / 纪要行 / 职称 / 评论定位 / 捕手登录 / 请求号）已齐。本轮只补**现有对象上半截的闭环**：

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 通知 / 周报 | `notifications.payload_json`；`fanout_activity` 透传 payload；`GET /notifications` / 首页未读 / `build_digest` 的 `href` | 第二套待办 | `manuscript.commented` 带 `comment_id` 与 `?comment=`；优先服务端 `href` |
| 顶栏跳转 | `GET /labs/{id}/jump` | 新搜索页 | 除文献/手稿/任务外，可落到方向枢纽、组会 Drawer、数据集详情 |
| 学位轴 | 方向抽屉 / 手稿页；`milestone.created` 进学生通知 | `/degree` | 记下时可选手稿；归属人或导师改状态 |
| 引导 | `build_lab_guide` `pick_direction` | 新 onboarding | 学生「选定研究方向」看自己是否已选，不被他人选中带过 |
| 管理 | `GET /admin/usage` 加 `created_at` / `worker` / `version` | 新监控台 | 管理页看见工人 lag 与用量时间 |
| 方向深链 | `noticeHref` / `object_open_href` | `/atlas` | `object_type=direction` 落到 `/labs/:id/directions/:id` |

***

## 28. H5.22–H5.26 证据链与指挥面（禁止新产品）

规划五段闭环已齐。本轮只补**现有对象上的证据链与指挥面**：声称从哪来、注入文献回哪一页、首页看见风险、主张能进复现、对比/驾驶舱/数据集同一套深链。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 复现声称 | `create_reproduce_run` / `execute_reproduce_run`；`claimed_metrics_from_card`；DTO `claimed_metrics_source` | 把声称写入 `actual_metrics`；新指标库 | 用户未填时从最好方法卡的 `metrics` 列表解析 `{name: value}`（兼 `"name: value"`）。显式 `claimed_metrics` 不覆盖。来源标 `user` / `method_card` |
| 发现式注入 | `_attach_injected` 对 `injected.literature` 再盖 `stamp_items_open_href` | 空跑仍写 `lab_context.json`；改主图 | 详情优先 `injected.literature` 时仍带 `href` / `last_page_no` |
| 首页 / 驾驶舱 | `GET /me/home`、`GET /labs/{id}/cockpit` 加 `milestones_upcoming`、`queue` | `/degree`、新监控台 | 未完成节点（学生看自己、导师看全组，约 5 条，过期提前）；`queue.awaiting_gpu` / `blocked` 链到现有复现列表 `?status=`。方向抽屉记下节点可填 `due_at` |
| 主张追踪 | `GET /manuscripts/{id}/claims` 每条引用加最近复现 | 新主张表 | 有则 `reproduce_run_id` / `reproduce_status` / `/reproduces/{id}`；无则 `/labs/{lab}/reproduces?literature_item_id=` |
| 对比 / 流 / 数据集 | `Literature.jsx` 对比卡；`Cockpit.jsx` SSE；`dataset_dto.usage_count` | 新对比引擎、第二套 SSE、新登记处 | 所选文献「去复现」；驾驶舱流断开可重连（与任务页同一 `StreamDropped`）；列表/详情带用量；创建可填说明 |

***

## 29. H5.27–H5.29 体验收口（禁止新产品）

证据链与指挥面字段已齐。本轮只改**现有页怎么看、怎么点**：空态不占首屏、截止日期能读、复现入口同一套查询串。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 待处理条 | `AttentionBar`；`milestone_dict` 加 `due_label` / `days_left` / `kind_label` | 永远占一张空卡；`/degree` | 无节点且不等 GPU / 不缺数据时不渲染。过期用危险色；剩余天数中文 |
| 复现入口 | 主张卡、对比卡、阅读器「发起复现」、复现创建表 | 第三套入口 | 一律 `/reproduces?literature_item_id=`；创建表预选 URL 中的文献 / 数据集 |
| 数据集用量 | 列表数字；详情 extra；`GET .../research-runs?dataset_id=` | 新监控 / 新登记处 | 点用量进已筛发现式或复现列表；详情引用带状态标签 |

***

## 30. H5.30–H5.32 阅读 / 写作 / 语言疏密（禁止新产品）

指挥面与证据链入口已齐。本轮只改**现有对象页的疏密**：侧栏一次只看一块，写作选题能回阅读器，章节引用带上次页。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 阅读器侧栏 | `LiteratureReader.jsx`；`?panel=card\|notes\|tutor` | `/tutor`、新阅读产品 | 在读条始终可见。有 `?note=` 先开笔记，有 `?session=` 先开辅导，否则方法卡。当前页笔记置顶 |
| 写作工作台 | `Writing.jsx` 同步 `?lit=` / `?ms=`；已选文献芯片 | 第三条写论文 | 芯片回阅读器上次页，并可「去复现」。写回成功条回手稿 |
| 章节引用 / 语言 | `POST .../writing/section` 的 `citations` / `literature` 盖 `href`；语言写回结果条 | 新笔记产品 | 引用卡带上次页与去复现；语言回阅读器带 `?panel=notes` |

***

## 31. H5.33–H5.35 手稿 / 知识库 / 方向疏密（禁止新产品）

阅读器与写作页已分栏。本轮只改**手稿侧栏、知识库、方向枢纽/图谱**怎么看：一次只开一块，问答引用能回上次页，方向文献能去复现。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 手稿侧栏 | `ManuscriptEditor`；`?tab=claims\|cites\|comments\|review\|versions` | 新批注产品、`/degree` | 有 `?comment=` 先开评论，否则主张。节点条常驻。Overleaf 挂版本栏 |
| 知识库 | `KB.jsx`；`?tab=docs\|ask\|terms`；`query_kb` 盖 `href` / `reproduce_href` | 新 RAG / 独立笔记 | 问答或综述后落到问答栏。引用卡回上次页并可去复现 |
| 方向 / 图谱 | `direction_overview.literature`、图谱文献节点 | 新图谱 / 新登记处 | 盖 `reproduce_href`；抽屉与对象摘要「去复现」落到现有复现列表 |

***

## 32. H5.36–H5.38 指挥面 / 任务 / 跳转疏密（禁止新产品）

对象页分栏已齐。本轮只改**首页、驾驶舱、发现式、顶栏/周报**怎么点：在读能去复现，注入文献同一套入口。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 首页 / 驾驶舱 | `GET /me/home` readings；cockpit 卡片与成员进展 | 新待办页 | 盖 `href` / `reproduce_href`（成员页用该生成员上次页，不盖成查看者自己的页）。手稿带 `writing_href` |
| 发现式 | `_attach_injected` / `_stamp_run_literature` | 改 `build_graph.py` | 列表与详情注入文献「去复现」 |
| jump / 周报 | `GET .../jump` 文献项；`build_digest` 文献/笔记事件 | 新搜索 / 新周报 | 盖 `reproduce_href`；周报表可去复现 |

***

## 33. H5.39–H5.41 日常主入口闭环（禁止新产品）

指挥面芯片已齐，但学生每天打开的**文献库主表、通知、统计、手稿列表**仍是死表：首页能去复现，库表不能；周报有 `reproduce_href`，铃铛没有。本轮只补这些现有入口，不新开检索/待办/统计台。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 文献库 | `GET .../literature`；`recommend_for_lab` 的 `lab_unread` / `lab_related`；对比 `items` | 新文献产品 | 主表与推荐已入库行盖 `href` / `reproduce_href`；对比卡走同一字段 |
| 通知 / 动态 | `enrich_literature_open_links` | 第二套待办 | 文献/笔记对象一并盖 `reproduce_href`（须带 `lab_id`）。手稿 `?comment=` 深链不变 |
| 统计 / 手稿列表 | `_stats_payload`；`_ms_dict` | 新统计台、排行红灯、第三条写作 | 方向行进枢纽/本方向文献/手稿；成员行进驾驶舱成员页。手稿列表带 `writing_href` |

***

## 34. H5.42–H5.44 指派 / 数据集 / 冲突可理解（禁止新产品）

文献库与统计已能点进对象。本轮只补**首页指派、数据集、整篇戳冲突**仍薄的地方：纪要行在首页看不见，关联文献不能去复现，409 只丢一句空话。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 首页指派 / Agent 卡 | `GET /me/home` 的 `tasks` / `runs` / `reproduces` | 新待办、新会议页 | 任务复用看板 `_task_dict`（`meeting_href` / `source_line`）。发现式/复现带对象 `href` / `run_href` |
| 数据集 | `literature_rows`；`usages` | 新登记处 | 关联文献盖 `reproduce_href`；用量行带 `/runs/` `/reproduces/` |
| 手稿冲突 | `PATCH .../manuscripts`、写回、恢复的 409 | 选区锁、OT、第三角色 | `detail` 为 `{message, href, updated_at}`，文案仍含「手稿已被更新」。页内刷新，不另开协同页 |

***

## 35. H5.45–H5.47 成员 / 铃铛 / 协作对象可跳（禁止新产品）

首页、文献库、统计已能点进对象。本轮只补**成员表、顶栏铃铛、组会/资料/日志/jump**仍薄的地方：导师在成员页看不见驾驶舱，铃铛只有标题，资料与日志能被通知打开却搜不到。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 成员表 | `GET /labs/{id}/members` | 独立档案、第三角色 | 行带 `href` 进驾驶舱成员进展；有方向则 `direction_href` 进枢纽。权限仍两档 |
| 顶栏铃铛 | `NoticeBell`；仍走 `GET /notifications` | 第二套待办、嵌套整卡 Link | 标题进 `noticeHref`；文献通知另出「去复现」，与通知页同一字段 |
| jump / 协作列表 | `GET .../jump`；组会 / 资料 / 工作日志列表 | 新搜索站、新会议/资料产品 | 手稿项补 `writing_href`。资料/日志可被 jump 落到 `?open=`。列表 DTO 带对象 `href`；组会带 `action_count` |

***

## 36. H5.48–H5.50 引擎可靠（禁止新产品）

对象深链已齐。本轮只补**发现式收尾、组内上下文、复现改进**仍薄的地方：`run_graph` 正常结束会 `sys.exit(0)` 却被当成崩溃；组内文献只躺在作业根目录；`/improve` 只是把阶段打回 `implement` 再跑同一轮。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 发现式收尾 | `execute_research_run` | 改 `build_graph.py` 边语义 | 接住 `SystemExit`：0/空为 `succeeded`，有 `interrupt_after` 为 `paused`，非 0 为 `failed`。禁止当未捕获崩溃 |
| 组内上下文 | `write_lab_context`；注入后的 `cfg.question` | 新 RAG、改六子图顺序 | 根目录与 `workspace/` 同写 `lab_context.json` / `linked_objects.md`。问题旁注要求引用已关联标题，未用须写「未使用：标题」。执行前断言文件存在 |
| 复现改进 | `advance_once` 的 `fix`/`improve`；`POST .../improve` | 第三条复现引擎；claimed→actual | 改进带上一轮 `diagnosis` + logs 再实现。平台把 `current_stage` 设为 `improve`，执行不再从 `ingest` 重来 |

***

## 37. H5.51–H5.54 生成与指挥可理解（禁止新产品）

引擎收尾已齐。本轮只让导师和学生看懂**弱方法卡、发现式走到哪、本月数字、组内待认领**。全部挂现有页。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 方法卡空态 | `card_empty_state`；阅读器 `GET .../detail` 的 `card_empty` | 新分析台、第三条方法卡 | `abstract_only` / `ungrounded` / `unusable` 用现有警告条写清下一步：上传可复制 PDF / 再生成方法卡。无 PDF 不得假装全文卡 |
| 发现式进度 | `subgraph_progress` | 改 `build_graph.py` 边 | 优先数 `states/step_NNNN_name.json`（有文件即该子图已完成）；无状态文件才回退 `messages.json` 字符串。详情带 `steps_done` / `progress_source` |
| 统计可点 | `_stats_payload.this_month` `*_href`；`by_year.href` | 新统计台、排行红灯 | 本月开始阅读进文献 `?reading_status=reading`；本月在投进手稿 `?status=submitted`；本月问答进知识库。年份进文献 `?year=` |
| 待认领 | `GET .../tasks?assignee=none`；首页/驾驶舱 `unassigned` | 新待办产品 | 学生可见组内未指派并可认领自己。AttentionBar 一条「待认领 N」进同一过滤 |

***

## 38. H5.55–H5.56 生产硬化（工程，不是新产品）

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 探活 | `GET /health` | 新监控台 | `db.ok`；`redis.configured` / `ok`（未配置则为空）。库不可用时 `ok=false` 且 503。Compose `api`/`postgres` 加 readiness |
| CI | `.github/workflows/fengsuan.yml` | TypeScript 全站重写 | 并行 `web_next` `npm ci && npm run build`。可选 Playwright（`FENGSUN_E2E=1`）覆盖登录→驾驶舱、文献 `?page=`。`pytest.mark.nightly` + `FENGSUN_NIGHTLY=1` 证明工人 stub 图能收尾 succeeded |

***

## 39. H5.57–H5.59 诚实指挥（禁止新产品、禁止 H6）

生成与探活已齐。规划里还薄、且能改现有对象的只剩三处：发现式长跑在 GPU 关闭时被记成 `llm`/`docker`；数据集列表看不见详情上的分项用量与关联文献；无 PDF 时复现只报失败、不像阅读器那样说下一步。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 发现式等 GPU | `execute_research_run`；`GET .../research-runs?status=`；`lab_queue.research_awaiting_*` | 改图边；无 GPU 仍开跑并伪造指标 | 问题/注入文献方法卡含 gpu/cuda，且默认 GPU 为 off：不启动图，记 `awaiting_gpu`。异常文案含 GPU 同样收口。复现队列计数口径不变 |
| 数据集列表 | `dataset_dto` + `Datasets` 表 | 新登记处、排行红灯、再撒「去复现」 | 列表带 `research_count` / `reproduce_count` 与现有筛选 href；说明回退 `source_note`；关联文献标题进阅读器上次页 |
| 复现引导 | `_enrich_repro.guide` | 第三条复现引擎 | 无 PDF / 仅摘要 / 扫描件用现有警告条 + 阅读器 `?panel=card`。`blocked` / `awaiting_gpu` 仍走数据集与管理页 |

***

## 40. H5.60–H5.62 等 GPU 闭环与文献列表诚实（禁止新产品、禁止 H6）

H5.57–H5.59 已能停在 `awaiting_gpu` 并说下一步。还薄的是闭环：停住之后只能取消重开；管理页改 GPU 看不见排队；文献库主表仍看不出哪篇没有可复制 PDF。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 恢复 | `POST /research-runs/{id}/resume`；`POST /reproduce-runs/{id}/resume` | 新队列页；GPU 仍 off 却启动图或伪造指标 | `awaiting_gpu` / `paused` 恢复为 `pending` 并 `enqueue_job`。无工人时当场再执行。GPU 仍 off 则再次 `awaiting_gpu`。复现恢复不是「改进一轮」，不得把声称写入实测 |
| 管理 GPU | `GET|PUT /admin/gpu-profiles`；Admin GPU 栏 | 新监控台、实验排行 | 回 `awaiting_research` / `awaiting_reproduce`。模式改为 internal/external 时把全站 `awaiting_gpu` 改回 `pending` 并入队，返回 `released_*`。不自动开跑、不伪造指标 |
| 文献库列表 | `GET .../literature`；库表「全文」列 | 新文献产品、再撒「去复现」 | 批量盖 `has_pdf` / `parse_status` / `card_empty` / `card_href`（`?panel=card`）。`?has_pdf=0` 看待上传。点全文进阅读器，不新开分析页 |

***

## 41. H5.63–H5.65 指挥面全文与等 GPU 动态（禁止新产品、禁止 H6）

库表已能看出无 PDF，但首页在读、推荐未读、驾驶舱预览仍当全文已齐；复现列表要进详情才看见引导；等 GPU 不进动态，导师会当成任务消失。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 指挥面文献 | `GET /me/home` readings；`recommend_for_lab` 未读/相关；驾驶舱卡片与成员进展 readings | 新文献产品、再撒「去复现」 | 复用 `stamp_list_card_state`。行上「无全文」进 `card_href`（`?panel=card`） |
| 复现列表 | `_attach_repro_links.guide` | 第三条复现引擎、N+1 | 列表批量盖与详情同一套 `guide`；点引导回阅读器。`blocked` / `awaiting_gpu` 口径不变 |
| 等 GPU 动态 | `mark_research_awaiting_gpu`；复现收尾 verb；`TEACHER_VERBS`；`VERB_LABEL` | 写成 `run.failed`；新待办页 | verb 为 `run.awaiting_gpu` / `reproduce.awaiting_gpu`。深链仍 `/runs/{id}` `/reproduces/{id}`。不发邮件 |

***

## 42. H5.66–H5.68 动态落地、诊断可执行、指挥面找得到等待（禁止新产品、禁止 H6）

等 GPU 已进动态，但驾驶舱/周报仍靠前端猜 href；发起人自己收不到铃铛；复现诊断停在「尚未实测」没有可执行下一步；外部 GPU 投递不带诊断；顶栏 jump、对比、主张、首页任务卡仍当全文/队列已齐。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 动态深链 | `serialize_event`；`fanout_activity` | 新收件箱；`EMAIL_VERBS` 加等 GPU | 事件带 `object_open_href`。`run.awaiting_gpu` / `reproduce.awaiting_gpu` 也通知发起人。不发邮件、不写成 `run.failed` |
| 复现诊断 | `diagnose` / `write_gap_report` / `_dispatch_external`；`diagnosis.json`；详情/列表 `diagnosis` | 第三条复现引擎；把声称写入实测 | `next_step` + `suggested_action`（`improve` / `resume` / `need_data` / `ask_admin_gpu` / `upload_pdf`）。差距报告 `## 下一步`。外部 POST 带 `improve_hint` 与 claimed，远端回声称则本地清空 |
| 找得到等待 | `GET .../jump`；compare `items`；claims / citations；home `runs` | 新搜索 / 新主张产品 / 再撒「去复现」 | jump 能按问题/文献题/「GPU」落到等待中的发现式与复现。对比、主张、引用、jump 文献盖 `card_empty`。首页任务卡带 `error_class` / `next_step` |

***

## 43. H5.69–H5.71 等待作业点得开（禁止新产品、禁止 H6）

计数已经诚实，但首页/驾驶舱只说「等 GPU 3」，管理页只说数字，点不开是哪一条；发现式详情没有复现那套结构化下一步；驾驶舱 Agent 标签没有 href。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 实验室队列 | `lab_queue.items`；AttentionBar | 新队列页；改 `awaiting_gpu_href` 口径 | 每条等待/缺数据任务带 `href` / `next_step`。复现等 GPU 计数与 href 仍只计复现 |
| 管理 GPU | `GET|PUT /admin/gpu-profiles` `waiting` | 新监控台、排行红灯 | 全站等待作业可点进 `/runs/{id}` `/reproduces/{id}`。开启 GPU 后 `waiting` 清空并 `released_*` |
| 发现式诊断 | `_run_dict.diagnosis`；cockpit `agents`；digest / 通知 `next_step` | 改图边；写成失败；空任务写工件 | `ask_admin_gpu` / `resume`。按状态派生，不往发现式作业目录写新文件。Agent 标签带 `href` |

***

## 44. H5.72–H5.74 开启 GPU 后闭环（禁止新产品、禁止 H6）

等待作业已经点得开，但管理员开启 GPU 后发起人不知道；统计和成员进展仍当队列为空；待处理只能进详情再点恢复。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 释放通知 | `release_awaiting_gpu`；`run.released` / `reproduce.released` | 新收件箱；`EMAIL_VERBS`；写成 `run.started`/`run.failed` | 入队后通知发起人与导师。深链仍 `/runs/{id}` `/reproduces/{id}`。不发邮件、不在本页开跑 |
| 统计与成员 | `_stats_payload`；`GET .../cockpit/members/{id}` `agents`；通知页 `next_step` | 新统计台、排行红灯 | 等 GPU 计数点进已筛列表。成员进展列出该生任务。铃铛/通知页带下一步 |
| 就地恢复 | `lab_queue.items.can_resume`；发现式/复现列表 | 新队列页；GPU 仍 off 却伪造指标 | 待处理与列表「恢复」走现有 `POST .../resume`。GPU 仍关则再次 `awaiting_gpu` |

***

## 45. H5.75–H5.77 工人未开也可闭环（禁止新产品、禁止 H6）

管理员开启 GPU 后任务变 `pending`。测试与许多本机部署 `FENGSUN_WORKER=0`，学生只看到「已入队」，待处理条因计数归零而消失，恢复不执行。方向枢纽与阅读器看不到这些作业。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 无工人恢复 | `job_can_resume`；`job_next_step`；`POST .../resume`；`lab_queue.items` | 新队列页；GPU PUT 自动全跑；改 awaiting 计数/href | `pending`/`queued` 且工人未开时可恢复并当场执行。计数与 `awaiting_gpu_href` 口径不变 |
| 方向与数据集 | direction `overview.agents`；dataset `usages` | `/atlas`；新数据集产品 | 复用驾驶舱 `_agent_items_for_users`。用量行带 `next_step` / `can_resume` |
| 阅读器与跳转 | `build_literature_detail.latest_reproduce`；jump；home unread | 新阅读产品；再撒「去复现」 | 最近复现可恢复。jump「入队」落到 pending。首页未读展示 `next_step` |

***

## 46. H5.78–H5.80 活状态抢回指挥面（禁止新产品、禁止 H6）

工人未开时学生已能在待处理恢复，但管理页 `waiting` 清空、统计等 GPU 归零、旧「等 GPU」通知仍叫人开 GPU，导师以为队列已空。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 管理与统计 | `GET\|PUT /admin/gpu-profiles` `pending_without_worker`；stats `pending_*` | 新监控台；把 pending 写进 `waiting`；改 awaiting 计数/href | 工人未开才计数。`waiting` 仍只等 GPU。统计卡进 `?status=pending` |
| 通知与跳转 | `stamp_job_live`；notifications；jump | 新收件箱；改历史 verb | 用当前作业状态覆盖 `next_step` / `can_resume`。旧等 GPU 通知在入队后改为「工人未开」 |
| 文献 / 周报 / 探活 | literature list `latest_reproduce`；digest；`/health` `worker.next_step` | 新阅读产品；再撒「去复现」；新监控台 | 库表可恢复最近复现。周报行跟活状态。探活写清工人未开 |

***

## 47. H5.81–H5.83 恢复结果跟活状态（禁止新产品、禁止 H6）

指挥面已经能点恢复，但一律 toast「已请求恢复」：GPU 仍关、当场跑完、仍在排队看起来一样。按钮无防连点，下一步永远灰色，复现详情只在 `awaiting_gpu` 才给恢复。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 诚实反馈 | `job_feedback`；run / repro / queue / notice / list DTO | 新 toast 产品；改 awaiting 计数/href | `feedback: {tone, title, next_step, can_resume}`。标题：仍在等 GPU / 仍缺数据 / 可当场再跑 / 已在队列 / 正在运行 / 已暂停 / 已跑完 / 仍失败 / 已取消 |
| 恢复交互 | `ResumeJob`；`tellFeedback`；AttentionBar `onResume` | 新队列页；GPU PUT 自动全跑；首页再 POST 一次 | 忙碌防连点。toast 跟返回 `feedback.tone`。`onResume` 只刷新查询 |
| 着色与详情 | `FeedbackStrip`；`runWaitHint`；复现详情恢复 | 暗色主题；新 IA；再撒「去复现」 | 金=warning、红=error、玉=success。复现详情看 `can_resume`，含工人未开的 pending |

***

## 48. H5.84–H5.86 写操作跟活状态（禁止新产品、禁止 H6）

恢复已经诚实，但创建发现式静默跳转、创建复现只报英文状态、暂停/取消无提示、开 GPU 一律「已重新排队」。工人未开时学生以为任务已在跑。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 创建 | `POST .../research-runs` / `reproduce-runs` 已有 `feedback` | 发现式创建当场开跑；改 awaiting 计数/href | 创建 toast 走 `tellFeedback`。工人未开发现式仍只入队，文案「可当场再跑」 |
| 暂停取消改进 | 现有 pause / cancel / improve DTO | 新工具条；隐藏始终可见的按钮 | toast 跟 `feedback`。按钮忙碌防连点 |
| 开 GPU 与额度 | `GET\|PUT /admin/gpu-profiles` `feedback`；429 `next_step` | 新监控台；PUT 当场全跑 | 工人未开开 GPU 为 warning「已重新入队」。额度错误写出下一步 |

***

## 49. H5.87–H5.89 写操作守卫与活状态不被盖住（禁止新产品、禁止 H6）

发现式恢复不校验状态：运行中/已跑完/已取消也会被打回 pending。等 GPU 点暂停却报「任务已结束」。SSE 快照和旧 `diagnosis.json` 会把「等 GPU」盖在已入队任务上。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 恢复守卫 | `job_can_resume`；`POST .../resume` | 运行中再入队；伪造指标 | 仅 awaiting / blocked / 无工人 pending / 发现式 paused·failed。非法 409。缺数据可恢复重试 |
| 暂停取消 | `job_can_pause` / `job_can_cancel`；409 文案 | 新工具条；藏始终可见按钮 | 等 GPU 暂停写清「无需暂停」。按钮禁用但可见 |
| 流与诊断 | research SSE snapshot；`diagnosis_is_live` | 新监控台；改图边 | 快照带 `feedback`。旧 GPU 诊断在入队后丢弃 |

***

## 50. H5.90–H5.92 改进守卫、额度与驾驶舱队列（禁止新产品、禁止 H6）

改进一轮不校验状态：已取消/运行中也会被打回 pending 再跑。已失败/已跑完再恢复或改进不占重型槽，能和第二条任务叠在一起。驾驶舱成员进展漏掉 `queued`。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 改进守卫 | `job_can_improve`；`POST .../improve` | 第三条复现引擎；藏按钮 | 运行中/已取消 409。`can_improve` 进 DTO |
| 再入队额度 | `ensure_reenter_heavy`；resume / improve | 改 HEAVY 集合；PUT 当场全跑 | 从 succeeded/failed 再入队时过配额 429 + `next_step` |
| 驾驶舱队列 | cockpit `_agent_items_for_users` | `/atlas`；新 Agent 页 | 发现式/复现含 `queued`，发现式含 `blocked` |

***

## 51. H5.93–H5.95 开跑互斥与查询减负（禁止新产品、禁止 H6）

工人认领是原子的，HTTP 恢复/改进把状态打回 `pending` 后再 `execute_*`，原先只做 `status != running` 再写成 running，连点会开跑两次。额度按表各 COUNT 两次。SQLite 额度锁只在进程内。发现式 SSE 在 `pending` 空闲时每拍读 `states/` 与消息。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 开跑互斥 | `try_begin_execute`；`execute_research_run` / `execute_reproduce_run` | 新队列；改 `claim_next` 槽位语义；`execute_token` 列 | HTTP 仅从 pending/queued/paused CAS 到 running。败者立即返回。工人 `already_claimed=True` |
| 额度查询 | `count_heavy`；`lab_quota_guard` | 新监控台；第二工人产品 | 每表一次 SUM/CASE。SQLite 另 `UPDATE labs SET code=code` 拿写锁。数字口径不变 |
| 空闲 SSE | `_sse_loop` research | 新流产品 | pending/queued 等且 `status|updated_at|error_class` 未变则复用上次快照。`running` 仍读盘 |

***

## 52. AI 成熟度 Era A–C（禁止新产品、禁止改图边、禁止 H6）

H5.8–H5.95 把指挥面做诚实了，但两套 AI 仍是提示词薄封装：平台 `llm.chat` 生成后几乎不落地；发现式不读 `lab_context.json`；复现 stub 一律 `docker`。学生路径不变（读 → 卡 → 辅导/对比 → 写作 → 发现式/复现）。变的是出口合同：能落地必须落地，落不了必须打标。

| Era | 切片 | 改哪个现有对象 | 禁止 | 本刀 |
| --- | --- | --- | --- | --- |
| A 平台落地闸 | H5.96–H5.104 | `analysis.py` compare/tutor；writing/KB；`citation_graph`；recommend/kb 分；vision 凭据 | 第三条写作；新 RAG；`/tutor` 页 | 已落地 |
| B 发现式读得到组内对象 | H5.105–H5.112 | `chatbot.format_prompt`；监督提示词；`openhands_adaptor`；收尾缺 `plan.md` 不得 succeeded | 改 `build_graph.py` 边；改六子图主文件 | 已落地 |
| C 复现实现诚实 | H5.113–H5.118 | `paper_reproducer/pipeline.py` + `/improve` | 第三条复现引擎；claimed→actual | 已落地 |
| E 组内证据脊柱 | H5.119–H5.126 | 文献详情/对比/辅导/写作/发现式注入/驾驶舱 | 新实验台；排行榜；claimed→实测 | 已落地 |
| F 证据写回其余对象 | H5.127–H5.134 | 图谱/方向枢纽/主张/KB/首页/发现式 sidecar/方向写回 | 新产品页；排行榜；Era D | 已落地 |
| G 指挥面与生成出口吃证据 | H5.135–H5.142 | 模拟评审/周报/统计方向/进展/引导/发现式收尾闸 | 官方查重；排行榜；改图边；Era D | 已落地 |
| H 写回/通知/列表过滤吃证据 | H5.143–H5.150 | 手稿保存/通知/文献过滤/复现诚实过滤/Overleaf sidecar/语言打标 | 新产品页；写进 main.tex；排行榜；Era D | 已落地 |
| I 证据口径闭合 | H5.151–H5.158 | 实测优先/尚无实测含 blocked/题名点名/首页跳转检索/手稿过滤 | 新产品页；改 awaiting href；Era D | 已落地 |
| J 证据出口补齐 | H5.159–H5.166 | sidecar 点名/语言点名/主张栏/驾驶舱统计/周报/发现式闸读 lab_context | 新产品页；写进 main.tex；排行榜；改图边；Era D | 已落地 |
| K 证据指挥面补齐 | H5.167–H5.174 | 未点名闸/动态进展/方向/图谱/引导/写作 KB | 新产品页；改图边；排行榜；Era D | 已落地 |

仍后置（Era D）：恢复 `ALTER_PLAN` 边、企微 / ASR / H6、独立 Agent 台、官方查重。

***

## 53. H5.96–H5.98 平台生成落地闸首刀（禁止新产品、禁止 H6）

方法卡不落地 `metrics`，短于 12 字自动过关，复现声称可灌进无摘录数字。对比缺出处时用整表顶 `gap_md`。辅导把最好卡塞进 prompt，答案零校验。企微 / ASR / `org_id` 仍后置。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 方法卡指标 | `assert_excerpt` / `stamp_ungrounded`；`ground_card` 覆盖 metrics | 新分析库；把声称当实测 | 非空摘录必须是原文子串。无依据 metrics 不进 `claimed_metrics_from_card` |
| 对比综述 | `compare_items`；`POST .../compare` | 第三条写作；用 `table_md` 冒充 gap | 句子须含所选 `item_id`。裁空后 `gap_md=""` |
| 精读辅导 | `tutor_item`；`POST .../tutor` | `/tutor` 页 | 引号摘录与指标数字须落在本页或最好卡已落地摘录。否则 `ungrounded` 并给出 `next_question` |

***

## 54. H5.99–H5.104 平台生成落地闸后半（禁止新产品、禁止 H6）

大纲只吃题录。章节 / KB 生成不校验本轮命中。图谱按 `created_at` 取最新卡。推荐与 pgvector 分假装 1.0。发现式 vision 复用 chat 密钥。超预算仍走 stub 假装成功。企微 / ASR / `org_id` 仍后置。不改 §53。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 大纲方法卡 | `literature_card_hints` / `enrich_outline_with_cards`；`POST .../writing/outline` | 第三条写作 | 最好卡主张/方法/限制写入大纲。无卡 `notes=未结合方法卡，仅题录大纲` |
| 章节 / KB 落地 | `write_section`；`kb_query`；`summarize_lab_kb` | 新 RAG | 数字与引号摘录须为本轮 hits / 题录子串。否则 `ungrounded` 并附 `KB_GROUND_NOTE` |
| 引用图谱 | `citation_graph` | 新图谱产品 | `best_cards_for_items` 提供 `source_page` 与 related，禁止最新弱卡 |
| 推荐分 | `recommend_for_lab`；`query_kb` pgvector | 新推荐页 | DTO `embed_fallback=hash`。`score = 1/(1+dist)`，禁止全 1.0 |
| 额度与 vision | `assert_lab_quota`（chat 先于 stub）；`resolve_llm(..., purpose="vision")` | 改图边；假装 OK | 超预算 429 + token `next_step`。`cfg.llm.vision` 独立解析，失败回退 chat |

***

## 55. H5.105–H5.112 发现式读得到组内对象（禁止改图边、禁止改六子图主文件）

`lab_context.json` 只写在作业目录，OpenLens 提示词看不见。监督计划吃不到数据报告。`file_manager is None` 时整段 OpenHands prompt 被 `continue` 跳过。文献按工具次数停。图 `sys.exit(0)` 即使没有 `plan.md` 也标完成。企微 / ASR / `org_id` 仍后置。不改 §53–§54。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 提示词注入 | `inject_job_context` / `format_prompt` | 改六子图主文件 | `{lab_context}` / `{data_report}` / `{fail_log}` 从作业目录读入 |
| 监督计划 | `supervisor_plan.md` / `supervisor_alter_plan.md`（非 medical） | 改 medical 专稿；打开 ALTER_PLAN 边 | 无数据报告不得装已剖析。alter 必须点名失败日志路径 |
| OpenHands | `compose_openhands_prompt` | 新编码引擎 | `file_manager is None` 仍执行 prompt。`lab_context.json` 前置 |
| 文献搜索 | `literature_search.md`；`dedupe_paper_dicts` | 改节点名 / 改 min_tool_call 图条件 | 覆盖+必读全文。工具次数只作上限。DOI/标题去重 |
| 收尾闸 | `research_output_gate`；`execute_research_run` | 改图边 | 子图已启用则缺 `literature_review.md` / `plan.md` 不得 succeeded。未点名标题须「未使用：」 |
| 数据报告 | `data_report.md`（management/materials/general） | 改 medical 专稿 | 去掉 Medical 串台。列名必须来自 `data_show` 抽样 |

***

## 56. H5.113–H5.118 复现实现诚实（禁止第三条引擎、禁止 claimed→actual）

CI / stub 一律 `error_class=docker`，详情可能被读成容器故障。无实测或把声称原样写回仍可能走到 succeeded。`actual_metrics.json` 任何 dict 都收。改进一轮只带一句笼统 improve。企微 / ASR / `org_id` 仍后置。不改 §53–§55。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 失败分类 | `try_implement`；`_dispatch_external`；`fallback_not_run_class` | 第三条复现引擎 | stub / no_docker / no_gpu / parse 分列。始终 `docker` 仅保留给真实容器/OpenHands 失败 |
| 未跑不得成功 | `diagnose`；`advance_once`；复现详情 | 声称写入实测；「成功完成实验」 | 无实测或 `actual==claimed` → `kind=not_run` 且 `failed`。详情禁止成功文案 |
| 实测小模式 | `normalize_actual_metrics` | 新指标库 | 只收「指标名→数字」。列表/嵌套/非数字整份拒绝，按未跑 + `parse` |
| 改进吃诊断 | `card_implement_brief`；`improve_hint`；`load_improve_diagnosis` | 第三条写作 | prompt 点名 problem / methods / metrics。`/improve` 读上一轮 `diagnosis.json` |
| 外部 GPU | `_dispatch_external` | 改 GPU PUT 只入队语义 | 回调抄声称仍清空实测。投递失败 `no_gpu` |

***

## 57. H5.119–H5.126 组内证据脊柱（禁止新产品、禁止排行、禁止 claimed→实测）

诚实闸已经分得清 stub / 未跑，但复现结果停在任务详情：对比、辅导、大纲、发现式、驾驶舱互相看不见。世界级实验室 OS 不是再开实验台，而是同一条证据在现有对象上流转。企微 / ASR / `org_id` 仍后置。不改 §53–§56。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 证据 DTO | `reproduce_evidence`；文献 `latest_reproduce` | 新实验台 | 带 `measured` / `actual_metrics` / `honest`。声称与实测分列 |
| 对比 | `compare_items`；`gap_md` | 第三条综述 | 每篇点名 `item_id` 写入组内实测或「尚无实测」 |
| 辅导 | `tutor_item`；`ground_tutor_answer` | `/tutor` 页 | prompt 带证据。仅实测数字可进落地语料，声称不能过关 |
| 写作 | `literature_card_hints` / outline / `write_section` | 第三条写作 | 大纲出现「复现：…」。章节 corpus 只收实测 |
| 发现式 | `build_lab_injection`；`compose_injected_question` | 改图边 | `lab_context.json` 带 reproduce。旁注禁止把声称当成功 |
| 改进 | `load_improve_lab_feedback`；`improve_hint` | 第三条引擎 | 吃精读追问与手稿批注 |
| 指挥面 | cockpit / stats `reproduce_spine` | 排行榜；改 awaiting href | 已实测 / 未跑 / 测试桩计数。等 GPU href 仍只指向复现列表 |

***

## 58. H5.127–H5.134 证据写回其余现有对象（禁止新产品、禁止排行、禁止 Era D）

脊柱已在文献详情 / 对比 / 辅导 / 写作 / 发现式注入 / 驾驶舱流转，但图谱、方向枢纽、主张、知识库、首页待处理与发现式 sidecar 仍只看见 href。世界级不是再开实验台，而是同一条证据写回这些现有对象。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§57。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 图谱 | `citation_graph` 文献节点 | 新图谱产品 | `latest_reproduce` + 图级 `reproduce_spine`。节点颜色表示诚实状态 |
| 方向 | `direction_overview` | `/atlas` | 文献行带证据；方向子集 `reproduce_spine` |
| 主张 | `GET /manuscripts/{id}/claims` | 官方查重 | `reproduce` DTO；已有实测 / 尚无实测 |
| KB | `query_kb` / `summarize_lab_kb` | 新 RAG | hits 带 `latest_reproduce`。`reproduce_gap_md` 单独字段，不并进综述 |
| 首页 | `home` + `AttentionBar` | 新监控台；改 awaiting href | `reproduce_spine`。芯片「复现未跑」进 `?status=failed` |
| sidecar | `write_lab_context` | 改图边 | 根目录与 `workspace/` 同写 `reproduce_gaps.md`。unused 扫描跳过该文件 |
| 写回 | `merge_direction_note`；对比写回 | 新方向页 | 「组内复现差距」同标题替换。对比可带 `reproduce_gap_md` |

***

## 59. H5.135–H5.142 指挥面与生成出口吃证据（禁止新产品、禁止排行、禁止 Era D）

证据已经写回图谱 / 方向 / 主张 / KB / 首页，但周报、统计方向行、模拟评审、发现式收尾与引导仍不拿这条脊柱做决定。世界级不是再开监控台，而是现有出口拒绝「未测当成功」。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§58。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 模拟评审 | `simulate_review`；`POST .../review-simulate` | 官方查重 | 正文写「成功完成实验」等而引用文献无实测 → `ungrounded_reproduce` |
| 周报 | `build_digest` / `digest_markdown` | 企微；新周报页 | `reproduce_spine`。邮件预览写出已实测 / 未跑 |
| 统计 | `_stats_by_direction`；`stats.csv` | 排行榜 | 方向行 `reproduce_measured` / `not_run`。点进现有复现列表 |
| 进展 | `GET .../progress` | 新进度页 | 文献事件 `latest_reproduce` |
| 引导 | `build_lab_guide` | 新引导产品 | `not_run>0` 时出现「尚无实测」项，href 为 `?status=failed` |
| 发现式闸 | `research_output_gate`；`dishonest_reproduce_claims` | 改图边 | 综述/计划把未测写成成功 → 不得 succeeded |
| 发现式详情 | 注入卡 / 工件表 | 新 Agent 页 | 注入行标实测或未跑；`reproduce_gaps.md` 标为差距 sidecar |

***

## 60. H5.143–H5.150 写回 / 通知 / 列表过滤吃证据（禁止新产品、禁止写进正文、禁止 Era D）

指挥面已经拒绝「未测当成功」，但手稿保存、铃铛、文献/复现列表、Overleaf 出口与语言工作台仍把证据留在别处。世界级不是再开写作闸或通知站，而是现有写回与过滤看见同一条脊柱。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§59。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 手稿 | `PATCH /manuscripts/{id}`；`_ms_dict` | 400/409 拦保存 | 正文写「成功完成实验」而引用无实测 → `ungrounded_reproduce`。仍 200 |
| 通知 | `GET /notifications`；home `unread_items` | 新通知产品 | `stamp_notification_reproduce`。文献/复现对象带 `latest_reproduce` |
| 文献库 | `GET /labs/{id}/literature?reproduce=` | 新文献站 | `measured\|not_run\|none\|awaiting`。先 stamp 再内存过滤 |
| 复现列表 | `GET .../reproduce-runs?honest=` | 改 `?status=failed` 口径 | `_repro_dict` 带 `honest`/`measured`。`status=` 语义不变 |
| Overleaf | `export-zip` / `overleaf-sync` | 写进 `main.tex` | 另加 `reproduce_gaps.md` sidecar |
| 语言 | polish / translate / expand；`_maybe_writeback` | 第三条语言引擎 | 输出含成功套话 → `ungrounded_reproduce` |
| UI | 文献/复现/通知/手稿/语言/写作 | 新堆「去复现」芯片 | 已有证据改「查看复现」。现有 Select / Alert |

***

## 61. H5.151–H5.158 证据口径闭合（禁止新产品、禁止改 awaiting href、禁止 Era D）

写回与过滤已经吃证据，但仍有口径裂缝：后一次失败盖掉已有实测；「尚无实测」漏掉缺数据；保存警告只看引用不看题名；模拟评审与保存不一致；首页/跳转/本库检索看不见警告。世界级不是再开实验台，而是同一条脊柱在现有对象上自洽。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§60。不改 `build_graph.py` 边。`not_run_href` 仍是 `?status=failed`。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 选跑 | `pick_item_reproduce_run`；`latest_reproduce_runs`；`lab_reproduce_spine` | 新实验台 | 同一文献有实测取最近实测，后一次失败不得盖掉 |
| 过滤 | `filter_rows_by_reproduce`；`?honest=not_run` | 改 awaiting href | `not_run`=尚无实测，含 blocked，不含 awaiting |
| 警告 | `manuscript_success_warning`；`merge_reproduce_hints` | 官方查重 | 题名点名的组内未测篇也打标。评审同一口径 |
| 首页/跳转 | `home` 手稿卡；`jump` | 新搜索站 | `ungrounded_reproduce`。复现卡带 honest |
| 检索 | `search_local` | 新文献站 | 本库命中 stamp `latest_reproduce` |
| 手稿列表 | `GET .../manuscripts?ungrounded=` | 新写作闸 | 只列未测当成功。不拦保存 |
| 通知 | `stamp_notification_reproduce` 手稿对象 | 新通知产品 | 铃铛/未读露出「未测当成功」 |

***

## 62. H5.159–H5.166 证据出口补齐（禁止新产品、禁止写进正文、禁止 Era D）

口径已经自洽，但仍有出口看不见题名：Overleaf sidecar 只列参考文献；语言无手稿时不点名；主张栏不警告；驾驶舱/统计/周报手稿事件不计数；发现式闸只读内存注入、不读作业目录 `lab_context.json`。世界级不是再开统计台或周报产品，而是现有出口看见同一条脊柱。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§61。不改 `build_graph.py` 边。`not_run_href` 仍是 `?status=failed`。`reproduce_gaps.md` 不得写进 `main.tex`。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| Overleaf | `manuscript_reproduce_gaps_sidecar` | 写进 `main.tex` | 引用 id + 正文点名未测题名一并进 sidecar |
| 语言 | `language_output_warning(..., lab_id=)` | 第三条语言引擎 | 无手稿时仍点名组内未测题名 |
| 主张 | `GET /manuscripts/{id}/claims` | 官方查重；新主张库 | 顶栏 `ungrounded_reproduce`。未引用、仅题名点名行 `mentioned_only` |
| 驾驶舱/统计 | 成员手稿；`_stats_payload`；`stats.csv` | 排行榜 | 手稿行 `ungrounded_reproduce`。计数 + `?ungrounded=1` |
| 周报 | `build_digest`；`digest_markdown` | 新周报 / 企微 | 手稿事件 stamp。预览写未测当成功 |
| 发现式闸 | `dishonest_reproduce_claims` | 改图边；把 sidecar 当综述 | 并读 `lab_context.json` 文献题名。仍 skip sidecar 正文扫描 |

***

## 63. H5.167–H5.174 证据指挥面补齐（禁止新产品、禁止改图边、禁止 Era D）

出口已经看见题名，但指挥面与发现式未点名闸仍有裂缝：`unused_injected_titles` 只读内存注入；驾驶舱动态 / 成员进展不 stamp 手稿警告；方向枢纽与图谱手稿节点不打标；引导只提复现未跑；写作大纲/章节与 KB 只给套话、不点名；只点名已实测篇仍可能误报「尚无实测」。世界级不是再开进度页或图谱产品，而是现有指挥面看见同一条脊柱。企微 / ASR / `org_id` / Era D 仍后置。不改 §53–§62。不改 `build_graph.py` 边。`not_run_href` 仍是 `?status=failed`。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 未点名闸 | `unused_injected_titles` | 改图边；把 sidecar 当综述 | 并读 `lab_context.json` 题名。仍 skip sidecar 正文扫描 |
| 动态/进展 | `recent_events`；`GET .../progress` | 新进度页 | `stamp_notification_reproduce`。手稿事件带 `ungrounded_reproduce` |
| 方向 | `direction_overview` 手稿行 | `/atlas` | 手稿 `ungrounded_reproduce`。动态走同一 stamp |
| 图谱 | `citation_graph` 手稿节点 | 新图谱产品 | `stamp_manuscript_honesty` |
| 引导 | `build_lab_guide` | 新引导产品 | `not_run>0` 之外，未测当成功手稿进现有 GuideList，href 为 `?ungrounded=1` |
| 写作/KB | outline / section；kb query / summarize | 第三条写作/RAG | `language_output_warning` 点名未测题名。只点名已实测不误伤 |

***

## 64. 组织入驻（产品负责人授权：开通 H6 入驻切片，禁止第二后台、禁止 Era D）

商业化缺「第一扇门」：访客不能自助进工作台，但必须能申请开通机构。本轮只做申请 / 审批 / 本机构建组，不改 §53–§63，不改 `build_graph.py` 边，不做一人多机构、插件、移动端、企微 / ASR。决策日志 D-REG / D-TEN 正文不改写；以本节目为产品授权记录。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 机构 | 现有 `orgs` 加列 | 新租户产品、第二套用户表 | `status` pending/approved/rejected/suspended；申请字段；Alembic 011 |
| 申请/审批 | `auth` + `/admin` | 独立运营站 `/ops` | `/apply`、`POST /auth/apply-org`、`GET/POST /admin/orgs*`；同一套页面按 `is_admin` 进管理 |
| 邀请 | 现有 invite | 开放注册进工作台 | 仅已开通机构可邀；注册 `org_id` = 实验室机构 |
| 建组 | `POST /labs` | 跨机构写 `org_id` | 已开通机构的导师可建本机构空库 |
| 门闩 | `accessible_labs` / `me.workbench_ready` | 未审先进文献 | 未开通侧栏锁工作台，落 `/pending` |

夹具：`platform/tests/test_org_onboarding.py`。版本号保持 0.2.0。

***

## 65. 组织入驻加深（禁止第二后台、禁止一人多机构、禁止 Era D）

§64 已能申请与审批，但仍有裂缝：已有账号无法接第二间同机构实验室邀请；运营收不到申请铃铛；停用后成员无感知；进度页不刷新；跨机构 `org_id` 可被非管理员误传。世界级不是新开运营站，而是现有邀请 / 通知 / 成员 / 管理页吃同一套机构门闩。不改 §53–§64 正文。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 邀请 | `preview` + `POST /auth/invites/{token}/accept` | 跨机构加入；开放注册 | `account_exists`；已有账号登录后接受；`org_id` 必须一致 |
| 运营铃铛 | `notify_users` → 管理员 | 企微 / 工单系统 | 申请与重新提交通知 `/admin` |
| 停用 | `review suspend/resume` | 伪造成功继续跑 | 成员通知；`workbench_ready=false`；禁止新邀请 |
| 建组 | `POST /labs` + 成员页 | 写进他人 `org_id` | 非管理员传异机构 403；成员页「本机构再建一组」 |
| 进度 | `/pending` | 新进度产品 | 15s 轮询；通过后落地工作台；公开 `GET /auth/public` 条款 |

夹具加深：`test_org_onboarding.py`（含 accept / suspend / 通知）。版本号保持 0.2.0。

***

## 66. 组织入驻门闩闭合（禁止第二后台、禁止跨机构邀请、禁止 Era D）

§65 已能接邀请与铃铛，但仍有裂缝：停用后成员仍可深链进实验室 API；可邀请已属其他机构的邮箱；同名机构可重复申请；管理侧栏看不见待审数；机构通知 href 被默认落到通知列表。世界级不是新开审批台，而是现有 `require_membership` / 邀请 / `me` / 通知深链吃同一道机构门闩。不改 §53–§65 正文。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 门闩 | `require_membership` | 停用后继续读写 | 非 admin 对 pending/rejected/suspended 一律 403 |
| 邀请 | `POST .../invites` | 跨机构拉人 | 邮箱已属其他机构 → 400；已是成员 → 400 |
| 申请 | `apply_org` | 同名刷申请 | pending/approved 同名拒绝 |
| 管理角标 | `GET /auth/me` | 独立运营角标页 | `org_pending_count`；侧栏「管理（待审 N）」 |
| 通知 | `object_open_href` | 新通知产品 | 尊重 payload `href`；`object_type=org` 默认真 `/admin` |

夹具：`test_org_onboarding.py` 加深。版本号保持 0.2.0。

***

## 67. 组织入驻体验闭合（禁止第二后台、禁止一人多机构、禁止 Era D）

§66 门闩已闭合，但仍有体验裂缝：未开通时顶栏仍露出空实验室切换；停用恢复后开通进度页不轮询；停用机构的旧邀请链接仍显示可注册；审批通过只 toast 路径不跳成员页；邀请文案未写清「不可跨机构」。世界级是现有壳层 / 邀请预览 / `/admin` / 成员页把同一道机构状态说清楚。不改 §53–§66 正文。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 壳层 | `shell.jsx` | 未开通仍像已进工作台 | 隐藏实验室切换与跳转；顶栏 + 内容区锁定提示 |
| 进度 | `/pending` | 仅 pending 轮询 | `!workbench_ready` 一律轮询（含停用恢复） |
| 邀请预览 | `GET .../invites/{token}` | 停用机构仍 usable | `org_status`；非 approved → `usable=false` |
| 审批 | `/admin` | 通过后找不到成员页 | `members_href` 深链跳转 |
| 成员 | `Members.jsx` | 跨机构误邀 | 页头写清本机构邮箱 |

夹具：`test_org_onboarding.py` 加深。版本号保持 0.2.0。

***

## 68. 组织入驻运营闭合（禁止第二后台、禁止一人多机构、禁止 Era D）

§67 体验已闭合，但仍有运营裂缝：机构已 `approved` 却无成员时壳层仍写「审核中」；再次「通过」不补实验室；停用后无法撤销旧邀请；管理员仍可为停用机构发邀请；建实验室回退到非开通机构；软删邮箱无法重申；用户表与审计筛不到机构态。世界级是现有 `me` / `review` / 邀请撤销 / `/admin` 用户与审计吃同一套机构态。不改 §53–§67 正文。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 就绪原因 | `GET /auth/me` | 无成员也叫审核中 | `workbench_block`：pending/rejected/suspended/no_membership |
| 修复通过 | `review approve` | 已 approved 静默空转 | 一律 `ensure_first_lab`；重复通过记 `org.heal` |
| 撤销邀请 | `DELETE /invites` + list | 停用后 403 清不掉 | `enforce_org=False` 仅允许列表/撤销 |
| 发邀请 | `POST .../invites` | admin 绕过停用 | 一律要求机构 `approved` |
| 建组 | `POST /labs` | 回退到 pending 机构 | admin 无 `org_id` 只选已开通 |
| 邮箱回收 | apply/register | 软删邮箱卡死 | 腾出 `deleted.{id}.` 前缀 |
| 运营筛 | `/admin/users` `/admin/audit` | 新运营台 | `?org_status=`；`action=org.*` |

夹具：`test_org_onboarding.py` 加深。版本号保持 0.2.0。

***

## 69. 组织入驻审批纪律与停用清理（禁止第二后台、禁止跳过重提、禁止 Era D）

§68 运营筛与修复已有，但仍有纪律裂缝：管理页对已驳回仍点「通过」、已开通看不到「修复」；停用后成员页被壳层挡掉，撤销 UI 不可达；恢复不跳成员页；驳回后可直批跳过重提；同邮箱重复未用邀请；机构表不展示联系人。世界级是现有 `review_org` / 壳层放行 / 成员页 / 邀请创建把审批纪律说清。不改 §53–§68 正文。不改 `build_graph.py` 边。

| 模块 | 改哪个现有对象 | 禁止 | 落地 |
| --- | --- | --- | --- |
| 审批 | `review approve` | 驳回直批 | 仅 `pending` 可通过；`approved` 走 heal；驳回须重提 |
| 管理页 | `Admin.jsx` | 驳回仍显示通过 | 「通过」仅 pending；「修复」仅 approved；恢复也跳成员页 |
| 停用清理 | shell + `list_members` | 撤销 UI 不可达 | 停用可进 `/members`；列表 `enforce_org=False` |
| 成员页 | `Members.jsx` | 停用仍发邀请 | 隐藏新建/邀请，保留撤销 |
| 邀请 | `POST .../invites` | 同邮箱重复未用邀 | 已有未接受邀请 → 400 |
| 联系人 | `/admin/orgs` 表 | 另开 CRM | 展示联系人与驳回备注 |

夹具：`test_org_onboarding.py` 加深。版本号保持 0.2.0。
