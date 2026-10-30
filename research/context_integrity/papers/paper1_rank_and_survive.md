# 面向智能体上下文压缩管线的持久化投毒攻击与上下文持久性度量

**摘要：** 长周期人工智能智能体普遍依赖上下文压缩以突破上下文窗口限制。现有提示注入与智能体安全研究多假设送入模型的消息仍保留可信的角色边界，因而将防御重点置于模型对齐与输入隔离。本文指出，检索重排序式压缩会将多角色对话序列化为纯文本并重新封装为用户消息，从而使角色元数据失效，外部不可信内容可在压缩后伪装为系统或工具指令。在此基础上，本文将压缩管线建模为独立攻击面，提出 Rank-and-Survive 攻击：攻击者通过提高恶意片段在重排序器中的相关性得分，使其在多轮压缩中持续存活；并定义上下文持久性指标，包括连续存活轮数、存活曲线面积与半衰期。本文以开源多智能体科研系统 OpenLens 为对象，在真实压缩、路由与旁路注入路径上复现 7 类攻击。实验表明，攻击成功率为 100%；未经优化的注入在 8 轮压缩中存活 0 轮，经查询词填充优化后存活 8 轮；污染可沿文献综述传播至规划、编码与论文生成子图，跨子图传播比为 0.75。结果表明，压缩管线本身构成可被量化的完整性风险，现有文本隔离防御在压缩后不再成立。

**关键词：** 智能体安全；提示注入；上下文压缩；记忆完整性；持久化投毒

**中图分类号：** TP309；TP18　　**文献标志码：** A

---

## 0 引言

大语言模型驱动的智能体通过工具调用、外部检索与多轮规划完成长周期任务。受上下文窗口约束，实用系统普遍引入压缩机制：将历史消息切分、按与当前查询的相关性重排序，并截断至预算之内。Claude 的 compact、MemGPT 的记忆换入换出、LangGraph 的摘要节点以及 OpenLens 的 `vector_search` 均属此类。

既有智能体安全工作主要关注模型是否执行不可信输入中的指令，代表性防御包括数据打标（Spotlighting）、结构化查询隔离（StruQ）、指令层级与能力安全架构（CaMeL）。上述方法隐含假设：送入模型的上下文仍为带有系统、用户、助手、工具等角色标注的原始消息序列。一旦压缩将角色信息降级为可伪造的文本前缀，并统一封装为用户消息，则角色边界不再构成可信原语，既有防御的前提被破坏。

本文将研究问题由“模型是否听从恶意指令”转为“恶意内容在智能体自身的压缩记忆中能否存活并获得错误的身份标签”。主要贡献如下：

（1）将智能体上下文压缩管线形式化为独立攻击面，给出角色坍缩、排序生存、失败开放、溢出扁平化、沙箱回流、停止条件操纵与控制流劫持等七类攻击的定义，并与 OpenLens 实现一一对应。

（2）提出 Rank-and-Survive 攻击目标函数与上下文持久性度量，用于刻画载荷在多轮压缩下的存活能力，并与面向外部知识库的投毒攻击加以区分。

（3）在真实多智能体科研流水线上完成可复现测量。7 类攻击全部成功；查询词优化使载荷存活轮数由 0 提升至 8；跨子图传播比为 0.75。

本文其余部分安排如下：第 1 节综述相关工作；第 2 节给出系统模型与威胁模型；第 3 节定义攻击分类与度量；第 4 节报告实验；第 5 节讨论局限；第 6 节总结。

## 1 相关工作

### 1.1 提示注入与智能体攻击

直接提示注入通过覆盖系统指令改变模型行为。间接提示注入将指令嵌入模型随后读取的网页、邮件或文档。针对工具增强智能体，AgentDojo、InjecAgent 等基准评估了工具调用被劫持的成功率。PoisonedRAG 与 AgentPoison 研究检索增强生成中的语料投毒，其目标是提高恶意文档被检索的概率。上述工作均以“载荷进入当前提示”为成功条件，并不刻画载荷在智能体内部记忆被反复压缩后的存活过程。

### 1.2 智能体防御

Spotlighting 通过对不可信文本插入间隔符提示模型将其视为数据。StruQ 在训练或推理阶段隔离指令与数据通道。SecAlign 以偏好优化降低模型对注入的遵从。指令层级赋予系统消息高于用户与工具消息的优先级。CaMeL 以能力与信息流约束工具副作用。上述防御作用于提示构造或模型行为，并不在压缩器处维护来源认证；若压缩销毁角色标签，则隔离与层级均失去可执行的依据。

### 1.3 长上下文与记忆系统

