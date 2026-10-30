# 蜂算科研：前沿成熟体系施工合同（F 系列）

> 给 coding agent 与人类共同维护。产品名 **蜂算科研**。OpenLens 只作为引擎。
>
> **本文件是 H5.174 之后的唯一施工合同。** 不重写 `docs/PLATFORM_UPGRADE_PLAN.md` 与 `docs/WORLDCLASS_ROADMAP.md`。
>
> 原则：**只升级现有对象。禁止平行实现。** 新需求必须先回答「改哪个现有 endpoint / 表 / 页面」。

***

## 0. 已锁定决策

| 编号 | 规则 |
| --- | --- |
| D-VIEW | 权限仍是 `teacher` \| `student` + 平台 `admin`。`membership.title` 只作文职/年级。大导师要看的东西挂在现有 `/cockpit`、`/stats`、`/digest`。禁止 `/pi`、`/report`、第三角色。 |
| D-LITENG | 允许改 `openlens_ai/agents/literature_reviewer.py` 的工具接线、停止条件、书目产出；允许改 `paper_dedupe`、文献提示词、`latex_related_works.md`。禁止改 `build_graph.py` 边语义。禁止打开 `ALTER_PLAN`。 |
| D-NOFORK | 统计加深走 `GET /labs/{id}/stats` 加字段；AI 用量走 `usage_ledger`；检索加深走文献库 `?tab=search`；相关工作加深走 `/writing` + compare 写回。 |
| D-HONEST | 无 GPU 不伪造指标；扫描件 `unusable`；模拟评审非官方；禁止排行红灯。 |
| D-KEEPAPI | 现有 `/api/v1` 只加字段与子路径，禁止 `/v2`。 |
| D-KEEPUI | 不新增侧栏分组。禁止 `/atlas`、`/degree`、独立检索/报表页。 |

企微 / ASR / H6 / 官方查重 / 暗色主题 / Sci-Hub **仍后置或永不做**。

**口径陷阱（G 系列必须先守住）：**

- 筛选空 ≠ 库空。文献英雄区只在实验室一篇都没有时出现。
- 组内在读 ≠ 我的在读。`reading_status=reading|want_read` 无 `reader_id` 仍是当前用户；组内人次必须带 `reading_scope=lab`。`unread` 无 `reader_id` 仍是 F9 组内无人在读。
- 核心会膨胀。方向未钉核心时分母仍是方向全部文献（F12）；钉过之后只计钉子。

***

## 1. 角色想看什么（权限不增加）

- **学生（5 分钟 / 5 天 / 5 周）**：登录进首页，选方向后打开本方向文献库（不带组内 `unread`）。读→方法卡/辅导→写作大纲→建稿写回→主张→复现/发现式。不见他人 Token。打开驾驶舱时须知这是组内透明。
- **带组导师（周一 / 周五 / 开题周）**：驾驶舱成员卡看谁卡住；想读用该生 `reader_id`。周报看七天事件，不复读六句。开题可钉方向核心，不新开学位页。
- **大导师（仍是 teacher）**：一页六句——人 / 方向 / 时间 / 诚实 / AI 用量 / 风险。本周阅读必须进组内在读（`reading_scope=lab`），不得进导师自己的在读空表。跨实验室只切换，不合并驾驶舱。
- **机构管理员**：开通、GPU、平台 Key、审计。页：`/admin`。不把组内态势做进管理页。

***

## 2. 阶段

