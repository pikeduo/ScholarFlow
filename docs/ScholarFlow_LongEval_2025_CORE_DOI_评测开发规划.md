# ScholarFlow LongEval 2025 CORE DOI 评测开发规划

> 文档状态：Phase 0–3 的离线适配已完成；Dev20 的受控来源快照、集合封存、候选覆盖诊断和 A/B/C/D 离线排序与 DOI-strict 评分已完成。独立 Validation100 已封存并生成调用前计划；BGE-M3 是当前开发集最佳排序配置。  
> 范围：仓库分析、LongEval 数据集适配、DOI Gold 评测与受控实验设计  
> 非目标：不修改生产检索，不调用学术 API、DeepSeek 或本地排序模型。

## 1. 背景与目标

ScholarFlow 的生产链路为：

```text
自然语言查询 → Query Agent → QueryIntent → 多轮候选召回
→ 归一化 / 身份融合 / RRF / 确定性过滤
→ BGE-M3 → Cross Encoder → DeepSeek 核验 → Final Top-K
```

PaSa AutoScholarQuery 的 Gold 主要使用标题和 arXiv ID，而生产来源更常返回 DOI、OpenAlex ID、Semantic Scholar ID、PMID 或 DBLP Key。即使论文相同，也可能无法以同一种标识稳定确认。因此保留 PaSa 作为复杂科研查询的补充评测，同时建立 LongEval 2025 CORE Sci-Retrieval DOI Track，作为严格的论文身份检索与排序评测。

LongEval 不是问答数据集，而是：

```text
Query → qrels → Relevant CORE documents
```

DOI Track 的目标是将其转换为：

```text
Query → qrels → CORE document metadata → normalized Gold DOI Set
ScholarFlow Query → Final Top-K → normalized Prediction DOI Set
Gold DOI == Prediction DOI → hit
```

此轨道不使用标题、作者、年份、模糊匹配或 LLM 来补 DOI 命中。

## 2. 当前仓库基线

### 2.1 可直接复用的能力

| 能力 | 现有实现 | LongEval 中的用途 |
| --- | --- | --- |
| 论文契约 | `EvaluationPaper`、`GoldQuery`、`PredictionRecord` | Gold、预测与结构化结果统一承载 |
| 身份规范化 | 生产 `normalize_doi()`，评测层复用生产纯函数 | DOI 规范化与严格匹配的唯一基础 |
| 常规指标 | Precision、Recall、F1、MRR、二元 nDCG、Macro/Micro | DOI Track 的通用检索报告 |
| 候选快照 | 规则过滤后、BGE-M3 前的 `CandidateSnapshot`，含 SHA-256 | 离线 A/B/C/D 共享输入、Candidate Recall |
| 离线排序 | RRF、BGE-M3、Cross Encoder、DeepSeek 对照与阶段 trace | 排序消融与阶段诊断 |
| 固定子集 | `gold-subset-select`、manifest、输入哈希 | Dev20、Validation100、Future100 封存 |
| 成本控制 | `usage-forecast`、显式确认哈希 | 在线调用前预算确认 |
| 覆盖诊断 | `coverage-diagnose` | 现有候选快照的零命中分析 |
| 效率与结构 | 原始 usage 汇总、字段完整度、关系边校验 | 20% 效率和 10% 结构化代理报告 |

### 2.2 当前实现与 DOI Track 的差异

1. 通用 `papers_match()` 在双方没有可比较强标识时允许标题、年份、作者回退；DOI Track 必须完全禁用该回退。
2. `dataset-gold-import` 只导入已准备的单文件金标，不能安全完成 LongEval queries、qrels、documents 的跨文件关联、DOI 覆盖审计和排除清单生成。
3. 当前 nDCG 为二元 nDCG。qrels relevance 的真实类型、值域、方向和语义必须先由 Phase 0 的真实数据确认，不能预设 graded nDCG 映射。
4. 生产端已有候选计数、最终论文和若干截断统计，但没有持久化 BGE、Cross Encoder、DeepSeek 每一阶段的论文 ID 集合，无法完整解释“在哪一阶段丢失 Gold DOI”。
5. 现有候选快照明确将 `actual_http_requests`、`retry_count`、`rate_limit_count` 设为未观测值；报告必须保留 `N/A`，不得写为零。

## 3. 已确认的本地数据 schema 与 Phase 0 结果

2026-08-22 已用 `longeval-audit` 对用户本地解压数据完成只读扫描。文件均为 UTF-8：Train queries 是 `query_id<TAB>query`；Train qrels 为 `query_id snapshot document_id relevance`；Held-out/Future qrels 为 TREC 四列 `query_id 0 document_id relevance`。documents 为 JSONL 分片，已确认字段包括 `id`、`title`、`abstract`、`authors`、`doi`、`arxivId`、`pubmedId`、`publishedDate` 等。