MemGPT 将上下文视为操作系统内存并换页。LangGraph 提供基于摘要的检查点压缩。OpenLens 采用远程重排序模型对历史消息切块打分，再将入选片段统一封装为用户消息。既有记忆系统文献关注召回率与任务完成率，较少讨论压缩对完整性语义的破坏。

与上述工作的区别在于：本文将压缩器本身作为攻击目标，提出面向内部记忆而非外部语料库的持久化目标函数，并在真实系统上测量管线级完整性，而非单一模型的遵从率。

## 2 系统模型与威胁模型

### 2.1 系统模型

考察由编排器驱动的多智能体流水线。共享状态包含消息序列 \(M=(m_1,\ldots,m_n)\)，每条消息具有角色 \(r_i\in\{\mathsf{system},\mathsf{user},\mathsf{assistant},\mathsf{tool}\}\) 与内容 \(c_i\)。当 \(\mathrm{tok}(M)\) 超过预算 \(B\) 时，压缩器 \(C\) 执行：

（1）将 \(m_i\) 序列化为字符串 \(s_i=\mathrm{fmt}(r_i,c_i)\)，角色成为文本前缀；

（2）按查询 \(q\) 对切块打分并选取子集，使总长度不超过 \(B\)；

（3）将入选切块重新封装为用户消息。OpenLens 中对应实现还将顺序取反。

编排器依据助手输出中的保留字（如 \(\texttt{DECISION: END}\)）决定子图迁移，并依据文献覆盖启发式决定是否终止检索。作业旁路文件（如 `lab_context.json`）可被拼接至后续提示前端。代码执行发生在挂载作业工作区的容器内。

### 2.2 威胁模型

攻击者不能修改编排器代码，不能获取模型服务密钥。攻击者可控制智能体将主动读取的外部对象，包括学术论文全文、网页摘要，以及在编码阶段由容器写入工作区的旁路文件。攻击目标为完整性而非可用性或机密性，具体包括：销毁或伪造角色边界；使恶意片段在不少于 \(N\) 轮压缩后仍存在于上下文；劫持控制流或提前终止检索；通过旁路文件持久污染下游智能体。

本文不假设攻击者能够直接调用大模型，也不以模型生成有害文本作为唯一成功判据。成功定义为管线可观测状态满足攻击谓词，例如压缩后仅保留用户角色、路由输出被篡改、覆盖计数器被满足等。

## 3 攻击方法与度量

### 3.1 压缩管线攻击分类

**定义 1（角色坍缩）**　若存在压缩结果 \(M'=C(M,q,B)\)，使得 \(\forall m\in M'\) 均有 \(r(m)=\mathsf{user}\)，且原系统消息的角色标签不可恢复，则称发生角色坍缩。攻击者可在工具返回的论文正文中嵌入伪造的 \(\texttt{System:}\) 或 \(\texttt{Tool:}\) 轮次；坍缩后该文本与真实系统消息不可区分。

**定义 2（排序生存）**　载荷 \(p\) 在第 \(t\) 轮压缩后仍出现，当且仅当其切块在重排序中的得分进入预算 \(B\)。因此，提高 \(p\) 与 \(q\) 的表面相关性即提高其存活概率。本文将查询词填充作为实现该目标的具体手段。

**定义 3（失败开放）**　当重排序服务不可用时，若压缩器不中止任务而静默丢弃大部分历史，则可用性故障转化为完整性故障。OpenLens 在重排序失败时将得分置零并可能仅保留循环中的最后一切块，系统提示随之丢失。

**定义 4（溢出扁平化）**　当模型接口返回上下文超限错误时，若实现将全部消息字符串化并截取尾部、再封装为单一用户消息，则系统提示（通常位于头部）被丢弃，攻击者可控的尾部被保留。

**定义 5（沙箱回流）**　若容器对宿主导出目录具有写权限，且编排器优先读取该目录中的旁路文件并前置到后续提示，则一次写入可持久影响规划与编码智能体。

**定义 6（停止条件操纵）**　若检索终止取决于消息文本中学术标识符的计数，则伪造若干 DOI 或 arXiv 编号即可使计数器满足阈值，造成覆盖饥荒。

**定义 7（控制流劫持）**　若子图路由对助手输出做子串匹配，则模型引用不可信文献中的保留字与其主动决策不可区分，路由结果被劫持。

### 3.2 上下文持久性

设压缩轮次 \(t=1,\ldots,N\)。定义示性变量 \(s_t\in\{0,1\}\)：若预先植入的金丝雀字符串在第 \(t\) 轮压缩后的上下文中出现，则 \(s_t=1\)。