| 阶段 | 改哪里 | 禁止 | 验收 |
| --- | --- | --- | --- |
| F0 | 本文件 + WORLDCLASS 指针 + UI.md | 重写 H1/H2 | 三份文档互指 |
| F1 | cockpit / stats / digest 加 `situation` + `lab_ai_overview` | 新报表路由、排行 | 导师见六句与 AI；学生不见他人 Token；CSV 对齐 |
| F2 | 文献库 `?tab=search`、jump `search_href` | 新搜索产品、jump 变学术检索 | 关键词到入库两次点击；重复入库 `already_in_library` |
| F3 | writing 相关工作步骤、claims 点名核心文献 | 第三条综述引擎 | 未选文献标「未结合文献」；写回 citations 可回跳 |
| F4 | 文献子图 PubMed/S2、覆盖率停止、`.bib`、去重 arXiv ID | 改图边、ALTER_PLAN、Sci-Hub | 工件同时有 md+bib；无 bib 不得 succeeded |
| F5 | `search_adapter` 共享 + `test_f1_*.py` | 再复制爬虫 | 召回键、bib、态势可复算 |
| F6 | 态势空态 / 额度将满 / 工人未开文案；检索无结果下一步；相关工作写回成功条 | 新产品页、排行 | 空库六句带下一步；检索/顶栏空结果含换源/捕手/PDF；写回摘要与译润同一套 |
| F7 | 方向枢纽 `stuck`；首页 `next_steps`；写作页核心文献覆盖 | 新卡住页、排行、点名学生 | 枢纽可见未读/未写相关工作/未跑；学生首页可点下一步；写作页未点名核心文献与主张追踪同一口径 |
| F8 | 驾驶舱成员卡 / 成员进展 / 统计 / 周报吃 `stuck` | 新报表、按卡住排序 | 成员卡可见尚未开读/未写相关工作；统计方向行与 CSV 有 stuck；周报有卡住摘要 |
| F9 | 文献库 / 手稿列表吃卡住过滤 | 新卡住页、改 jump 语义 | `reading_status=unread` / `has_card=0` / `related_work=0` 与卡住数字同一口径；相关工作只认标题行 |
| F10 | 对比写回 `相关工作`；方向列表 `stuck_count` | 第三条综述、按卡住排方向 | 对比写回后 `related_work_written=true`；方向表卡住数字与过滤 href 同一口径 |
| F11 | 驾驶舱/导师工作台吃 `stuck_brief`；对比 `related_work_md` 点名题名 | 新报表、催导师开读、第三条综述 | 导师落地页可见卡住摘要；学生首页仍只谈自己；写回相关工作后 `core_mentioned` 含所选篇目 |
| F12 | 手稿 `core_missing=1`；方向列表 stuck 对齐统计脊柱；成员卡点名核心 | 新卡住页、按卡住排序 | 枢纽「核心未点名」进同一过滤；方向表 `stuck_count` 与统计方向行一致；写了相关工作仍未点名时成员卡不空 |
| F13 | 枢纽核心计数改手稿篇数；写作/写回点名所选文献；学生开读不再进无人在读 | 第三条综述、改 jump | 枢纽 count 与 `core_missing=1` 列表条数一致；写作写回后可选文献被点名；学生「开始阅读」进本方向文献库 |
| F14 | 卡住按全量手稿计；译润拓展相关工作写回点名；成员 stuck.count 对齐 | 新卡住页、排行、第三条综述 | 枢纽/成员卡不再被预览 20/3/6 篇截断；拓展相关工作写入「相关工作」并点名；`stuck.count` 等于展示步数 |
| F15 | 学生首页吃未测当成功；译润文末追加也写入相关工作；统计成员行未测当成功 | 新卡住页、排行、第三条综述 | 学生下一步可见自己稿的未测当成功；拓展相关工作即使用「文末追加」也写入「相关工作」节；成员行有 ungrounded 列 |
| F16 | 写回成功条下一步；首页「全部」带方向/卡住；下一步一行一条 | 新产品页、排行、第三条综述 | 写回/拓展返回 `next_step`（未测去该稿，缺点名进主张）；首页卡片链到过滤列表且文案跟过滤一致；`NextStepList` 一行一条 |
| F17 | 手稿 `owner_id`；成员卡住进本人列表；手稿页下一步；态势诚实句点对；方向枢纽链文案 | 新成员档案、新卡住页、第三条综述 | 统计成员核心/未测/未写相关工作带 `owner_id`；手稿对象页 `next_step` 去复现或去写作；无未跑但有未测时诚实句进 `ungrounded=1`；枢纽顶栏文案跟卡住过滤一致 |
| F18 | 文献 `reader_id`；成员尚未开读进该生未读；写作生成下一步写回；手稿作者筛 | 新阅读产品、改 F9 组内无人在读、第三条综述 | `reader_id`+`unread` 只看该成员未开读，组内无人在读口径不变；成员卡开读带 `reader_id`；撰写章节返回写回下一步；手稿列表可选作者 |
| F19 | 大纲下一步；文献读者下拉与空态；成员进展文献 extra | 新阅读产品、改 F9 组内无人在读、第三条综述 | 生成大纲返回写相关工作/从大纲创建手稿；文献库可选读者（默认叠未开读）；个人/组内未读空表给 `empty_next`；成员进展「正在读」进该生未读或在读 |
| F20 | 成员卡/统计该生文献；创建手稿下一步写相关工作 | 自动建稿、新档案页、改 F9 组内无人在读 | 驾驶舱成员卡与统计「在读/手稿」进 `reader_id`/`owner_id`；创建手稿返回写相关工作；大纲创建后切章节标题，不在生成接口里自动建稿 |
| G0 | 本文件口径陷阱 + UI/USER 角色动线 | 重写 H1/H2、第三份路线图 | 三份互指；P0 三条写进 UI |
| G1 | 文献/手稿/复现 `empty_kind`；筛选空 ≠ 库空 | 新检索页 | 真空库才出上传英雄区；过滤空给 `empty_next` |
| G2 | `reading_scope=lab`；想读/方向在读/态势本周阅读 | 改 F9 unread、学生开读带 scope | 组内在读列表含学生篇目；导师自己的在读仍为空；成员想读只用 `reader_id` |
| G3 | FeedbackStrip 可点；手稿列表创建吃 next_step；文案 | 新通知中心、自动建稿 | 创建手稿下一步写相关工作；导师首页写明下方是我的在读 |
| G4 | 覆盖率读 `lab_context.json` sidecar；计数走 dedupe | 改图边、ALTER_PLAN | 只写 sidecar 时未点名注入题名不得提前停 |
| G5 | medical 等域提示词对齐；禁止 title-only 空壳 bib | Sci-Hub、新爬虫 | 无工具结果不得 succeeded；medical 不再 5 次硬停 |
| G6 | 方法卡/辅导结构化 next_step；KB hash 常驻横幅 | 新 /tutor、新 RAG | 仅摘要分析含 next_step；刷新后仍见简易检索 |
| G7 | 复现 `preflight`（卡/数据/Docker/工人/GPU） | 无 Docker 却 succeeded | 允许无卡创建但标明；GPU off 链等待列表 |
| G8 | 周报事件为主 + 相对上周；卡住合计脚注 | 新报表、按卡住排序 | digest 有 `delta`；situation 字段仍在 |
| G9 | 方向可钉核心；有钉才改分母 | 模型自动选核心、排行 | 未钉与 F13 一致；钉 2 点 1 则 missing=1 |
| G10 | 推荐 / KB 问答 / 对比空态给 `empty_next` 或 `next_step` | 新检索页、第三条综述 | 真空推荐才出关键词下一步；KB 无索引/无命中可点上传；对比无 gap 不假装有综述 |
| G11 | 图谱方向下拉 + 顶栏 jump 失败不静默 + 图谱 `empty_next` | 新图谱产品、jump 变学术检索 | URL 写 `direction_id`；失败仍给「去文献检索」；空图给入库/挂方向 |
| G12 | 文献子图接上已有 bioRxiv / IACR 工具 | Sci-Hub、新爬虫、Scholar 默认入列、改图边 | `search_tools` 含现成 Searcher；提示词点名这两源 |
| G13 | `GET .../reproduce-preflight`；手机顶栏折行 | 无 Docker 却 succeeded、新复现页 | 创建区不依赖列表包一层也能看缺卡/GPU/工人；窄屏 jump 单独一行 |
| G14 | 文献检索/推荐露出已有 medRxiv / bioRxiv / IACR | Sci-Hub、新爬虫、Scholar 默认入列 | 来源多选可见三源；空结果下一步可换这三源；推荐走 adapter 已有 Searcher |
| G15 | 发现式 `empty_kind` + `GET .../research-preflight` | 新任务台、无 GPU 却 succeeded | 筛选空 ≠ 库空；创建区可见 GPU/工人/Docker，数据集仍可选 |
| G16 | 数据集空列表 `empty_kind` / `empty_next` | 新数据产品 | 真空库才说「还没有数据集」；前端 `asItems` |
| G17 | 图谱方向 / 成员动态 / 写作覆盖失败 ≠ 空 | 新通知中心 | 加载失败出重试，不装成没有方向、没有事件、没有核心缺口 |
| G18 | 手稿下拉一律 `asItems` | 自动建稿、新写作页 | 真空库包装不再把对象当成列表；写作/译润/对比写回可选手稿 |
| G19 | 看板 `empty_kind`；待认领空 ≠ 没有任务 | 新看板产品 | 筛选空给 `empty_next`；有任务时仍返回数组 |
| G20 | 文献检索失败 ≠ 无结果 | 新检索页 | 失败出重试，不出现「没有检索结果」换源条 |
| G21 | 读者/作者/主张失败 ≠ 空 | 新阅读产品 | 下拉与主张追踪可重试，不装成没有成员或没有缺口 |
| G22 | 方向列表空态按角色 | 新方向产品、学生建方向 | 学生空表写「导师创建后在工作台选定」，不催学生先建 |
| G23 | 推荐失败 ≠ 无推荐 | 新检索页 | 失败出重试，不出现「先填方向关键词」空表 |
| G24 | 周报邮件开关 / 平台 Key 失败 ≠ 关闭 | 新通知站 | 读设置失败禁用开关并重试；管理页不把 Key 失败写成未保存 |
| G25 | 写作批注失败 ≠ 没有批注 | 新批注产品 | 加载失败可重试，不装成「导师式批注」为空 |
| G26 | 组会 / 资料真空库 `empty_kind` | 新资料产品 | 一篇都没有才包一层；有记录仍返回数组 |
| G27 | 工作日志真空库 `empty_kind` | 新日志产品 | 一篇都没有才包一层；有记录仍返回数组 |
| G28 | 发现式 / 复现诊断失败 ≠ 齐备 | 无 GPU 却 succeeded | 诊断请求失败出重试，不把「没有 missing」当成可以放心创建 |
| G29 | 管理页 GPU 失败 ≠ 关闭 | 新算力台 | 读档失败写「状态未知」并禁用改档，不得写成实验算力关闭 |
| G30 | 术语 / 成员方向失败 ≠ 空 | 新成员产品 | 术语失败可重试；方向列表失败不得写成「未选方向」 |
| G31 | 顶栏未读失败 ≠ 没有未读 | 新通知站 | 未读数读失败写「通知（未知）」，铃铛不把失败画成 0 |
| G32 | 译润术语 / 笔记失败 ≠ 空 | 新译润页 | 失败出重试，不出现「可去知识库锁定」或空笔记下拉 |
| G33 | 管理页实验室失败 ≠ 没有实验室 | 新管理台 | 列表失败可重试，不装成没有实验室 |
| G34 | 知识库文档真空库 `empty_next` | 新 RAG | 已有分页对象只加字段；一篇都没有才给出上传下一步 |
| G35 | 学生选定方向回工作台 | 新方向产品、学生建方向 | 首页下一步 / 引导「选定方向」进 `/labs/{id}`，不进只能新建的方向列表 |
| G36 | 成员页可选定自己的方向 | 新成员档案 | 用已有 `PUT .../me/direction`；条上可选，不说「在下表改」 |
| G37 | 工作台方向下拉同步已选 | 新方向产品 | 下拉跟上 `my_direction_id`；无方向时学生不催新建 |
| G38 | 看板添加任务后清空表单 | 新看板产品 | 提交成功后复位，避免连点重复建同一条 |
| G39 | 推荐空态按角色给下一步 | 新检索页、学生改关键词 | 学生去检索入库；导师才「先填方向关键词」 |
| G40 | 导师可在枢纽补关键词 | 新方向产品、学生改词 | 用已有 `PATCH /directions/{id}`；词空时列表点进枢纽填 |
| G41 | 指定方向的推荐空态进该枢纽 | 新推荐页 | `direction_id` 有值时关键词下一步进 `/directions/{id}`，不丢进总表 |
| G42 | 入库 / 术语 / 笔记 / 节点表单复位 | 新入库产品 | 成功后清空，避免连点重复导入同一条 |
| G43 | 推荐禁止 `survey` 空壳检索 | 新检索页、乱推荐当成功 | 没有方向名/关键词时不外搜，三表空才给 `empty_next` |
| G44 | 推荐空提示按角色 | 新检索页 | 无方向仍请求接口；toast 用 `empty_next`，学生不催填词 |
| G45 | 枢纽关键词表单跟上当前方向 | 新方向产品 | 换方向时写入当前词，避免串到上一个方向 |
| G46 | 看板指派下拉 `asItems` | 新看板产品 | 成员列表形状变化时指派不会变成空对象 |
| G47 | 归档方向解开成员选定 | 新方向产品 | `DELETE /directions/{id}` 清空指向它的 `direction_id`，不留幽灵选定 |
| G48 | 已删方向当未选 | 新档案页 | 工作台 / 推荐 / 成员列表不把已归档 id 当有效方向；不能再选定已删方向 |
| G49 | 写作 / 图谱方向下拉 `asItems` | 新写作页 | 方向列表包装变化时筛选不会空白 |
| G50 | 无方向文案两边可读 | 学生建方向 | 态势与学生下一步写「由导师创建」，不催学生去新建 |
| G51 | 选定与归档互斥 | 新锁产品 | 同一实验室的选定/归档走已有实验室行锁，避免归档后选定写回幽灵 id |
| G52 | 已归档方向再删 404 | 硬删回收站 | `deleted_at` 已有则 404，不假装又归档成功 |
| G53 | 云端模型地址禁回环/私网 | 封内网 LLM | `kind=cloud` 禁止回环与 RFC1918；`onprem` 仍可用内网；云元数据一律禁 |
| G54 | 选定只写入未删除方向 | 跨实验室改方向 | 锁内再验 `lab_id` + `deleted_at`，不能选定已归档或外实验室方向 |
| G55 | 最后一位导师互斥 | 新锁产品 | 降级 / 移除导师走已有实验室行锁，锁内再数导师，避免两人同时把最后一位降成学生 |
| G56 | 邀请核销互斥 | 新邀请产品 | 发出 / 注册 / 接受 / 撤销走同一把实验室行锁；已撤销再删 404，已用邀请不能再注册 |
| G57 | 云端主机名跟解析走 | 封内网 LLM | `kind=cloud` 解析后若落到回环 / RFC1918 / 链路本地 / 云元数据则拒绝；解析失败不放行十进制回环；`onprem` 仍可用内网 |
| G58 | 删组与选定互斥 | 新锁产品 | `DELETE /labs/{id}` 走行锁且已删再删 404；选定在锁内复验实验室未删 |
| G59 | 最后一位管理员互斥 | 新锁产品 | 停用管理员走平台进程锁，锁内再数在职管理员，避免两人同时把最后一位停掉 |
| G60 | 已删手稿再删 404 | 硬删回收站 | `deleted_at` 已有则删除 / Overleaf 同步 404，不假装又成功 |
| G61 | Overleaf / GROBID 禁元数据 | 封内网 git | git 与 GROBID 一律禁云元数据与链路本地；实验室内网 Overleaf / 本机 GROBID 仍可用 |
| G62 | 建组 / 加成员撞唯一键 | 新实验室产品 | 代码或成员并发重复时 400，不 500 |
| G63 | 发现式镜像装入引擎依赖；预检 `engine_ok` | 改图边、装 OpenHands、第三条写论文 | 缺依赖仍诚实失败；装齐后 `engine_runtime_missing` 为空 |
| G64 | 无算力时建议 `interrupt_after=literature_reviewer` | 无 GPU 却 succeeded | 预检给建议；默认暂停于综述，状态是暂停不是完成 |
| G65 | 同实验室同文件哈希旧条合并 | 新清洗台、硬删 | 保留最早一条，其余软删；方向/在读挂回保留条 |
| G66 | `chat()` 剥 think；阅读器对照译、辅导气泡 | `/tutor`、思维链秀 | 译文/辅导无 `<think>`；空文本不空放 400；侧栏正序对话 |
| G67 | `tutor_sessions` + 本会话 12 轮 + 上下文芯片 | 跨文献记忆表 | 下拉切会话；history 不含其他 session；无页文本仍叫「尚无页文本」 |
| G68 | 辅导发送中可停；气泡写笔记/去译润 | 全站通知中心、独立 IM | 连点禁用、本地待发送气泡；写回现有笔记 |
| G69 | 辅导 ungrounded 可见、重新回答、`[p.N]` 可点 | `/tutor`、新记忆表 | 历史气泡带无依据警告；同一 session 再答；MdPreview 点回 PDF |
| G70 | `chat()` 一次重试；辅导/译润可选 SSE；Runs 剥 think | 新流式产品、双记账单 | 超时/502 只重试一次；`stream=true` 仍走现有 POST；EventFeed 不展示 think |
| G71 | 译润/写作发送中可停、上下文芯片、章节再写 | 新写作引擎 | Abort 停止；芯片填本页/摘要/笔记；`rewrite` 改写上一稿 |
| G72 | 方法卡超长分块合并；坏 JSON 不装垃圾；生成后 `panel=card` | 新分析页 | 仅超 12000 字切块；解析失败空卡 + next_step |
| G73 | 对比 `gap_md` 无出处裁空；对照卡；禁止连点 | 第三条写论文、新对比页 | 坏 JSON 不进 table_md；empty_next；复现差距单独字段 |
| G74 | 章节 `ungrounded_spans` 进 Alert | 新核验产品 | 写回仍须 `write_mode`；短语可见 |
| G75 | 大纲三种诚实 notes；禁止自动写回/建稿 | 自动建稿、第三写作引擎 | `write_mode` 默认空；选手稿不等于写回 |
| G76 | 模拟评审同套未测当成功；`official=false` | 官方查重、知网 | 顶栏字段 + 主张同一警告；不改正文 |
| G77 | KB 引用可点；`reproduce_gap_md` 独立块 | 新 RAG 页 | 问答也返回差距；综述不吞差距 |
| G78 | 组会纪要 LLM 不得吞失败；行动须是原文子串 | 新纪要产品、自动乱建任务 | 幻觉行动 `ungrounded` 不入看板；失败仍提取「行动:」行 |
| G79 | 平台 chat system 收口只读 markdown | 提示词管理后台 | `app/prompts/*.md` + `load_prompt`；禁止后台编辑 |
| G80 | 生成失败记一次尝试；hash 仍叫简易检索 | 新评测台、假装向量已通 | `usage_ledger.credential_scope=failed`；概述写失败次数 |
| G81 | 综述停止/书目/去重：URL 抽 arXiv/DOI、题名标点归一、utf-8 收尾 | 改 `build_graph.py` 边、ALTER_PLAN | untitled 不进 bib；同文不同标点不算三篇；覆盖解析失败写日志再回退 |
| G82 | 收尾闸拒绝 untitled / 空题名 bib | 无 bib 却 succeeded、从标题臆造 bib | `valid_bib_titles` 为空不得标完成；有真题名仍可无 DOI |
| G83 | 从 fence / 缓冲串 / 夹杂 JSON 收割题录 | 新爬虫、改图边 | 工具缓冲不再因为第一行不是 `[` 丢光；标题行仍不能冒充 bib |
| G84 | 检索命中写入 `search_hits.jsonl` 后再裁断 | 新记忆表、改图边 | 向量裁断后仍能写出 bib；`papers_to_dicts` 补 arXiv/PMID |
| G85 | 网页命中不进 bib；提前停须有学术标识 | Scholar 默认入列、Tavily 当论文 | 无 DOI/arXiv/PMID 的博客不进书目；仅三篇题名不得提前停 |
| G86 | `search_hits.jsonl` 进发现式工件；闸文案带命中数 | 新书目台、改图边 | Runs 工件可见该文件；空 bib 且有命中时写清请恢复 |
| G87 | 检索命中落盘去重重写 | 新记忆表 | 同一 DOI/题名不无限追加；工件条数等于去重后篇数 |
| G88 | 点名认「未使用：标题」；失败列出未点名题名 | 自动改综述正文冒充已引用 | 未点名闸点出题名；写了未使用则过闸 |
| G89 | 发现式 diagnosis 带 `search_hit_count` / `unused_bib_titles` | 新诊断页、改图边 | 现有 GET run 加字段；成功态无 next_step |
| G90 | Runs 详情未点名题名 Tag；工件标「检索命中」 | 新书目台 | 沿用 Alert / 工件表；可下载 search_hits.jsonl |
| G91 | 列表 FeedbackStrip 写未点名篇数 | 新通知中心 | 有 unused 才写；命中数不另开列 |
| G92 | diagnosis 带 `unused_injected_titles` / `search_hit_titles` | 新诊断页 | 组内未交代与命中题名进现有 GET |
| G93 | 列表下一步改短句 | 新列、堆闸全文 | 有未点名/未交代时不展示整段闸文案 |
| G94 | 详情 Alert 可恢复；命中题名 Tag；可下载命中 | 新书目台 | 沿用 ResumeJob / 工件下载 |
| G95 | 学生首页 `next_steps` 吃自己的发现式未点名 | 新任务台、排行 | 失败，或暂停且已有综述工件时链到 `/runs/{id}`，不催别人 |
| G96 | 导师卡住摘要 / 六句诚实与风险 / 成员卡吃未点名 | 新报表、点名学生 | 计数是任务次数；href 进现有 `?unused=1` |
| G97 | 发现式列表 `?unused=1`；StuckBriefBar 芯片 | 新书目台、改图边 | 筛选空 ≠ 库空；芯片与数字同一口径 |
| G98 | 综述前暂停不算未点名；diagnosis 已有键不再扫盘 | 新诊断页、改图边 | 无 md/bib 的暂停不进指挥面；空列表键也信 |
| G99 | 失败/暂停落盘 unused；统计/CSV 一次计算 | 新报表、排行 | `unused_research` 进 stats 与 CSV；卡住合计仍不含 |
| G100 | 学生下一步点名题名；导师宫格/统计卡进 `?unused=1` | 新任务台 | 文案带第一篇题名；统计列不是排行 |
| G101 | 发现式 diagnosis 信已落盘 unused 键 | 新诊断页、改图边 | 列表不再每条扫作业目录；空列表键也信 |
| G102 | 大纲诚实条吃上次发现式未点名 | 第三条写论文、自动改正文 | `honesty_notes.kind=unused_research`；生成不等于写回 |
| G103 | 写作页 / 主张追踪 / 写回下一步露出未点名题名 | 新写作引擎 | 沿用 Alert / claims；点进 `/runs/{id}` |
| G104 | 综述前失败不算未点名；brief 不因新失败漏计旧任务 | 新诊断页、改图边 | 无 md/bib 即使 diagnosis 脏键也不 actionable；扫描窗口大于展示条数 |
| G105 | 写作未点名看手稿作者与正文 | 自动改正文、第三条写论文 | 正文已点名或写「未使用」不再催；导师看学生稿用学生任务 |
| G106 | 对比 / 相关工作拓展吃同一套未点名诚实条 | 第三条写论文 | 沿用 Alert；生成不等于写回；译润不刷未点名 |
| G107 | KB 问答/综述吃未点名诚实条 | 新 RAG 页、第三条综述 | 沿用 Alert；手稿已点名则不再催写作面 |
| G108 | 模拟评审吃未点名；有未点名不得只报 ok | 官方查重、改正文 | `official=false`；finding.kind=unused_research |
| G109 | 方向列表/枢纽吃该方向成员未点名；首页文案分综述/相关工作 | 新卡住页、排行、改 stuck_count | 选定或手稿所挂方向才计数；卡住合计仍不含 |
| G110 | 未点名列表吃 `direction_id`；方向深链不再打开全组 | 新卡住页、改 jump | `?unused=1&direction_id=` 只见该方向成员任务；筛选空写「该方向没有未点名」 |
| G111 | 学生周报只吃自己的未点名；邮件点出题名 | 新报表、排行 | `scope=self` 不见他人任务；markdown 写出 leftover 题名 |
| G112 | 图谱/手稿行吃同一套未点名；对比 leftover 认已点名稿 | 新图谱产品、第三条写论文 | 这篇正文已点名则该节点不再催；任一篇点名后对比不再催写作面 |
| G113 | 同哈希入库走实验室行锁 | 新清洗台、硬删 | 并发拖入同一 PDF 只留一条；哈希相同会合并 |
| G114 | 手稿写回/保存/恢复走行锁；diagnosis 合并不丢 progress | 新锁产品 | 连点写回不互相覆盖；有戳仍 409；失败落盘 unused 不丢 subgraphs |
| G115 | 周报未点名题名收齐本组 leftover | 新报表、排行 | 导师 markdown 写出多条任务题名；学生仍只见自己的 |
| G116 | 成员未点名深链吃 `requester_id` | 新卡住页、改 jump | 统计/驾驶舱成员数字进 `?unused=1&requester_id=`，不是全组列表 |
| G117 | 发现式创建快照方向；换向不带走旧未点名 | 新方向产品、改图边 | 换方向后原方向仍能看见该任务；新方向不把旧账算进来 |
| G118 | unused 信 `review_ready`；指挥面一次扫描 | 新报表、扫盘当成功 | 已落盘 diagnosis 不再扫作业目录；手稿列表不再每人再查一遍 |
| G119 | 未点名列表写出人名 / 方向名 | 新筛选产品、改 jump | `?unused=1` 才包 `{items, unused_scope, unused_titles}`；页头写「张三 · 方向甲」；有任务时普通列表仍是数组 |
| G120 | 周报 / 统计 leftover 改成可点芯片 | 排行、新书目台 | 同一套 `unused_titles` + `unused_title_more`；芯片进现有 `?unused=1`，不是灰字堆题名 |
| G121 | 未点名是页英雄；清筛选丢掉范围参数 | 新卡住页、改 crumbs 语义 | 页头/面包屑「发现式未点名」；关掉 unused 同时丢掉 `direction_id`/`requester_id`；创建折进 Collapse |
| G122 | 无快照旧任务不进方向未点名 | 新方向产品、改图边 | 没记下当时方向的任务只出现在实验室/成员未点名；换向不把旧账算进现方向 |
| G123 | 手稿卡住过滤是页英雄 | 新卡住页 | 页头/面包屑写「核心未点名 / 未写相关工作 / 未测当成功」；筛选空写对应文案；有稿时仍返回数组 |
| G124 | 写作/KB leftover 芯片；文献卡住页头 | 第三条写论文、新阅读产品 | 沿用 Alert + 同一套题名芯片；文献 `unread` / 无卡页头对齐；关掉在读筛选丢掉 `reader_id` |
| G125 | 复现列表过滤是页英雄 | 新复现页、改 wrap 语义 | `status` / `honest` / `literature_item_id` 改页头与面包屑；筛选空写对应文案；有任务时状态/诚实列表仍返回数组；创建折进 Collapse |
| G126 | 发现式状态过滤是页英雄 | 新卡住页、改 unused wrap | 未点名仍优先；`awaiting_gpu` / `pending` / `failed` / `paused` 改页头；筛选空写对应文案；状态过滤不折创建 |
| G127 | FeedbackStrip 题名优先；方向枢纽 / 成员进展 leftover 芯片 | 新书目台、排行 | 下一步条先写第一篇题名再写篇数；方向列表/枢纽/成员进展用同一套可点芯片；只记快照方向 |
| G128 | 复现筛选空态诚实 | 新复现页、改 wrap | 文献/数据集范围叠加 status/honest，不假装该文献完全没有任务；`queued` 与 `pending` 同一英雄 |
| G129 | 发现式 unused 与 status 互斥 | 新卡住页、改 unused wrap | 打开 unused 丢掉 status；打开 status 丢掉 unused；API 未点名时忽略 status；`queued`/`blocked` 命名 |
| G130 | 数据集库空是页英雄 | 新数据集页 | 库空用 Alert + 页头；复现/发现式 `dataset_id` 筛选空写「该数据集没有…」，有记录时仍返回数组 |
| G131 | 方向可挂子课题；成员可挂多方向 | 新课题组页、第三角色 | `parent_id` 仅两级；`membership_direction_links`；主方向仍镜像 `membership.direction_id` |
| G132 | 成员生命周期 status | 人事模块、排行 | `active / on_leave / graduated`；毕业/休学退出尚未开读，历史归属保留 |
| G133 | 导师可改他人方向与负责人 | `/pi`、第三角色 | PATCH 成员扩 `direction_id` / `direction_ids` / `status`；方向 PATCH 写 `owner_user_id` / `sort` / `parent_id` |
| G134 | 方向级 RAG 语料域 | 拆 KB、新 RAG 产品 | `kb_documents.direction_id`；问询带 `direction_id` 用文献方向链接推导语料 |
| G135 | 切块重叠 + heading_path | 新解析产品 | 约 15% 重叠；GROBID 章节写入已有列 |
| G136 | 混合检索 + MMR + 术语扩写 | 新检索引擎 | 词法归一打分；同页去重；术语表进查询 |
| G137 | 转化漏斗走现有 stats | 新报表、排行 | `funnel` 七级 + 级间转化；分母为零写暂无 |
| G138 | 每日 rollup | 新时序产品 | `lab_metric_daily`；工人幂等写当日；复用 `_stats_payload` |
| G139 | 趋势 / 方向过滤 / CSV | 新报表页 | `?days=` `?direction_id=`；CSV 同步 funnel 行 |
| G140 | 统计页 ECharts | 暗色、排行红灯 | 漏斗 / 堆叠柱 / 趋势；图元点进现有过滤；表格切换 |
| G141 | 驾驶舱热条与 sunburst | 学位进度轴、排行 | 成员阶段覆盖 + 方向层级；历史成员中性分区 |
| G142 | 周报同比条 | 新周报页 | 复用 `delta`，不是排行 |
| G143 | 统计 / 方向列表去 N+1 | 改口径 | 一次 spine + 一次核心扫描后分桶 |
| G144 | 趋势读 rollup | 请求时扫全史 | 无汇总时写「尚无每日汇总」 |
| G145 | 父方向含子课题文案；负责人可改 | 新地图页 | 列表/枢纽写「含子课题 N」 |
| G146 | 召回评测夹具 | 新评测台 | recall@8 / MRR 金标；阈值进 pytest |
| G147 | chunk 反馈走 ActivityEvent | 新反馈表 | `kb.feedback` 作检索先验 |
| G148 | 增量重建 + 上月自比雷达 | 方向总分、方向间排名 | `content_hash` 未变则不重嵌；只和自己上月比 |
| G149 | 父方向列表含子课题 | 新课题组页、改 wrap | 文献/手稿/推荐/图谱/未点名走 `direction_scope_ids`；无效方向空列表不假装看全库 |
| G150 | 子方向仍精确；空态写含子课题 | 新筛选产品 | 子方向 exact；父方向 empty_next / 页头写「含子课题」 |
| G151 | 统计筛选写进网址 | 新报表页 | `?days=` `?direction_id=` 与 GET 同步 |
| G152 | CSV 同步；漏斗流失可点；空漏斗是页英雄 | 新漏斗页、排行 | CSV 带同一筛选；`leak_href` / `leak_count` 进现有列表；空漏斗 Alert |
| G153 | 工作台可挂多方向 | 第三角色、`/pi` | `pick_direction` 收 `direction_ids`；主方向仍是 `membership.direction_id` 快照 |
| G154 | KB 上传/文档列表接 direction_id | 拆 KB | Form / `?direction_id=`；父方向列表含子课题 pending |
| G155 | 漏斗流失进驾驶舱 / 周报 / 方向枢纽 | 新指挥页 | 复用 `funnel` + leftover 芯片，不做排行 |
| G156 | 事件趋势去 N+1 | 改口径 | `event_trend` 一次查出再按日分桶 |
| G157 | 枢纽露出漏斗与覆盖 | 新地图页 | overview 用全量计数，不按预览 20 篇 |
| G158 | 夹具 + 文档 + 云落地 | 新产品 | `test_g149_maturity`；FRONTIER / UI / USER；`_cloud_g149*` |
| G159 | 核心点名父方向含子课题钉子 | 新综述页、改 wrap | 手稿挂父方向须点名子课题已钉核心；子方向仍只看自己 |
| G160 | 检索叠父方向及子课题关键词 | 新检索引擎 | `direction_id` 进 search body；无效方向不叠全库词 |
| G161 | 推荐空态与已挂方向语料 | 新推荐页 | 父方向 empty 写含子课题；学生无选向用语料=已挂方向并集 |
| G162 | 图谱 includes_children | 新地图页 | 空图写含子课题；子方向仍精确 |
| G163 | 大纲关键词走方向口径 | 自动建稿 | 父方向叠子课题词；响应带 `keywords` / `includes_children` |
| G164 | 方法卡 related_in_lab 同口径 | 改图边 | 父向文献可相关到子课题篇目；子向不拉兄方向 |
| G165 | 写作页文献/手稿按方向 | 第三条写论文 | 选父方向后选篇含子课题；覆盖文案写及子课题 |
| G166 | 成员「我的」多选；父筛含子课题成员 | 第三角色、`/pi` | 改主方向保留已挂；筛父方向看见挂在子课题的人 |
| G167 | leftover / 指挥面点名文案含子课题 | 新报表、排行 | 卡住/下一步/主张 next_step 写「本方向及子课题」；leftover unused 扫子课题 |
| G168 | 夹具 + 文档 + 云落地 | 新产品 | `test_g159_maturity`；FRONTIER / UI / USER；`_cloud_g159*` |
| G169 | 开读完成判定走范围 | 新阅读产品、改「开始阅读」href | 主方向是父时，读子课题也算已开读；首页/引导 href 仍只带主方向 |
| G170 | 检索空态带方向 | 新检索页 | `search_empty_next` 写含子课题；换源/捕手/上传带同一 `direction_id` |
| G171 | 撰写本章叠范围词 + 方向 KB | 第三条写论文 | `WriteSectionBody.direction_id`；`query_kb(..., direction_id=)`；无效方向 404 |
| G172 | 拓展相关工作叠范围词 | 新译润产品 | `LanguageBody.direction_id` 或手稿已挂方向；父向含子课题词 |
| G173 | 复现列表接 direction_id | 新复现页、改 wrap | 父含子课题；子精确；无效空不装全库；有任务仍返回数组 |
| G174 | 复现空态含子课题 | 改 wrap 语义 | `empty_next` 写本方向及子课题；有任务时状态/诚实列表仍是数组 |
| G175 | 漏斗/枢纽复现未跑带方向 | 新报表、排行 | spine / `leak_href` / `reproduce_not_run_href` 带 `?direction_id=` |
| G176 | 复现页方向筛选与页头 | 新筛选产品 | `?direction_id=` 写进网址；父项标含子课题 |
| G177 | 写作页传 directionId；译润可推断手稿方向 | 自动建稿 | 现有 `writeSection` / `expand`，不新开写作产品 |
| G178 | 夹具 + 文档 + 云落地 + 剩余规划 | 新产品 | `test_g169_maturity`；FRONTIER §4；UI / USER；`_cloud_g169*` |
| G179 | 事件趋势按方向 | 新时序页、排行 | `reading.started` 靠范围文献钉子；`kb.query` 靠 payload；无效方向全日零，不装全库；无 rollup 时用事件点 |
| G180 | KB 查询记下方向 | 拆 KB | emit `payload.direction_id`；活动 / 打开知识库可带 `?direction_id=` |
| G181 | 数据集按关联文献方向 | 新数据产品、改图边 | 经 `DatasetLiteratureLink`→`LiteratureDirectionLink` 推断；父含子课题；无效空 wrap；有数据仍数组 |
| G182 | 发现式空选注入已挂并集 | 新发现式、改主方向语义 | 核心钉子优先；快照 `direction_id` 仍是主方向；`injected_from_hung` |
| G183 | 通知 leftover 盖章 | 改 jump、有记录改 wrap | `unused_titles` + `unused_research_href` 带快照方向；`href` 仍 `/runs/{id}` |
| G184 | 精读辅导叠文献方向词 | 新辅导页 | `_tutor_messages` 带 `direction_keywords`；术语表仍不按方向 CRUD |
| G185 | 译润 / 数据集方向选择器 | 新译润或数据产品 | `?direction_id=` 写进网址；父项标含子课题 |
| G186 | 通知/首页芯片 + 成员复现未跑 | `/pi`、改 stuck | `UnusedTitleChips`；驾驶舱「本方向复现未跑」；创建发现式提示注入已挂方向 |
| G187 | 夹具 | 新产品 | `test_g179_maturity` |
| G188 | 文档 + 云落地 + 剩余规划 | 新产品 | FRONTIER §4；UI / USER；`_cloud_g179*` |
| G189 | 漏斗 / 覆盖无效方向不装全库 | 新报表、改 sunburst | 无效 `direction_id` 用零源；父向仍走已有含子课题的 `by_direction` 行 |
| G190 | 本周 / 本月卡与趋势同范围 | 新时序页 | `reading.started` / `kb.query` / 在投按范围计；链接带 `?direction_id=` |
| G191 | 统计顶栏数字随方向 | 新指挥页 | 筛父向时文献数用含子课题并集；无效方向是零 |
| G192 | 周报漏斗 / 未点名接方向 | 新周报、LLM 周报 | `?direction_id=`；动态列表仍是本周对象 |
| G193 | 发现式预检预告注入篇数 | 自动建稿 | `hung_inject_count` / `hung_inject_text` |
| G194 | 数据集详情露出推断方向 | 新数据产品、加列 | `direction_ids` 仍经关联文献推断 |
| G195 | 统计 / 周报页选择器与面包屑 | 新筛选产品 | 父项标含子课题；写进网址 |
| G196 | 夹具 | 新产品 | `test_g189_maturity` |
| G197 | 文档 + 云落地 + 剩余规划 | 新产品 | FRONTIER §4；UI / USER；`_cloud_g189*` |
| G198 | 统计未点名次数按方向 | 新报表 | `stamp_stats_direction_scope` 用 `research_unused_brief(..., direction_id=)`；无效方向是 0 |
| G199 | 卡住摘要按方向 | 新指挥页、排行 | `build_lab_stuck_brief(direction_id=)`；父向含子课题；href 带方向 |
| G200 | 态势条按方向 | 新指挥页 | 在读/手稿/进行中任务与未点名同范围；学位过期仍看本人/全组 |
| G201 | 周报卡住 / 态势接方向 | 新周报、LLM 周报 | 复用同一套 brief；动态列表仍是本周对象 |
| G202 | 统计页文案写卡住与未点名 | 新筛选产品 | 现有页头，不新开指挥页 |
| G203 | 夹具 | 新产品 | `test_g198_maturity` |
| G204 | 文档 + 云落地 + 剩余规划 | 新产品 | FRONTIER §4；UI / USER；`_cloud_g198*` |
| G205 | 事件对象归属方向 | 新图边、LLM 周报 | `filter_activity_events_by_direction`：文献/手稿挂接，发现式只认快照，复现走文献，数据集经文献推断；无法判定则剔除 |
| G206 | 周报动态按归属过滤 | 新周报、改 items 形状 | 现有 `build_digest` `items` 仍是数组；父向含子课题 |
| G207 | 无效方向动态空表 | 装全实验室 | `items_empty_kind=filter`；文案「不是全实验室合计」；`empty_next` 进该方向文献 |
| G208 | 周报页头与空表 | 新筛选产品 | 现有 Digest 页；选择器写「漏斗与动态」 |
| G209 | 夹具 | 新产品 | `test_g205_maturity` |
| G210 | 文档 + 剩余规划 | 新产品 | FRONTIER §4；UI / USER |
| G211 | 云落地 | 新产品 | `_cloud_g205*` |
| G212 | 成员动态 progress 接方向 | 新动态页、`/pi` | 现有 `GET /labs/{id}/progress?direction_id=` 复用 `filter_activity_events_by_direction`；`items` 仍是数组 |
| G213 | recent_events 复用同一套归属 | 第二套规则 | `recent_events(..., direction_id=)`；过取再截断 |
| G214 | 成员进展 timeline 接方向 | 新档案页 | `GET .../cockpit/members/{user_id}?direction_id=`；默认仍是该成员全组 |
| G215 | 方向枢纽动态改用同一套 | 成员泄漏、新枢纽 | 去掉「成员+文献类型」旁路；父向不再混进并列方向 |
| G216 | 成员进展页选择器与空表 | 新筛选产品 | 写进网址；筛空不回落到未筛时间线 |
| G217 | 夹具 + 文档 + 剩余规划 | 新产品 | `test_g212_maturity`；FRONTIER §4；UI / USER |
| G218 | 云落地 | 新产品 | `_cloud_g212*` |
| G219 | 复现脊柱未跑题名 | 新复现产品、把 unused 套到复现 | `not_run_titles` / `not_run_title_more`；只计已有未实测任务（等候不算）；从未开跑文献不进这个数 |
| G220 | 方向行与统计盖章带题名 | 新统计台 | `by_direction` 带题名；`stamp_stats_direction_scope` 从该行抄，不回落到全组脊柱 |
| G221 | 未跑题名芯片 | 新复现页、排行 | `NotRunTitleChips`；文案写「复现未跑题名，不是发现式未点名」 |
| G222 | 指挥面吃未跑题名 | 新指挥页 | 周报 / 驾驶舱 / 统计 / 首页导师 / 方向枢纽 / 待处理条 |
| G223 | 复现列表从行推导芯片 | 把列表改成对象 | 有任务时仍返回数组；芯片用 `literature_title` + `literature_item_id` |
| G224 | 夹具 + 文档 + 剩余规划 | 新产品 | `test_g219_maturity`；FRONTIER §4；UI / USER |
| G225 | 云落地 | 新产品 | `_cloud_g219*` |
| G226 | 卡住摘要三类题名 | 新卡住页、复用 unused/not_run | `no_card_titles` / `related_work_titles` / `core_missing_titles`；还没方法卡计数写进 stuck_brief |
| G227 | 方向枢纽同一套题名 | 新枢纽 | `build_direction_stuck` 带三类题名；父含子课题，并列隔离 |
| G228 | 卡住条芯片 | 新指挥页、排行 | `NoCardTitleChips` / `RelatedWorkTitleChips` / `CoreMissingTitleChips`；周报 / 驾驶舱 / 统计 / 首页导师 |
| G229 | 文献与手稿列表推导芯片 | 把列表改成对象 | `has_card=0` / `related_work=0` / `core_missing=1` 有记录时仍返回数组 |
| G230 | 方向枢纽页芯片 | 新筛选产品 | 现有枢纽，不新开阅读产品 |
| G231 | 夹具 + 文档 + 剩余规划 | 新产品 | `test_g226_maturity`；FRONTIER §4；UI / USER |
| G232 | 云落地 | 新产品 | `_cloud_g226*` |
| G233 | 无人在读题名 | 新阅读产品、把 no_card 套上去 | `unread_titles` / `unread_title_more`；点进阅读器，不要 `panel=card` |
| G234 | 侧栏线描图标 | 新图标产品、霓虹 | 现有分组侧栏补 `FsGlyph`，纸墨描边 |
| G235 | 模型用量落到现有页 | 新用量页、装成方向 token | `usage_by_feature.href` / `keys_href`；空态去个人模型 |
| G236 | 成员表下一步 | 新档案页、`/pi` | 成员列表带该人文献 / 手稿 href；待接受邀请可回页内 |
| G237 | 文献未读列表与枢纽芯片 | 把列表改成对象 | `reading_status=unread` 有记录时仍返回数组 |
| G238 | 夹具 + 文档 + 剩余规划 | 新产品 | `test_g233_maturity`；FRONTIER §4；UI / USER |
| G239 | 云落地 | 新产品 | `_cloud_g233*` |
| G240 | 枢纽尚未开读学生篇名 | 新档案页、把实验室 unread_titles 抄到 idle | `idle_members` 带 `idle_unread_titles` / `idle_unread_title_more` / `literature_href`；该生未读 ≠ 组内无人在读 |
| G241 | 相关工作单行散文不再误判 | 新写作产品 | `is_related_work_heading`：「还没有相关工作标题。」为假；`Related Works` 仍真 |
| G242 | leftover 三字题名 | 把 leftover 改成对象 | `leftover_title_rows` 只拆 tuple/list；三字符字符串当题名 |
| G243 | 邀请锚点滚动 | 新邀请页 | 成员页待接受邀请滚到 `#invites` |
| G244 | 枢纽 idle 芯片 | 新筛选产品 | `UnreadTitleChips mine`；点进该生未读列表 |
| G245 | 夹具 + 文档 | 新产品 | `test_g240_maturity`；FRONTIER §4；UI / USER |
| G246 | 云落地 | 新产品 | `_cloud_g240*` |
| G247 | 邮件周报不跟筛选 | LLM 周报、把网址方向抄进邮件 | `POST .../digest/email` 忽略 `direction_id`；`email_scope=lab` / `follows_filter=false` |
| G248 | 周报页写清邮件口径 | 新邮件产品 | 筛了方向仍提示「邮件是全实验室，不跟本筛选走」 |
| G249 | 驾驶舱 SSE 与 GET 同一套 | 给驾驶舱硬加方向、新动态页 | `cockpit_lab_events`：limit=12 + 文献上次页；SSE 不再丢富化 |
| G250 | 态势实验室句不再混进方向 | `/degree`、把 token 装成方向用量 | 带方向时 token 写「不是本方向」；过期/GPU/入队写「实验室或本人」 |
| G251 | 邮件 leftover 题名 | 把 leftover 改成对象 | `leftover_title_names`；无人在读 / 方法卡 / 相关工作 / 核心 / 未跑写进 markdown |
| G252 | 夹具 + 文档 | 新产品 | `test_g247_maturity`；FRONTIER §4；UI / USER |
| G253 | 云落地 | 新产品 | `_cloud_g247*` |
| G254 | 术语表不按方向切 | 新表、加 `direction_id` 列 | `GET .../glossary` 忽略方向；有记录仍返回数组 |
| G255 | 知识库 / 译润写清术语口径 | 新术语页 | 方向只限定文档与问答；术语是全实验室 |
| G256 | 辅导历史不按方向切 | 新辅导页 | `tutor_scope=paper`；`direction_id` 不切会话 |
| G257 | 阅读器辅导提示 | `/tutor` | 历史按这篇和本人；方向词只进这一问 |
| G258 | 发现式 unused 与 status 互斥 | 新筛选产品 | 两参数同时出现是筛选空，不把未点名装成该状态 |
| G259 | 夹具 + 文档 | 新产品 | `test_g254_maturity`；FRONTIER §4；UI / USER |
| G260 | 云落地 | 新产品 | `_cloud_g254*` |
| G261 | 日汇总旧行不回填 | 回填改写 `lab_metric_daily` | 新行盖 `rollup_kind`；旧行数字不动 |
| G262 | 父方向趋势诚实 | 新报表页 | 未盖章日写「更早的日行可能只计本节点，没有回填。」 |
| G263 | 上月对比同一句 | 方向间排名 | `compare_month.legacy_note`；子方向 / 实验室合计不装 |
| G264 | 统计 CSV 带口径 | 新导出页 | `trend` / `compare_month` 写 `legacy_rollup` |
| G265 | 统计页图下灰字 | 新图表产品 | `ChartBlock note`；父方向页头复用同一句 |
| G266 | 夹具 + 文档 | 新产品 | `test_g261_maturity`；FRONTIER §4；UI / USER |
| G267 | 云落地 | 新产品 | `_cloud_g261*` |
| G268 | 驾驶舱动态不加方向 | 新动态页、只改 GET 或只改 SSE | `events_scope=lab`；查询里的方向忽略 |
| G269 | SSE 水印少做富化 | 新推送产品 | `cockpit_feed_watermark`：事件 + 上次页不变则 ping |
| G270 | progress / 周报 800 窗盖章 | 全历史扫描 | `scan_window=800` / `scan_capped`；页头写清不是全历史 |
| G271 | 周报演员名批查 | 新周报页 | 一次查出 actor，不再每人 `get` |
| G272 | 日汇总按实验室互斥 | 回填旧行、双写改历史 | `lab_quota_guard` 包住 rollup |
| G273 | 夹具 + 文档 | 新产品 | `test_g268_maturity`；FRONTIER §4；UI / USER |
| G274 | 云落地 | 新产品 | `_cloud_g268*` |
| G275 | 统计方向筛后盖实验室面 | 新报表、`usage_ledger.direction_id` | `ai_scope=lab` / `members_scope` / `directions_chart_scope=lab`；用量不装成本方向 |
| G276 | 成员表跟挂靠 | 把全组数字改成假方向计数 | 有效方向只列 `requester_ids_for_direction`；行内在读/手稿仍盖 `members_counts_scope=lab` |
| G277 | 无效方向空成员表 | 装成全实验室成员 | `by_member=[]` + `members_empty_kind=filter`；有人时仍是数组 |
| G278 | 周报用量同一套 | 把 token 装成方向、新周报 | `GET .../digest` 盖 `ai_follows_direction=false`；CSV 写 scope 行 |
| G279 | 统计 / 周报灰字 | 新图表产品、排行 | 页头写「只列挂在该方向的人」；堆叠图下写仍是全实验室各方向 |
| G280 | 夹具 + 文档 + 剩余规划 | 新产品 | `test_g275_maturity`；FRONTIER §4；UI / USER |
| G281 | 云落地 | 新产品 | `_cloud_g275*` |
| G282 | 侧栏白话名 | 新路由、改 `/cockpit` `/runs` | 「进度」「文献调研」只改人看见的字 |
| G283 | 卡住条 / 概况 / 芯片 | 新帮助中心 | 还没做完 / 本周概况 / 从文献到发表 / 综述没写到 |
| G284 | 工作台与统计页头 | 新报表页 | 长句改成两句人话；表头跟对照表 |
| G285 | 文献调研列表与写作提示 | 新调研产品 | 筛选项与诚实条用「综述没写到」 |
| G286 | 夹具 + 文档 | 新产品 | `test_g282_maturity`；FRONTIER §4；UI / USER |
| G287 | 云落地 | 新产品 | `_cloud_g282*` |
| G288 | 云校验 | 新产品 | 线上 JS 见「文献调研」「还没做完」 |
| G289 | 点问号弹层 | 新帮助页、悬停才出 | `HintTip` 单击「？」弹出；页头半句，细则进弹层 |
| G290 | 页头收短 | 新报表、改路由 | 进度 / 统计 / 周报 / 文献调研一眼能看懂在干什么 |
| G291 | 题名芯片去长注 | 新芯片产品 | 芯片自己说话，对照进弹层 |
| G292 | 统计格与用量 | 新图表产品 | 长列名收短，点问号看口径 |
| G293 | 夹具 + 文档 | 新产品 | `test_g289_maturity`；FRONTIER §4；UI / USER |
| G294 | 云落地 | 新产品 | `_cloud_g289*` |
| G295 | 云校验 | 新产品 | 线上 JS 见「提一个问题，自动写综述」 |
| G296 | 方法卡改白话 | 新对象、改 `has_card` | 栏名「这篇怎么做」；缺了写「还没写方法」；按钮「提炼方法」 |
| G297 | 卡住条去「不是排名」 | 新帮助页 | 半句自己说话，排名说明进点问号 |
| G298 | 阅读器 / 复现 / 写作 | 新分析产品 | 侧栏、复现卡、大纲诚实条跟同一套字 |
| G299 | 夹具 + 文档 | 新产品 | `test_g296_maturity`；FRONTIER §4；UI / USER |
| G300 | 云落地 | 新产品 | `_cloud_g296*` |
| G301 | 云校验 | 新产品 | 线上见「还没写方法」「提炼方法」，不见「方法卡」页头 |
| G302 | 抽出按行拼 | 新翻译表、空格硬拼 | `join_pdf_text_items`：分行、去断词、去页脚、双栏先左后右 |
| G303 | 译文只出正文 | 把说明书当成功 | 提示只出译文；接口拒收 `carefully translate` 一类英文说明书 |
| G304 | 看中文伴读 | 第四个侧栏、PDF 下对照卡 | 顶栏「看中文」左右分栏；不再展示抽出的英文 |
| G305 | 选中即译 | 新 `/translate` 页 | 文字层选区弹层；写入现有笔记 |
| G306 | 本页记住 + 译润让路 | 整篇自动译完 | `sessionStorage`；主按钮离开阅读器；译完才「拿去改」 |
| G307 | 夹具 + 文档 | 新产品 | `test_g302_maturity`；FRONTIER §4；UI / USER |
| G308 | 云落地 | 新产品 | `_cloud_g302*` |
| G309 | 辅导接选区 | 新 `/tutor`、新表 | `TutorBody.excerpt` 进现有 `_tutor_messages`；空页标题改人话 |
| G310 | 选中可问可改 | 第四个侧栏 | 弹层「问这一句 / 拿去改」；仍是 `panel=tutor` 与译润页 |
| G311 | 问这一页 | 新辅导产品 | 侧栏标题与三句常见问法；不要苏格拉底行话 |
| G312 | 润色只出正文 | 把说明书当成功 | 提示只出改好的正文；接口拒收英文说明书 |
| G313 | 改文字页 | 新译润路由 | 页题「改文字」；draft 带模式；主按钮跟人走 |
| G314 | 夹具 + 文档 | 新产品 | `test_g309_maturity`；FRONTIER §4；UI / USER |
| G315 | 云落地 | 新产品 | `_cloud_g309*` |
| G316 | 选区前后文 | 复用 `LanguageBody.selection` | `LanguageBody.context` / `TutorBody.context`；`surrounding_context` + `wrap_language_user` |
| G317 | 长页分块译 | 整篇自动译、新翻译表 | `split_readable_chunks` + `_language_reply_many`；下一块带上一段尾 |
| G318 | 辅导接地选区 | 新 `/tutor` | excerpt 入 extra_corpus 与 payload；`selection_context` 只消歧 |
| G319 | 阅读器改措辞 | 第四侧栏 | 弹层走现有 polish；本页锁定词芯片 |
| G320 | 改文字带前后文 | 新译润路由 | draft.context 进现有译润，不重用 selection 写回 |
| G321 | 夹具 + 文档 | 新产品 | `test_g316_maturity`；FRONTIER §4；UI / USER |
| G322 | 云落地 | 新产品 | `_cloud_g316*` |
| G323 | 选区弹层可读 | 新弹层产品 | 摘引 + 夹住视口 + Esc；对着这几字 |
| G324 | 分块译进度 | 整篇自动译 | SSE `chunk/chunks`；「正在译第 2/5 段」 |
| G325 | 辅导看见选区 | 新 `/tutor` | `excerpt_preview`；气泡引用那几句 |
| G326 | 改文字展开前后文 | 新译润路由 | Collapse 展示并可「不用前后文」 |
| G327 | 锁定词说明 | 术语按方向切 | 本页锁定词一句人话 |
| G328 | 夹具 + 文档 | 新产品 | `test_g323_maturity`；FRONTIER §4；UI / USER |
| G329 | 云落地 | 新产品 | `_cloud_g323*` |
| G330 | 刚才选的还在 | 新选区表 | sessionStorage 记住选区；辅导「问刚才选的」 |
| G331 | 翻页译文不闪空 | 整篇缓存表 | 先留上一页译文，标「正在译本页」 |
| G332 | 选区再译 + 锁定词 | 新 `/translate` | 「再译这几句」；弹层本句锁定词 |
| G333 | 继续问这几句 | 新 `/tutor` | `followup_prompt`；续问仍带 excerpt |
| G334 | 选区译文记住 | 翻译表 | `selZhKey` 与本页译文同一套 session |
| G335 | 夹具 + 文档 | 新产品 | `test_g330_maturity`；FRONTIER §4；UI / USER |
| G336 | 云落地 | 新产品 | `_cloud_g330*` |
| G337 | 方向表收列 | 新方向产品 | 主列方向 / 负责人 / 文献手稿 / 还没做完；卡住改行内芯片 |
| G338 | 空态不套空表 | 新空页 | 没方向不画 15 列表头；导师见新建卡，学生仍等导师 |
| G339 | 夹具 + 文档 | 新产品 | `test_g337_maturity`；FRONTIER §4；UI / USER |
| G340 | 云落地 | 新产品 | `_cloud_g337*` |
| G341 | 顶栏跳转看清 | 暗色主题、新搜索页 | 暖纸底 + 墨绿字；占位「打开组内文献、手稿、任务…」 |
| G342 | 作者 DOI 也能跳 | jump 变学术检索 | 文献吃作者 / DOI / arXiv / 刊物；方向吃关键词；数据集吃说明 |
| G343 | 题名前缀靠前 | 新排序产品 | `jump_match_score`；每类最多 3 条，总 16，不饿死后面的类型 |
| G344 | 下拉分组可回车 | 新命令面板 | 分组 + 副标题；`/` 与 Ctrl+K 聚焦；没有组内对象去文献检索 |
| G345 | 空查询仍空 | 改 jump 语义 | `q=""` 仍 `items=[]`；未命中仍 `search_href` + `empty_next` |
| G346 | 夹具 + 文档 | 新产品 | `test_g341_maturity`；FRONTIER §4；UI / USER |
| G347 | 云落地 | 新产品 | `_cloud_g341*` |