审计规则为 `relevance > 0` 形成 Gold 候选；DOI 必须先经生产 `normalize_doi()` 归一化，再满足 `^10\\.\\d{4,9}/\\S+$`。不以任何其他 ID 或文本补齐。

| Split | Query | qrels | DOI Gold | DOI-eligible | excluded_no_doi_gold | DOI coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 393 | 4,262 | 1,958 | 365 | 28 | 55.64% |
| Held-out | 99 | 947 | 206 | 65 | 34 | 26.30% |
| Future | 492 | 5,017 | 1,823 | 455 | 37 | 44.95% |
| Total | 984 | 10,226 | 3,987* | 885 | 99 | — |

`*` 为各 split 去重 DOI 数之和，不代表跨 split 全局去重数。逐 query eligibility 及输入哈希见 `data/evaluation/longeval_2025/reports/longeval-audit/`。

审计同时发现：Held-out 有 424 个正相关 qrels document ID 未出现在测试 documents；测试 documents 对正相关 ID 有重复记录（Held-out 526，Future 5,001），Future 其中 1 条 DOI 状态或值冲突。Phase 2 已将 DOI 冲突文档写入 evidence/异常 ledger，不会静默依赖扫描首条记录。

已完成的 `longeval-gold-import` 重新核验三份原始输入 SHA-256，生成 365 条 Train、65 条 Held-out、455 条 Future DOI-strict GoldQuery，以及 8,515 条正相关 evidence 和 99 条 excluded ledger。导入 manifest 与各输出 SHA-256 位于 `data/evaluation/longeval_2025/processed/longeval-doi-gold/`。

Train Dev20 已封存为 `data/evaluation/longeval_2025/subsets/longeval-doi-train-dev20-v1.jsonl`，选择标识为 `longeval-doi-train-dev20-v1`，种子为 `scholarweave-longeval-train-dev20-v1`，Gold SHA-256 为 `c5809925dfc6b7e97a0459950e427f7283a545139be05cacb1f012acb9a91fd8`。`doi-track-score` 已实现 Macro/Micro P/R/F1、MRR、二元 nDCG、Hit、Zero-Hit 与 Prediction DOI Coverage；它只能读取本地 Gold 和预测，尚未对真实 ScholarWeave 预测评分。

Dev20 的直接 QueryIntent 审阅包已生成于 `data/evaluation/longeval_2025/plans/longeval-dev20-direct-v1/`：它直接封装 LongEval 原始 query，显式冻结 `source_recall_count=50`、`target_paper_count=20`、单轮、零网页、零本地模型。20 条独立 forecast 合计预估上限为 20 次学术 API 逻辑调用和 80 次 HTTP 尝试；没有 Query Agent/DeepSeek 调用。

用户已确认该计划的全部 20 个 `confirmation_sha256`。2026-08-22，`snapshot-export` 逐条成功封存 20 份 OpenAlex 单轮候选快照；每条均为 50 篇排序前候选，实际合计 20 次逻辑学术 API 调用，未出现来源降级、DeepSeek 或本地模型调用。随后离线组装为 `data/evaluation/longeval_2025/runs/longeval-dev20-direct-v1/longeval-dev20.snapshot-collection.jsonl`，并以对应 manifest 冻结了 20 条顺序、快照 ID 与 SHA-256。

该集合的只读候选覆盖诊断显示：158 个 DOI Gold 中有 10 个已在 1,000 个排序前候选中以强标识命中（6.33%），5/20 条查询至少命中一个 Gold，15/20 条为零命中。诊断未发生标题回退或内部标识匹配；它只说明当前候选快照的覆盖边界，不是最终 DOI Track 排名分数，也不能单独归因于来源、查询或规范化。

在同一集合上，实验 A（规则过滤后的 RRF 基线）已完成 20 条完全离线排序并生成 DOI-strict 评分。Top-20 的 Macro P/R/F1 分别为 0.0100 / 0.0030 / 0.0047，Micro P/R/F1 为 0.0115 / 0.0253 / 0.0158，MRR 为 0.0125；仅 1/20 条查询在 Top-20 命中，预测 DOI 覆盖率为 86.75%。这不是生产端到端分数：它只衡量同一份候选快照经 RRF 截断后的 DOI 命中，并确认候选覆盖和最终截断均是当前主要瓶颈。

