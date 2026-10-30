# Agent 上下文压缩管线的完整性攻防（研究工件）

这不是产品功能目录。这里把 OpenLens / 蜂算里**已经存在的压缩、路由、sidecar 注入**当成真实实验平台，对应博士选题：

> 别人防的是「注入了什么」；我们管的是「注入内容在 agent 记忆里怎么被搬运、压缩、伪装成系统指令」。

## 先说清楚：上面那些脚本在干什么

不是在给蜂算加新业务页。是在做三件事：

1. **攻击可复现**：用仓库里的真函数（`vector_search`、`route_by_keywords`、`job_context`、`literature_search_should_stop`）跑 PoC，证明问题不是空想。
2. **度量**：提出「上下文持久性」——一段 payload 能在多少轮压缩后还活着。
3. **防御原型**：HMAC + Merkle 的可裁剪签名，接到压缩路径上（默认关闭，不影响现有任务）。

## 怎么跑

在仓库根目录：

```text
python -m research.context_integrity.eval.test_crypto
python -m research.context_integrity.poc.poc1_role_collapse
python -m research.context_integrity.poc.poc2_context_persistence
python -m research.context_integrity.poc.poc3_sandbox_writeback
python -m research.context_integrity.poc.poc4_failopen_overflow
python -m research.context_integrity.poc.poc5_control_and_stop
python -m research.context_integrity.eval.run_attacks
python -m research.context_integrity.eval.run_defense
```

结果写在 `results/attacks.json` 和 `results/defense.json`。

打开防御（可选）：

```toml
[context]
authenticated_context = true
```

或环境变量 `OPENLENS_AUTHENTICATED_CONTEXT=1`，密钥 `OPENLENS_CONTEXT_KEY`。

## 期刊短文（中文稿）

体例按国内信息安全期刊短文：题目、摘要、关键词、引言、相关工作、方法、实验、结论、参考文献。数字与 `results/*.json` 一致。

- [papers/paper1_rank_and_survive.md](papers/paper1_rank_and_survive.md)　攻击与度量
- [papers/paper2_authenticated_context.md](papers/paper2_authenticated_context.md)　可认证上下文
- [papers/sok_agent_memory.md](papers/sok_agent_memory.md)　记忆安全综述

投稿时可再改为英文并补作者单位、基金与正式图表。