***

## 3. 明确不做

Sci-Hub、付费墙绕过、监控、排行红灯、官方查重、第三角色、主图反馈回环、第三条「AI 写论文」、与现有 IA 重复的侧栏。

## 4. 规划剩余（只加深现有对象）

**已闭合：** F0–F20，G0–G9（原 G 合同），G10–G347（空态 / 选定方向 / 归档解开 / 云端禁回环 / 导师邀请与管理员行锁 / 出站 git / 发现式镜像与同哈希合并 / 阅读器译润对照与辅导对话加深 / `chat()` 重试与可选 SSE / 方法卡分块 / 对比大纲章节评审与知识库出口诚实 / 组会纪要接地 / 提示词收口 / 失败记账 / 综述书目去重与收尾闸 / 检索命中落盘与网页不进 bib / 命中工件与未点名题名 / 发现式 diagnosis 结构化 / 组内未交代与列表短句 / 指挥面吃发现式未点名 / 综述前暂停不误判 / 统计一次算清未点名 / 写作页吃发现式未点名 / 综述前失败不误报 / 写作未点名看作者与正文 / 对比与拓展同一诚实条 / KB 与模拟评审吃未点名 / 方向枢纽吃该方向成员未点名 / 方向未点名列表与周报/图谱诚实 / 同哈希入库与手稿写回并发 / 周报未点名题名收齐 / 成员未点名深链 / 方向快照 / unused 不再扫盘 / 未点名页头写出人名方向 / leftover 芯片 / 筛选与面包屑收干净 / 无快照旧任务不跟现方向 / 手稿卡住页头 / 写作芯片与文献卡住页头 / 复现与发现式状态页头 / FeedbackStrip 题名优先 / 方向枢纽与成员进展 leftover 芯片 / 复现筛选空态诚实 / unused 与 status 互斥 / 数据集库空页英雄 / 方向课题组单元与成员生命周期 / 方向级 RAG 与混合检索 / 转化漏斗与每日 rollup / ECharts 中性可视化 / 统计去 N+1 / 召回评测与增量重建 / 父方向列表含子课题 / 统计网址与漏斗流失 / 工作台多方向 / KB 方向列表 / 指挥面漏斗 / 核心点名含子课题 / 检索叠范围词 / 推荐与图谱空态 / 大纲与方法卡同口径 / 写作选篇 / 成员多挂与 leftover 点名文案 / 开读范围判定 / 检索空态带方向 / 章节与译润叠范围词 / 复现列表与漏斗带方向 / 事件趋势按方向 / 数据集经文献推断方向 / 发现式空选注入已挂并集 / 通知未点名盖章 / 辅导叠文献方向词 / 漏斗无效方向不装全库 / 本周卡与趋势同范围 / 周报漏斗接方向 / 预检预告注入篇数 / 态势条卡住摘要未点名同范围 / 周报动态按事件对象归属方向 / 成员动态与方向枢纽复用同一套归属 / 复现未跑题名芯片 / 还没方法卡与未写相关工作、核心未点名题名芯片 / 无人在读题名 / 侧栏线描图标 / 模型用量落到现有页 / 成员表该人文献与手稿 / 枢纽尚未开读学生篇名 / 相关工作单行散文不再误判 / leftover 三字题名不拆 / 邀请锚点滚动 / 邮件周报不跟筛选 / 驾驶舱 SSE 与 GET 同一套 / 态势实验室句拆开 / 术语表不按方向切 / 辅导历史按篇与本人 / unused 与 status 互斥落地 / 日汇总旧行不回填只盖章 / 驾驶舱动态不加方向 / SSE 水印 / progress 800 窗 / 日汇总互斥 / 统计方向筛后用量与堆叠仍实验室 / 成员表跟挂靠且无效方向空表 / 年份与手稿状态盖实验室口径 / 用户可见行话改成白话 / 页头半句与点问号弹层 / 方法卡改成这篇怎么做 / 阅读器按读论文译 / 选中可译可问可改 / 选区前后文与长页分块译 / 辅导接地选区与阅读器改措辞 / 选区弹层与辅导摘引可读 / 分块译进度 / 刚才选的还在与续问这几句 / 翻页译文不闪空 / 方向表收成可读列 / 顶栏跳转看清并能命中作者 DOI）。

