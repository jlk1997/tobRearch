# 讲义：下一词理论哪里不够，别人改了什么

配合大纲 [TEACHING_SYLLABUS.md](TEACHING_SYLLABUS.md) 第 11 课、第 18～22 课；第 33 课创新工坊会再引用本页的缝。  
先会第 1、4、6 课的流水线再读。不会「S 给词表打分」时不要在这里改信仰。

---

## 四句先钉住

1. **不是整条大模型路线走错了。** 下一词预测把「会说话」做成了可规模化工程。
2. **觉得怪是对的。** 流水线里没有「真假 / 计划 / 世界」格子；优化的是「听起来像训练数据」。
3. **幻觉不是 softmax 抽奖独有的 bug。** 抽奖只是一层；更硬的是目标层和累积层。
4. **GitHub 上要分清：多数只换发动机（省算力），少数才换交通规则（先想后说、先建世界模型、或把真假交给外面）。** 后者还没有取代通用对话引擎。

---

## 你已经会的流水线（对照用）

```text
字 → 编号 → 一排数 → 网络 → 一排总结 S
  → 和每个字那排乘加得到整表 logits
  → softmax 归一化
  → 按概率选下一个字（可取最大，可抽）
  → 把这个字接回去，再算下一轮
```

全程问的都是：**像不像下一词。**  
没有：指不指得到世界上的事、证据在哪、谁说的、先列提纲再填词。

所以它能写出流畅的假文献——和论文一「压缩撕掉是谁说的」是同一类：流畅 ≠ 有根据。

---

## 把「错」拆成三层（不要糊成一个「幻觉」）

### 抽奖层

温度 > 0 时，不必选 logits 最大的那个。  
贪心 / 低温能压这层，但压不死「说得像真的」。

### 目标层（更根本）

训练目标是续写文本，不是核对世界。  
没见过的生日、假 DOI，模型仍会编一个「像论文的下一词」。  
流水线里没有「查真假」工位，所以和词表、编号、乘加打分完全兼容。

Kalai & Vempala（STOC 2024）：校准得好的语言模型，对「训练集里只出现过一次的任意事实」，统计上就必须胡说一部分。  
算术、反复出现的书目等「系统事实」没有同样的必然胡说——更吃工具和验算，不是再堆参数。

评测常惩罚「我不知道」，逼模型猜（OpenAI, 2025）。系统被训练成宁猜不闭嘴。

### 累积层

自回归用自己刚吐的词当下一步输入。前面一个似是而非的词，会把后面整段带歪。  
人通常先有要点再发声；模型是边说边想。

---

## 一张对照：改了什么，没改什么

| 还在「编号 → S → 打分 → 抽词」 | 没离开当前理论 |
| --- | --- |
| 能改前面已经写出的词 | 缓解累积错 |
| 预测的是表示不是字 | 在建世界模型 |
| 外面有检索 / 证明 / MAC | 在补真假格子 |

---

## 五营（按改了什么分，不按星星数吹）

### A 营：换骨架，不换理论

仍是下一词语言模型。线性时间、少 KV cache。幻觉机制几乎原样。

- [BlinkDL/RWKV-LM](https://github.com/BlinkDL/RWKV-LM)（RWKV-7 Goose）
- [state-spaces/mamba](https://github.com/state-spaces/mamba)
- [NX-AI/xlstm](https://github.com/NX-AI/xlstm)

主航道大模型更多是 MoE 省激活量，骨架仍常是 Transformer。

**可优化吗：** 智能体记忆很长时，骨架能降成本；**角色坍缩不会因为换成 Mamba 就消失。**

### B 营：还是词表，但可以改前面的字

掩码 / 离散扩散：并行填空，回头改稿。缓解「说错一个后面全歪」。  
目标仍是像不像人话。

- [ML-GSAI/LLaDA](https://github.com/ML-GSAI/LLaDA)
- [inclusionAI/LLaDA2.X](https://github.com/inclusionAI/LLaDA2.X)
- [MinkaiXu/Energy-Diffusion-LLM](https://github.com/MinkaiXu/Energy-Diffusion-LLM)

**实验室缝：** 主流水印打在 softmax 前的 logits。扩散不是从左到右锁死，印还在不在、检测统计量怎么改，是可做的优化题，不是口号。

### C 营：换目标——预测表示，不预测下一词

LeCun：在表示空间预测「接下来世界会怎样」，丢掉灯光、同义改写这些不可预测细节，用来规划。

- 蓝图：*A Path Towards Autonomous Machine Intelligence*（2022）
- [facebookresearch/ijepa](https://github.com/facebookresearch/ijepa)
- [facebookresearch/vjepa2](https://github.com/facebookresearch/vjepa2)

现状：视觉 / 机器人能跑，不是 ChatGPT。批评：潜空间可能塌缩、预测不接地。

**可优化吗：** 不要幻想学期内重做 JEPA。可问：智能体记忆能不能「预测下一块该留什么表示」，而不是只按 rerank 分切块。

### D 营：小网络反复改答案

隐空间里多步迭代，近似「先想后写」。强项是谜题，不是开放聊天。宣传要打折。

- [sapientinc/HRM](https://github.com/sapientinc/HRM)
- [SamsungSAILMontreal/TinyRecursiveModels](https://github.com/SamsungSAILMontreal/TinyRecursiveModels)

**可优化吗：** 路由 / 停止条件若改成「隐状态收敛再输出」，控制流劫持面会不会变。先有指标再谈模型。

### E 营：不让神经网络独自负责真假

目前唯一能大规模压幻觉的实践：检索、代码解释器、求解器、符号约束。

- [HEmile/neurosymbolic-diffusion](https://github.com/HEmile/neurosymbolic-diffusion)
- RAG、引用必查、AlphaGeometry 一类「网络提议 + 符号验证」

**和课题同哲学：** 模型输出必须能被外面卡住。论文二的 HMAC / Merkle / 信息流就是 E 营在记忆上的版本。

---

## 和你课题怎么接（比改行发明新骨架更值钱）

- 论文一：压缩撕角色。换 A 营骨架照样塌，除非记忆带认证。
- 论文二：在 E 营补真假格子，不是在模型里变出良心。
- 水印：打在 B 营会动的那一层（logits / 采样）。压缩会删印、扩散会改采样顺序。
- 博士切口默认是：**引擎会流畅地错 → 系统层必须可验证。** 不是学期内替代 Transformer。

---

## 读完自我检查

1. 三层错误各举一个自己编的例子。
2. 五营各用一句话说「改了什么、没改什么」。
3. 指出一条：只有系统层能补、换骨架补不上的缺口。