实验 B（RRF + 本地 BGE-M3）已使用用户提供的本地模型快照在 CPU、batch size 8 下完成 20 条离线排序；20 次 BGE 阶段合计 100.23 秒，未发生 OOM 降批。Top-20 的 Macro F1 提升至 0.0223（A 为 0.0047），Micro F1 为 0.0276（A 为 0.0158），命中查询为 4/20（A 为 1/20），MRR 为 0.0255，预测 DOI 覆盖率为 87.50%。B 使 5 条候选覆盖非零查询中的 4 条在 Top-20 暴露 Gold DOI，但不能改善 15 条候选零覆盖查询；因此后续 Cross Encoder 对照只能评估排序增益，不能作为覆盖问题的修复依据。

实验 C（RRF + 本地 Cross Encoder）已使用用户提供的本地 `bge-reranker-v2-m3` 快照在 CPU、batch size 8 下完成 20 条离线排序；20 次 Cross Encoder 阶段合计 50.30 秒。Top-20 的 Macro F1 为 0.0152、Micro F1 为 0.0231、命中查询为 3/20、MRR 为 0.0254、预测 DOI 覆盖率为 90.25%。C 优于 A，但在本 Dev20 的 Macro F1@20、Micro F1@20 与命中查询数上均低于 B；该集合只有 20 条，结论仅是当前开发集观察，不构成最终模型选择。下一项 D 将在同一候选集合上组合 BGE-M3 与 Cross Encoder，比较两阶段串联是否带来额外收益。

实验 D（RRF + BGE-M3 + Cross Encoder）已在同一 20 条候选快照上完成：BGE-M3 将每条 50 篇候选保留至 40 篇，再由 Cross Encoder 截断至 Top-20。CPU、batch size 8 的阶段 trace 显示 BGE-M3 合计 52.10 秒、Cross Encoder 合计 38.01 秒。Top-20 的 Macro F1 为 0.0155、Micro F1 为 0.0233、命中查询为 3/20、MRR 为 0.0255、预测 DOI 覆盖率为 89.50%。因此当前 Dev20 的排序对照为：A=0.0047、B=0.0223、C=0.0152、D=0.0155（均为 DOI-strict Macro F1@20）；B 是开发集上的暂定最优配置，D 没有证明组合重排的额外收益。该结论必须在独立 Validation 子集复核，且不能掩盖 15/20 查询在当前候选池零覆盖的事实。

Validation100 已从 Train 的 365 条 DOI-eligible GoldQuery 中封存，并通过 Dev20 manifest 显式排除 20 条已开发查询；两个子集交集为 0。Validation Gold SHA-256 为 `7b7b3f05b0cbd6b3de28c9aaa0129f2c58e8ffa0cf53410f431d8dddeba57c1b`，排除来源 manifest 的 SHA-256 已写入 Validation manifest。直接 QueryIntent 审阅计划位于 `data/evaluation/longeval_2025/plans/longeval-validation100-direct-v1/`，其中 100 条查询均冻结 `source_recall_count=50`、`target_paper_count=20`、单轮、零网页、零模型；100 份 forecast 合计上限为 100 次逻辑学术 API 调用和 400 次 HTTP 尝试。

2026-08-23 已在用户确认全部 `confirmation_sha256` 后执行首次 Validation100 导出：94 条快照成功封存；第 92–97 条在适配器内部重试后仍为 OpenAlex 全部来源失败，未写入候选快照；第 98–100 条随后恢复成功。用户随后明确重试第 92–97 条：第 92、93、94、96、97 条成功，当前有效快照为 99/100；第 95 条仍失败。其根因已定位为 OpenAlex 返回单条 `year=1739` 的异常记录，映射到 `Paper` 时违反年份下限并中止了整个来源调用，而非临时网络错误。首次和重试审计清单均须保留；在适配器隔离该类异常记录并经测试后，用户才可再次明确授权重试第 95 条。Validation100 共享候选集合、BGE-M3 重排与 DOI-strict 评分仍不得在 99 条不完整分母上执行。

## 4. DOI Gold 构建规则

### 4.1 数据流

```text
queries.query_id
  → qrels.query_id, qrels.document_id, qrels.relevance
  → documents.id
  → documents.doi
  → normalize_doi(document.doi)
  → Gold DOI Set
```

### 4.2 强制规则

- 仅经 Phase 0 确认属于有效相关判断的 qrels 行可形成 Gold 候选。
- 只有 `normalize_doi()` 后满足 DOI 语法门槛的文档可形成 DOI Gold。
- 同一 query 下同一 DOI 只计一次；保留 document-to-DOI 多对一关系用于审计。
- 空 DOI、无 DOI、异常 DOI、无法规范化 DOI 均记录，不尝试由 title、作者、arXiv 或平台 ID 补齐。
- 某 query 没有一个有效 Gold DOI 时，写入 `excluded_no_doi_gold`；保留在审计报告中，但不进入 DOI-strict 主评分分母。
- Prediction 缺 DOI 或 DOI 非法时仍保留在原排名中，但不能命中 Gold DOI。
- DOI Track 结果必须记录 `matching_policy=doi-strict-v1`，并拒绝调用通用标题回退匹配器。