**定义 8（上下文持久性）**　连续存活轮数 \(L=\max\{k:s_1=\cdots=s_k=1\}\cup\{0\}\)；存活曲线面积 \(\mathrm{AUC}=\frac{1}{N}\sum_{t=1}^{N}s_t\)；半衰期 \(T_{1/2}\) 为满足此后 \(s_t\) 恒为 0 的最小 \(t\)，若不存在则记为 \(\infty\)。

**定义 9（Rank-and-Survive）**　在不修改 \(C\) 的前提下，构造 \(p\) 以最大化 \(L\) 或 \(\mathrm{AUC}\)。与 PoisonedRAG 的差异在于：后者优化外部语料被检索的概率，前者优化载荷在智能体内部历史中跨越压缩轮次的存活率。

### 3.3 跨子图传播

OpenLens 主图为线性流水线。若占位符 \(\{\texttt{literature\_report}\}\) 或 \(\{\texttt{lab\_context}\}\) 的替换文本含有载荷，则主管规划被污染，其输出计划进一步进入编码与 LaTeX 子图。定义传播比 \(\rho=n_{\mathrm{hit}}/n_{\mathrm{sub}}\)。

## 4 实验与分析

### 4.1 实验设置

被测系统为 OpenLens 智能体流水线中的真实函数，包括 `vector_search`、`route_by_keywords`、`literature_search_should_stop`、`read_job_sidecar` 与 `prepend_lab_context`。为在无远程密钥条件下复现排序机制，重排序器采用与查询词项重叠成正比的确定性替代模型；失败开放实验通过返回非法响应触发原实现的失败分支。任务场景为重症监护心脏骤停前生命体征恶化的文献综述，与系统公开示例一致。压缩预算在角色坍缩实验中取总令牌数的 60%，在持久性实验中固定为 600 令牌、共 8 轮，每轮追加与查询同主题的良性活动以形成竞争。

评测指标包括：攻击成功率（ASR）、上下文持久性（\(L\)、AUC）、跨子图传播比。成功谓词与第 3 节定义一致，不依赖闭源模型的生成内容。

### 4.2 攻击成功率

表 1 给出 7 类攻击的观测结果。ASR 为 \(7/7=1.00\)。

**表 1　压缩管线攻击结果**

| 攻击类型 | 观测 | 成功谓词是否满足 |
| --- | --- | --- |
| 角色坍缩 | 9 条消息（含 system/user/assistant/tool）压缩为 3 条，角色均为 user；伪造系统轮次与保留字仍在 | 是 |
| Rank-and-Survive | 见第 4.3 节 | 是 |
| 沙箱回流 | 工作区旁路文件被优先读取并置于后续提示顶部，同时注入主管失败日志 | 是 |
| 失败开放 | 重排序不可用时 8 条消息静默变为 0，系统提示丢失且无异常 | 是 |
| 溢出扁平化 | 4 条消息变为 1 条用户消息，头部系统提示丢失，尾部攻击文本保留 | 是 |
| 控制流劫持 | 助手引用文献中的 `DECISION: END` 后，路由输出为结束 | 是 |
| 停止条件操纵 | 良性状态唯一论文数 1、不停；植入 3 个伪造 DOI 后唯一论文数 3、停止 | 是 |

角色坍缩表明，压缩后攻击者无需欺骗模型“忽略安全规则”，只需使伪造角色前缀成为上下文中唯一可见的身份信号。失败开放表明，将重排序依赖视为尽力而为的优化会把可用性故障升级为完整性故障。控制流与停止条件表明，以自由文本子串作为编排原语时，引用与决策不可区分。

### 4.3 上下文持久性

表 2 比较两种载荷。二者携带相同注入指令与不同金丝雀。朴素载荷不含查询词填充；优化载荷重复嵌入任务查询词项。

**表 2　上下文持久性（\(N=8\)，预算 600 令牌）**

| 载荷 | 存活曲线 | \(L\) | AUC | 半衰期 |
| --- | --- | --- | --- | --- |
| 朴素注入 | \(00000000\) | 0 | 0.00 | 1 |
| 查询词优化 | \(11111111\) | 8 | 1.00 | \(\infty\) |

优化载荷在全部 8 轮中保持存活，朴素载荷在第 1 轮即被挤出。该结果支持定义 9：在固定压缩器下，存活由重排序得分决定，而非由载荷是否“看起来像攻击”。与外部语料投毒不同，攻击者优化的是智能体对自身历史的选择策略。

### 4.4 跨子图传播

当 \(\{\texttt{literature\_report}\}\) 含保留字且 \(\{\texttt{lab\_context}\}\) 含沙箱指令时，文献子图本身未必在当轮被判定为污染，但主管、编码与 LaTeX 子图被污染，\(\rho=3/4=0.75\)。线性流水线缺乏回边并不阻碍前向污染；相反，不可逆的前向边使污染一旦进入规划阶段即难以隔离。

