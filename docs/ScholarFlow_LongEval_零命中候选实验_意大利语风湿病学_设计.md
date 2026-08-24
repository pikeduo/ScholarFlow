# ScholarFlow LongEval：零命中候选实验设计——意大利语风湿病学

生成日期：2026-08-24  
状态：设计已冻结，**等待项目方确认规范化检索式；尚未生成 QueryIntent、forecast 或快照**

## 1. 实验目标

对 `query_id=1f841355-4fee-4949-9993-8988f12743cc` 的候选不足问题做一次单查询、单变量实验，检验来源实际使用的规范化检索式是否会影响候选可得性与 DOI-strict 覆盖。

当前基线快照为 `longeval-validation100-direct-v1-075`：原始查询 `settore med16 - reumatologia`，Gold DOI=4，排序前候选=0，命中 Gold DOI=0。它只可作为对照，不得覆盖或替换。

## 2. 变量与冻结条件

| 项目 | 对照（已封存） | 实验（待项目方确认） |
| --- | --- | --- |
| `original_query` | `settore med16 - reumatologia` | 保持不变 |
| 来源实际检索式 | 同原始查询 | 一个经项目方确认的规范化检索式 |
| `research_topics` 等结构化词 | 全空 | 保持全空 |
| `source_recall_count` | 50 | 50 |
| `target_paper_count` | 20 | 20 |
| 检索轮次 / 模式 | 第 1 轮 / `standard` | 第 1 轮 / `standard` |
| 语义、交叉重排、DeepSeek、网页证据 | 全部关闭 | 全部关闭 |
| 来源与排序 | `snapshot-export` 的单次候选生成；不排序 | 保持相同 |

本实验的唯一变量是 `normalized_query`。不填 `research_topics`、`methods` 或 `subqueries`，以避免同时改变 OpenAlex 的字段组合、触发多轮搜索或混入 Query Agent 行为。

## 3. 需要确认的语义决定

原始字符串含有意大利语术语 `reumatologia` 和 `MED16` 代码。当前 `QueryIntent.query_language` 契约仅允许 `zh`、`en`、`mixed`，字符范围推断会把该字符串标记为 `en`；更改这个标签既不能表示意大利语，也不会改变 OpenAlex 适配器使用的检索文本。因此它不是有效实验变量。

建议的待确认检索式为：`rheumatology`。它将只写入 `normalized_query`，原始查询仍原样保存，其他字段不变。此建议属于一次人工语义规范化，不从 LongEval Gold 补全论文、作者、年份或主题；在项目方明确确认前不得写入文件或执行。

项目方也可以明确拒绝该建议，改为给出另一个单一规范化检索式。不得同时增加翻译词、主题字段、子查询、第二来源或召回数量。

## 4. 成功判据与解释边界

| 检查 | 通过条件 | 解释边界 |
| --- | --- | --- |
| 快照契约 | 新快照通过 `snapshot-check` | 只说明产物可用于后续离线比较 |
| 候选可得性 | `ranking_candidate_count > 0` | 不等于 DOI Gold 覆盖提升 |
| DOI 覆盖 | `matched_gold_paper_count > 0` | 只说明当前 Gold DOI 至少一个进入新候选 |
| 对照比较 | 与旧快照分别运行 `coverage-diagnose --query-id` | 不合并至 Validation100 主集合或 A/B/C/D 报告 |

即使候选数从 0 增加到 50，若 DOI 命中仍为 0，实验结论也只能是“改善候选可得性而未观察到 DOI-strict 覆盖改善”。不得将单条结果推广到非英语查询、来源能力或整个 split。

## 5. 获准后的执行顺序

1. 项目方明确确认规范化检索式（建议值 `rheumatology`）和实验 ID `longeval-coverage-it-rheumatology-v1`。
2. 离线写入一个新的 QueryIntent 文件；先用 `QueryIntent` 契约校验，再以 `usage-forecast --operation snapshot-export` 生成单条 forecast。此时新增学术 API=0、DeepSeek=0、本地模型=0。
3. 项目方审阅该 forecast 的 1 次逻辑学术 API、最多 4 次 HTTP 尝试及 `confirmation_sha256`，再明确确认该哈希。
4. 仅在确认后，以 `snapshot-export --allow-online-sources` 写入新的实验专用目录；不可覆盖基线快照。
5. 立即运行 `snapshot-check` 与只读 `coverage-diagnose --query-id 1f841355-4fee-4949-9993-8988f12743cc`，把新旧覆盖结果并列报告。没有 DOI 命中时不得加载 BGE-M3 或 Cross Encoder。

## 6. 证据与实现依据

- 基线诊断：`data/evaluation/longeval_2025/reports/longeval-validation100-candidate-coverage-v1/query_diagnostics.jsonl`
- 基线快照：`data/evaluation/longeval_2025/runs/longeval-validation100-direct-v1/snapshots/`
- QueryIntent 契约：`backend/app/models/query_intent.py`
- 直接 QueryIntent 生成与语言推断：`evaluation/runners/longeval_query_intents.py`
- OpenAlex 检索文本拼接：`backend/app/adapters/openalex.py`

本文件不包含新的真实数据或 API 调用记录；真实候选快照仍位于 Git 忽略的 `data/` 目录。