### 4.3 审计统计

每个 split 与全量均输出：

- `total_queries`
- `total_qrels`
- `total_relevant_documents`
- `relevant_documents_with_doi`
- `relevant_documents_without_doi`
- `doi_gold_coverage`
- `doi_eligible_query_count`
- `excluded_no_doi_gold_query_count`
- 每 query Gold DOI 数量分布
- DOI 重复数、异常 DOI 数
- relevance judgment 分布

必须单独展示：

```text
Train: 393 → DOI-eligible = ?
Held-out: 99 → DOI-eligible = ?
Future: 492 → DOI-eligible = ?
```

问号仅能由真实本地 audit 填充。

## 5. 指标口径

### 5.1 Competition-style 本地代理

报告以下 DOI-strict 指标：

- Precision@5 / 10 / 20
- Recall@5 / 10 / 20
- F1@5 / 10 / 20
- Macro F1 与 Micro F1

建议将 **DOI-strict Macro F1@20** 作为本地 competition-style 主代理指标：生产最终结果上限为 20，且当前比赛代理的检索质量权重为 70%。它不是官方比赛成绩，必须同时展示 Micro F1@20 和全部 P/R/F1 曲线。

当前评测 Precision@K 的分母为 K 内实际去重预测数，而不是固定 K；最终结果不足 K 时不能隐藏此口径。实现 DOI scorer 前必须冻结该定义并在报告中写明。

### 5.2 检索诊断指标

保留：

- Recall@5 / 10 / 20、Precision@5 / 10 / 20；
- MRR；
- 二元 nDCG@10、二元 nDCG@20；
- Hit@5 / 10 / 20；
- Zero-Hit Query Rate；
- DOI Gold Coverage；
- Prediction DOI Coverage。

若 Phase 0 明确 qrels 存在可解释的多级 relevance，另行增加 graded nDCG，并把原始 relevance 与聚合策略写入版本化 evidence 文件；否则只报告已验证的二元 nDCG。

## 6. Candidate 到 Final 的阶段诊断

新增不可变 `EvaluationStageTrace`，冻结同一生产运行已经产生的事实，不触发二次检索：

```text
normalized source results
→ deduplicated pre-filter papers
→ deterministic-filter survivors
→ BGE retained IDs
→ Cross Encoder retained IDs
→ DeepSeek input / accepted / rejected IDs
→ Final Top-K IDs
```

逐 query 报告 Candidate、BGE、Cross Encoder、Final Recall@K，并按以下规则归因：

| 现象 | 可报告的事实结论 |
| --- | --- |
| Gold DOI 不在已执行来源的 normalized results | 本次已执行来源未观测到，不可推断全网缺失 |
| 在 pre-filter、未在 candidate pool | 被确定性过滤移除，输出 filter reason |
| 在 candidate、未通过 BGE 或 CE | 被对应阶段的排序截断淘汰 |
| 在 DeepSeek 输入、未进 final | 区分约束拒绝、最终截断、失败后降级 |
| Final 论文有 title 但无 DOI | `prediction_missing_doi`，不算 DOI 命中 |

现有离线 A/B/C/D 已保存 BGE、Cross Encoder、DeepSeek 的数量 trace；生产完整链路需补充阶段论文集合的只读终态记录。

## 7. 效率与结构化评分

### 7.1 效率

保持 20% 本地代理框架，并始终输出原始指标：

- academic API logical calls；
- actual HTTP requests；
- retry count、429/rate-limit count；
- LLM calls、input/output/total tokens；
- mean / P95 end-to-end latency；
- cache hits；
- BGE、Cross Encoder 输入数、输出数、延迟、设备、batch size。

`SearchRunState` 已有逻辑 API 调用、累计 Token、延迟和 cache hits。HTTP、retry、429 及 Query Agent / query evolution / final verification 分阶段 usage 尚未完整贯通；在其实现前报告为 `N/A`。

### 7.2 结构化

继续复用已有确定性结构检查：

- 有序结果与重复论文；
- DOI / 强 ID、title、authors、year、venue、source、relevance、recommendation reason 完整度；
- 引用关系只指向当前结果内论文。

LongEval 仅新增 DOI completeness、invalid DOI count、duplicate DOI count，不重做结构评分系统。

## 8. 数据目录、manifest 与结果目录

```text
data/evaluation/longeval_2025/
├── raw/                       # 用户准备的 queries / qrels / documents
├── processed/
│   ├── longeval-doi-evidence.jsonl
│   ├── longeval-doi-gold.train.jsonl
│   ├── longeval-doi-gold.heldout.jsonl
│   └── longeval-doi-gold.future.jsonl
├── subsets/
│   ├── longeval-doi-dev20.jsonl
│   ├── longeval-doi-validation100.jsonl
│   └── longeval-doi-future100.jsonl
├── manifests/
└── reports/
```