**本刀已落地：** G341–G347。顶栏跳转改成暖纸底墨绿字，能看清占位与输入。仍是组内跳到现有对象，不是学术搜索站。文献可用作者 / DOI / arXiv / 刊物跳到阅读器；方向吃关键词；数据集吃说明。题名前缀排前面。空查询仍 `items=[]`，没有组内对象仍给 `search_href`。学生首页「开始阅读」href 仍只带主方向。通知有记录时仍返回数组。进度 GET / SSE 仍不加方向。

**事件对象归属（本刀合同，下一刀复用，不要另写一套）：**

1. `payload.direction_id` ∈ `resolve_direction_scope` → 属于。
2. `object_type=literature` 且文献挂在范围（父含子课题）→ 属于。
3. `object_type=manuscript|writing` 且手稿挂在范围 → 属于。
4. `object_type=research_run` 且 `run_direction_id` 快照 ∈ 范围 → 属于；无快照则不属于。
5. `object_type=reproduce_run` 且 `literature_item_id` 在范围文献 → 属于。
6. `object_type=dataset` 且推断方向与范围相交 → 属于。
7. `kb_document` / `kb_chunk` 看文档 `direction_id` 或挂接文献。
8. `object_type=direction` 且 `object_id` ∈ 范围 → 属于。
9. 其余无法判定的对象，筛方向时剔除。
10. 无效 / 已归档方向 → 空表，文案写「不是全实验室合计」。