### 4.5 与现有防御的关系

Spotlighting 与 StruQ 作用于提示构造阶段的文本表面。压缩将带标记或已过滤的文本再次序列化并封装为用户消息后，标记不再绑定角色，字面过滤也无法恢复被删除的角色通道。因此，将上述方法作为压缩后的唯一防线是不充分的。面向模型遵从率的评测会低估管线级风险：即使模型拒绝执行伪造系统指令，路由与停止条件仍可能仅因子串匹配而改变控制流。

## 5 讨论与局限

第一，重排序器在本文中采用词项重叠替代模型，用以在无密钥条件下隔离“排序即生存”机制。正式推广前应在线上交叉编码器（如 bge-reranker）上重复持久性曲线实验。机制本身不依赖特定打分函数，只要压缩器按与查询的相关性丢弃历史，查询词填充即构成合理的对抗目标。

第二，本文测量的是编排器可观测状态，而不是特定闭源模型是否口头遵从注入。该选择是有意的：若角色边界在进入模型前已被销毁，则模型遵从率无法还原已丢失的完整性语义。

第三，沙箱回流属于链式攻击，其前提是编码智能体已在容器内执行写操作。单一恶意 PDF 并不足以直接写盘；完整链路为间接注入进入编码提示，再由代码执行写入旁路文件。论文陈述应保持该区分。

第四，本文未在 MemGPT、官方 LangGraph 摘要节点等其他记忆实现上重复全部七类攻击。将上下文持久性作为跨系统基准是后续工作。

## 6 结论

本文论证了智能体上下文压缩管线构成独立于模型对齐的完整性攻击面。压缩过程中的序列化、相关性截断与角色重封装使外部内容能够伪造系统身份、劫持控制流并在多轮压缩中存活。Rank-and-Survive 与上下文持久性为该现象提供了可计算的攻击目标与度量。在 OpenLens 上的复现实验表明，七类攻击均可稳定触发，查询词优化可将存活轮数由 0 提升至 8，污染可沿流水线向前传播。后续工作将研究在压缩后仍可验证的来源绑定，以及在其他记忆架构上的跨系统测量。

## 参考文献

[1] GRESHKE K, ABDELNABI S, MISHRA S, et al. Not what you've signed up for: compromising real-world LLM-integrated applications with indirect prompt injection[C]//Proceedings of the 16th ACM Workshop on Artificial Intelligence and Security. New York: ACM, 2023: 79-90.

[2] LIU Y, DENG G, LI Y, et al. Prompt injection attack against LLM-integrated applications[J]. arXiv preprint arXiv:2306.05499, 2023.

[3] HINES K, LOPEZ I, et al. Defending against indirect prompt injection attacks with Spotlighting[J]. arXiv preprint arXiv:2403.14720, 2024.

[4] CHEN S, PIET J, SITAWARIN C, et al. StruQ: defending against prompt injection with structured queries[C]//Proceedings of the 33rd USENIX Security Symposium. 2024.

[5] CHEN S, ZAREMBA W, et al. SecAlign: defending against prompt injection with preference optimization[J]. arXiv preprint arXiv:2410.05451, 2024.

[6] WALLACE E, XIAO K, LEIKE R, et al. The instruction hierarchy: training LLMs to prioritize privileged instructions[J]. arXiv preprint arXiv:2404.13208, 2024.

[7] DEBENEDETTI E, et al. AgentDojo: a dynamic environment to evaluate prompt injection attacks and defenses for LLM agents[C]//Advances in Neural Information Processing Systems, 2024.

[8] ZHAN Q, et al. InjecAgent: benchmarking indirect prompt injections in tool-integrated large language model agents[C]//Findings of ACL, 2024.

[9] ZOU W, GENG R, WANG B, et al. PoisonedRAG: knowledge corruption attacks to retrieval-augmented generation of large language models[C]//Proceedings of the 34th USENIX Security Symposium. 2025.

[10] CHEN Z, et al. AgentPoison: red-teaming LLM agents via poisoning memory or knowledge bases[C]//Advances in Neural Information Processing Systems, 2024.

[11] DEBENEDETTI E, ROCHER L, et al. Defeating prompt injections by design: CaMeL[J]. arXiv preprint arXiv:2503.18813, 2025.

[12] PACKER C, WOODERS S, LIN K, et al. MemGPT: towards LLMs as operating systems[J]. arXiv preprint arXiv:2310.08560, 2023.

[13] CHEN J, et al. OpenLens AI: fully autonomous research agent[J]. arXiv preprint arXiv:2509.14778, 2025.

[14] WANG L, MA C, FENG X, et al. A survey on large language model based autonomous agents[J]. Frontiers of Computer Science, 2024, 18: 186345.