真实数据、模型、结果和日志均位于已忽略的 `data/` 下，不提交 Git。

每个 manifest 至少保存：source 文件 SHA-256、processed Gold SHA-256、query_id 完整有序列表、选择规则、seed、split、DOI eligibility 规则、生成时间与 schema version。

## 9. CLI 设计

新增命令应复用既有 contracts、reports、hash 和安全输出策略：

```text
longeval-audit
longeval-gold-import
gold-subset-select
doi-track-score
stage-recall-diagnose
longeval-end-to-end-plan
longeval-end-to-end-execute
longeval-end-to-end-score
```

- `longeval-audit`：只读 raw，禁止网络、LLM、模型；输出字段确认和覆盖审计。
- `longeval-gold-import`：仅接受已完成 audit 的文件路径与 schema version；输出 GoldQuery、DOI evidence、excluded ledger。
- `doi-track-score`：只读 Gold、Prediction、可选 trace；只使用 DOI strict matcher。
- `stage-recall-diagnose`：只读 Gold DOI 与 stage trace，不加载模型或访问来源。
- `longeval-end-to-end-execute`：仅用户显式授权、先通过 `usage-forecast` 与确认哈希后运行；每条 query 仍独立归档成功、失败和超时。

现有 PaSa 命令保留。LongEval 不应复制一套评测框架，而应在通用 runner 之上增加适配器与 DOI 评分策略。

## 10. 测试设计

所有测试只使用合成 fixture，不读取 `.env`、不访问网络、不开模型：

1. queries/qrels/documents 格式确认、缺字段、编码异常、重复 query/document ID；
2. qrels 指向缺失 document、document 无 DOI、非法 DOI、重复 DOI；
3. 无 DOI Gold query 必须进入 excluded ledger，不能静默丢弃；
4. DOI strict：相同 DOI 命中；同标题不同 DOI、不带 DOI、arXiv↔DOI 均不得命中；
5. P/R/F1、Macro/Micro、MRR、nDCG、Hit 与 Zero-Hit 的边界；
6. stage trace 的各阶段集合、过滤原因、失败降级和 DOI 缺失归因；
7. manifest 的 seed、顺序、SHA-256 与不可覆盖输出保护；
8. PaSa、`dataset-gold-import`、既有 A/B/C/D fixture 的回归兼容性；
9. 所有 online CLI 在缺少显式授权、forecast 或 confirmation SHA-256 时必须在建立客户端前失败。

## 11. 分阶段计划与决策门

| 阶段 | 输入和操作 | 学术 API | LLM | 输出 | 验收标准 |
| --- | --- | --- | --- | --- | --- |
| Phase 0：数据审计（已完成） | 全部 984 条本地 queries、qrels、documents metadata | 否 | 否 | split 审计、DOI coverage、异常清单 | Gate A 已确认：885 DOI-eligible；详见第 3 节 |
| Phase 1：LongEval Adapter（已完成） | 合成 fixture、已确认 schema | 否 | 否 | adapter、错误契约、测试 | 三表 join 正确且不破坏 PaSa |
| Phase 2：DOI Gold（已完成） | 已审计 raw | 否 | 否 | evidence、Gold、excluded、manifest | DOI strict Gold 可复核、原始与输出哈希完整 |
| Phase 3：小规模离线验证（部分完成） | Dev20、合成预测或已有快照 | 否 | 否 | scorer、metrics、Dev20 manifest | DOI 评分已通过合成测试；等待真实预测后完成 Gate B |
| Phase 4：受控候选快照（Dev20 已完成） | Dev20，后续 Validation / Test 固定集合 | 是，显式授权 | 否 | 单查询快照、集合 manifest、覆盖诊断 | Dev20 20/20 成功封存；后续端到端与阶段 trace 另行受控执行 |
| Phase 5：消融和诊断 | 同一候选快照 | 否 | DeepSeek 仅显式授权 | A/B/C/D、E、阶段报告 | 不重调来源，定位 Candidate 到 Final 损失 |
| Phase 6：最终报告 | 已归档各 split 结果 | 否 | 否 | JSON、JSONL、Markdown | Dev/Validation/Held-out/Future 分开报告 |

规模职责固定：

```text
984 全量数据       → 离线 Dataset Audit
Train Dev20        → 开发与链路验证
Train Validation100→ 策略比较与消融
Held-out           → 同分布未见查询
Future             → 时间泛化
```

Dev20、Validation、Held-out、Future 不得混成同一个主分数；可以提供 Combined Test Summary，但必须保留 Held-out 与 Future 的独立结果。

## 12. 风险与迁移原则