**已诚实、不要再改成方向冒充（看起来像剩余，其实已闭合）：**

| 面 | 现有对象 | 已落地口径 | 禁止再做 |
| --- | --- | --- | --- |
| 驾驶舱最近动态 / SSE | `GET /labs/{id}/cockpit`、`events/stream` | `events_scope=lab`，查询方向忽略 | 只改 GET 或只改 SSE；硬加方向冒充全组 |
| 邮件周报 | `POST .../digest/email` | `email_scope=lab`，不跟网址方向 | LLM 周报、按筛选裁邮件 |
| 态势 token / 学位 / GPU | situation AI / 风险句 | 带方向写「不是本方向」「实验室或本人」 | `/degree`、把 token 改成方向用量 |
| 术语表 | `GET .../glossary` | 忽略方向；有记录仍是数组 | 加 `glossary.direction_id`、把 `domain` 当方向 |
| 辅导历史 | 文献 detail `tutor_sessions` | `tutor_scope=paper` | 新 `/tutor`、按方向切会话 |
| 日汇总旧行 | `lab_metric_daily.metrics_json` | 新行盖 `rollup_kind`，旧行不动 | 回填改写历史数字 |
| 组会 / 看板 | 现有会议与任务 | 筛方向时不出现 | 给它们加 `direction_id` 冒充归属 |
| 统计用量 / 堆叠 / 成员名单 | `GET .../stats`、digest、CSV | 本刀：实验室面盖章；成员表跟挂靠 | `usage_ledger.direction_id`；把行内数字改成假方向计数 |
| 年份分布 / 手稿状态 | 现有 `by_year` / `manuscript_by_status` | 本刀盖 `years_scope=lab` / `status_scope=lab`，页头写清 | 按方向重算直方图冒充本方向 |