- qrels 来自 click model，未在 qrels 中不等于论文绝对不相关；报告应称“未获得 judged DOI hit”。
- CORE 文献集合与 ScholarFlow 实时多源检索集合不同；Candidate Recall 反映本次运行来源覆盖，不是官方 CORE corpus 检索成绩。
- DOI-only 轨道提升身份确定性，但会引入 DOI coverage selection bias；必须始终同时报告 excluded 数量与 DOI Gold Coverage。
- 当前评测规范对 `snapshot-export` 与完整端到端来源调用的边界需要在实现前统一；LongEval 线上运行不得作为任何离线命令的隐式副作用。
- PaSa 不删除、不改分数；它与 LongEval DOI Track 分开报告，除非以后有经审阅的合并依据。

## 13. 预计涉及文件

| 类别 | 预计文件 |
| --- | --- |
| LongEval 输入与 DOI Gold | 新增 `evaluation/adapters/longeval.py`、`evaluation/runners/longeval_import.py`、`evaluation/contracts/longeval.py` |
| DOI strict 指标 | 新增 `evaluation/metrics/doi_track.py`，扩展 `evaluation/contracts/result.py`、`evaluation/reports/writers.py`、`evaluation/cli.py` |
| 阶段诊断 | 新增 `evaluation/contracts/stage_trace.py`、`evaluation/runners/stage_diagnostic.py`；按需扩展生产工作流与快照读取 |
| 生产观测 | `backend/app/adapters/academic_api.py`、`backend/app/models/search_run.py`、`backend/app/agents/search_workflow.py`、`backend/app/api/routes/usage.py` |
| 测试 | 新增 `evaluation/tests/test_longeval_import.py`、`test_doi_track_metrics.py`、`test_stage_diagnostic.py`，并扩展 CLI 回归测试 |

实施优先级：人工审阅并逐条授权 Dev20 候选快照 → 组装快照与离线预测 → 运行 DOI scorer/覆盖诊断 → 生产阶段 trace 与效率观测 → 受控端到端 → 消融与最终报告。

## 14. 数据下载脚本

`scripts/download_longeval_dataset.py` 是唯一的数据下载与解压辅助入口。它只在用户手动运行并显式传入 `--allow-download` 时访问官方 TU Wien 数据仓库；默认只选择 Train abstract ZIP，可用 `--split test` 或 `--split all` 明确扩大范围。它校验发布方公开 MD5、下载到同目录临时文件后原子发布，默认不下载 fulltext ZIP。已下载 archive 可通过零网络的 `--extract-only` 解压；解压器拒绝 ZIP 路径穿越和符号链接，在临时目录完成后才发布到按 split 隔离的目录。

```powershell
python scripts/download_longeval_dataset.py --allow-download --split train
python scripts/download_longeval_dataset.py --allow-download --split test
python scripts/download_longeval_dataset.py --allow-download --split test-qrels
python scripts/download_longeval_dataset.py --extract-only --split all
python -m evaluation longeval-audit
```

## 15. Validation100 快照 095 的来源异常修复（2026-08-23）

`longeval-validation100-direct-v1-095` 在首次导出与一次显式重试后仍失败。重试日志定位到 OpenAlex 的单条 Work 含 `publication_year=1739`，该值违反统一 `Paper.year` 的 `1800..2100` 契约，导致此前未被捕获的 Pydantic 校验异常中断整页映射。

已在 `OpenAlexClient.search` 与兼容的 `search_works` 中捕获单条 `ValidationError`：异常 Work 被跳过并写入不含来源字段值的告警，同一响应中的其他 Work 继续映射。离线回归测试覆盖“异常年份后紧跟合法 Work”的响应，验证合法 Work 保留原始 `raw_rank=2`；定向测试 13 项通过，且 `compileall` 通过。本修复未调用任何学术来源、模型或 LLM。

修复后仍需用户再次显式确认，方可使用原 `confirmation_sha256` 重试快照 095；成功后再以 100 个有效快照组装 Validation100 集合并运行离线候选覆盖诊断。

用户已于 2026-08-23 确认使用原确认值 `a21de934f97e4141ebd17db3ba8e8eb927330393b76648e45042814f36fe9e4d` 重试 095。导出成功：OpenAlex 原始 50 条、映射成功 49 条、异常记录跳过 1 条；逻辑学术 API=1，LLM=0，本地模型=0，快照 SHA-256 为 `208a93c650cfdf9e72e1dc29d7578c5d1f4c1cba2a69000d9fe7f6076804d548`。快照已通过离线契约校验，并归档至 `runs/longeval-validation100-direct-v1/snapshots/`。递归清点确认该目录现有预期 100 份快照、100 个唯一 snapshot ID、无缺失且无重复。

Validation100 共享候选集合已离线封存并通过 100 份快照校验，集合 SHA-256 为 `0bb598ca8488b565cb8ba4c8dbbbf37196801d325a5e108e5828c253de2fd0f0`。基于此集合的 DOI-strict 候选覆盖诊断结果：100 条查询、549 篇 Gold、4,899 篇排序前候选、24 篇 Gold 命中，84 条查询零命中。该诊断未调用学术 API、LLM 或本地模型；零命中只能说明当前候选快照没有覆盖相应 judged DOI，不能单独归因于来源、查询、规范化或排序。

Validation100 的 A/B/C/D 离线消融计划已生成：矩阵 `local-ranking-abcd` 冻结 100 个快照、400 个任务，`source_recall_count=50`，语义阶段保留 40，Cross Encoder 阶段保留 20，最终候选数 20，评测 Top-K 为 5/10/20。该计划不调用学术 API 或 DeepSeek；下一步可先执行不加载模型的 A（RRF）基线，再在用户显式授权本地模型后执行 B/C/D。

Validation100 的 A（RRF）已离线执行并评分，100 条预测、无本地模型阶段、评分过程新增学术 API=0/LLM=0/本地模型=0。DOI-strict 结果：Macro F1@5=0、@10=0.0056、@20=0.0081；Micro F1@20=0.0087；Mean MRR=0.0077；Mean nDCG@20=0.0095。评分报告内的“逻辑学术 API=100”继承自历史快照导出观测，非本次评分调用。该基线与候选覆盖诊断一致：候选未覆盖的 judged DOI 不能由重排恢复。

Validation100 的 B（RRF + 本地 BGE-M3）已在 CPU、batch size 8 下执行并评分，结果与 A 共享同一 100 条快照；新增学术 API=0、DeepSeek=0。B 的 DOI-strict 结果：Macro F1@5=0.0094、@10=0.0075、@20=0.0078；Micro F1@20=0.0087；Mean MRR=0.0188；Mean nDCG@20=0.0116。相对 A，BGE-M3 将已有命中显著提前（MRR、nDCG@20 与 F1@5 上升），但没有改变 Top-20 内的总命中数，且 Macro F1@20 略低；这是同一候选集合上纯重排的预期限制。

Validation100 的 C（RRF + 本地 Cross Encoder）已在 CPU、batch size 8 下执行并评分，新增学术 API=0、DeepSeek=0。首次评分在 Windows 原子发布报告目录时出现瞬时 `PermissionError`，未留下目标或临时产物；同一已核验结果的评分重试成功，未重新加载模型。C 的 DOI-strict 结果：Macro F1@5=0.0104、@10=0.0115、@20=0.0103；Micro F1@20=0.0119；Mean MRR=0.0241；Mean nDCG@20=0.0160。C 当前在 A/B/C 中所有列示检索指标最佳，且相对 A/B 提高了 Top-20 命中数；仍受 84 条候选零命中的覆盖上限约束。

Validation100 的 D（本地 BGE-M3 + Cross Encoder）已在 CPU、batch size 8 下完成两阶段执行并评分，新增学术 API=0、DeepSeek=0。D 的 DOI-strict 结果：Macro F1@5=0.0104、@10=0.0115、@20=0.0111；Micro F1@20=0.0127；Mean MRR=0.0248；Mean nDCG@20=0.0169。D 为当前 A/B/C/D 的指标最佳配置，但相对 C 的提升较小：F1@5 和 F1@10 持平，F1@20 提升 0.0008、MRR 提升约 0.0007、nDCG@20 提升约 0.0009；最终默认策略应在该增益与额外 BGE-M3 成本之间权衡。所有四组共享同一 Validation100 候选快照，均未调用 DeepSeek。

Validation100 的 A/B/C/D 对比与策略决策已单独归档于 `docs/ScholarFlow_LongEval_Validation100_ABCD_评测对比与策略决策报告.md`。其中将 D 推荐为指标优先配置、C 推荐为常规成本/时延优先配置、A 保留为无模型降级基线；B 被 C 在列示检索指标和本轮耗时观测上同时超过。独立 Held-out/Future 评测前，项目方须在 C 与 D 中明确固定一个主策略；如需新候选快照，仍需生成 forecast 并获得用户对确认哈希的显式授权。

项目方随后确认继续下一阶段，本次独立评测按 D（指标优先）固定主策略。Heldout65 已完整封存（65/65，Gold SHA-256 `fa412ccae9fdd6058c380fec2415cc2c2693cf6dbb0ac74f8ec138aa4630e7a1`），并生成 `longeval-heldout65-direct-v1` 的 65 份直接 QueryIntent、65 份逐条 `snapshot-export` forecast 和 65 个 confirmation SHA-256。该准备步骤新增学术 API=0、DeepSeek=0、本地模型=0；待审阅在线预算为逻辑学术 API 最多 65 次、实际 HTTP 最多 260 次。只有用户以 manifest 中全部 confirmation SHA-256 显式确认后，才可逐条导出 Heldout65 候选快照。