**仍薄、下一刀只加深现有对象（不是新产品清单）：**

| 缺口 | 改哪个现有对象 | 禁止 | 为何还薄 |
| --- | --- | --- | --- |
| 统计顶栏手稿合计 / 等 GPU | 现有 `GET /labs/{id}/stats` 的 `manuscript_count` / `awaiting_*` | 新队列页、把等 GPU 装成本方向 | 本刀只盖 `manuscripts_scope=lab` / `gpu_scope=lab`，数字仍是全实验室 |
| 成员行在读 / 手稿 / 未点名 | 现有 `by_member` 行 | 新档案、按卡住排序 | `members_counts_scope=lab`：列出的人跟方向，数字仍是该人全组。重算要先定口径，不要装作已经按方向计 |
| 成员进展 800 窗 | 现有 `GET .../cockpit/members/{id}` | 全历史扫描、新时间线产品 | 已盖 `scan_capped`；窗外的对象像没发生过，不要偷偷加大窗口冒充完整 |
| 数据集方向 | 现有数据集经文献推断 | 给 `datasets` 加 `direction_id` 列 | 推断对，对象上仍没有自己的方向字段；详情已能看见推断结果 |

组会 / 看板任务没有方向列。筛方向时不出现是诚实。`progress` 带方向时在最近 800 条里按归属分页，不是全历史扫描。以上都必须先回答「改哪个现有 endpoint / 表 / 页面」。做完一刀再另开编号，禁止把上表当成可以一次铺开的新产品路线。下一刀不要把 token / 学位过期 / 等 GPU 改成方向冒充。不要回填日汇总旧行，不要给术语表或 `usage_ledger` 加 `direction_id`，不要给驾驶舱硬加方向。不要把成员行在读数字改成未定义的方向计数。

**接棒合同：** G347 之后不在本文件追加切片。读 / 析 / 进度 / 方向 / 拓展见 **`docs/CORE_MATURITY_PLAN.md`**（C 系列）。Agent 运行时、阅读性能底座、新生入门与成员档案见 **`docs/AGENT_ERA_PLAN.md`**（A 系列，A0–A14）。该文件经产品负责人授权解禁三项：平台侧 Agent 运行时（A-AGENT）、成员档案与入门语料（A-PROFILE）、`build_graph.py` 边语义与 checkpointer（A-ENGINE）。本节下方「主图反馈回环」与「`paper_reproducer` 统一编排」两条后置项由 A-ENGINE 取代；本文件 §0 的 `D-LITENG`「禁止改 `build_graph.py` 边语义」同样由 A-ENGINE 取代。其余口径（口径陷阱、事件对象归属十条、`D-HONEST`）继续有效，A 系列不得突破。

**仍后置，禁止用新产品冒充：** 企微、会议 ASR、H6 跨实验室合并驾驶舱、官方查重、暗色主题、Sci-Hub、用 LLM 周报代替可复算数字、排行红灯、第三角色、`/pi` `/atlas` `/degree`、Scholar 默认入列。学生首页「开始阅读」仍只带方向。大纲不得自动建稿。通知列表有记录时仍返回数组，不改 jump 语义。数据集不要新增 `direction_id` 列，继续经文献推断。sunburst 保持每向一行，不要把父切片改成只含本节点。