用户已确认使用 Heldout65 manifest 中全部 confirmation SHA-256。`snapshot-export` 按冻结顺序完成 65/65：65 个唯一 snapshot ID、65 个唯一 query ID、排序前候选共 3,204 篇，且无学术来源降级告警。实际逻辑学术 API=65，DeepSeek=0，本地模型=0；每条输出后已立即通过离线 `snapshot-check`，随后完成集合级只读计数复核。下一步仅可离线组装共享候选集合，并先运行 DOI-strict 候选覆盖诊断；在候选快照冻结前不得加载 BGE-M3 或 Cross Encoder。

Heldout65 共享候选集合已按 manifest 顺序离线封存，集合 SHA-256 为 `49a6b2c9e2c59c77c89bd423eb9df094337f10675c219275216e041cea2b37c0`。随后的 DOI-strict 覆盖诊断读取 65 条查询、206 篇 Gold DOI 与 3,204 篇排序前候选：仅 3 篇 Gold DOI 命中候选，62 条查询零命中。诊断新增学术 API=0、DeepSeek=0、本地模型=0；零命中仅表示当前冻结候选没有覆盖相应 judged DOI，不可据此单独归因于来源、QueryIntent、规范化或排序。候选快照现已冻结，下一步可生成 D 的离线消融任务计划；不得先运行本地模型。

Heldout65 的 D-only 离线消融矩阵与任务计划已生成。矩阵 SHA-256 为 `cac1fc01c36d17a58e6f5c44f262411e454ec39eaee37ce131b4becc0ad6e280`，仅包含 D（RRF + BGE-M3 + Cross Encoder），冻结 `source_recall_count=50`、语义保留 40、Cross Encoder 保留 20、最终输出 20、评测 K=5/10/20。计划绑定全部 65 个快照及其哈希，共 65 个任务；计划生成新增学术 API=0、DeepSeek=0、本地模型=0。下一步如需执行，用户必须再次显式授权本地模型，并提供已确认的 BGE-M3 与 Cross Encoder 本地目录。

用户已授权执行 Heldout65 的 D 组本地排序。CPU、batch size 8 下的 BGE-M3 与 Cross Encoder 已完成 65/65 个任务并原子归档，结果 SHA-256 为 `40886bd1ac41ff659c4cd038db9137975ed97226ac1529ff7faa5d0eb00cbdb1`。执行新增学术 API=0、DeepSeek=0；结果与计划的 65 条任务一一对应。下一步仅需对该已归档预测运行 DOI-strict 离线评分，评分不得重新加载模型或调用来源。

Heldout65 的 D 组 DOI-strict 离线评分已完成（评分 manifest 新增学术 API=0、DeepSeek=0、本地模型=0）：Macro F1@5=0、@10=0、@20=0.0012；Micro F1@20=0.0013；Mean MRR=0.0013；Mean nDCG@20=0.0014。报告中的“学术 API 逻辑调用数=65”是候选快照导出的历史观测，不是本次评分调用。该低分与此前 3/206 Gold DOI 候选覆盖、62/65 查询零命中的诊断一致；它不构成对排序模型、来源或查询策略任一单独环节的因果归因。下一步应离线生成 Validation100 与 Heldout65 的独立对比/决策报告，确认是否在不改善候选覆盖的前提下推进 Future。

Validation100 与 Heldout65 的跨 split 对比及 Future 决策已归档于 `docs/ScholarFlow_LongEval_Validation100_Heldout65_对比与Future决策.md`。结论为：保持 D 作为冻结的排序配置，但不立即执行全量 455 条 Future 快照导出；先以 Future20 完成独立的候选覆盖试点。Future20 的查询、QueryIntent 与 forecast 准备可以完全离线执行；任何候选快照导出仍须另行审阅 forecast 并以对应 confirmation SHA-256 显式确认。

Future20 试点已从 455 条 Future DOI Gold 中离线封存（Gold SHA-256 `8d7b02bf84911d7acc542ecac0c9ff4792cb257c4ba4ff4cb139bcec13a5010d`），并生成 `longeval-future20-direct-v1` 的 20 份直接 QueryIntent、20 份逐条 `snapshot-export` forecast 和 20 个 confirmation SHA-256。该步骤新增学术 API=0、DeepSeek=0、本地模型=0；待审阅在线预算为逻辑学术 API 最多 20 次、实际 HTTP 最多 80 次。只有用户显式确认使用 manifest 中全部 confirmation SHA-256，才可逐条导出 Future20 候选快照。
