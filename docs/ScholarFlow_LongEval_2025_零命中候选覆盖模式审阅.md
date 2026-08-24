# ScholarFlow LongEval 2025：零命中候选覆盖模式审阅

生成日期：2026-08-24  
性质：只读诊断审阅，不产生新预测或候选快照

## 1. 范围与结论

本审阅读取四个已封存 split 的 `query_diagnostics.jsonl`、对应 DOI Gold 子集和快照元数据，共 285 条查询。未调用学术 API、DeepSeek 或本地模型，未修改候选与排序。

当前零命中的主要可观测模式是：候选通常已经达到 49–50 篇，且 Gold 与候选均具有强标识符，但二者没有 DOI 身份交集。由此可确定后续实验应优先检验**候选生成与 QueryIntent 表达**，而不是以 BGE-M3 或 Cross Encoder 重排作为补救。该观察不能单独说明是哪一个来源、查询改写或数据集因素导致了覆盖失败。

## 2. 汇总事实

| Split | 查询数 | 零命中查询 | 有命中查询 | 单源 OpenAlex | 来源告警 | 零命中平均候选数 | 有命中平均候选数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Validation100 | 100 | 84 | 16 | 100 | 0 | 48.83 | 49.81 |
| Heldout65 | 65 | 62 | 3 | 65 | 0 | 49.26 | 50.00 |
| Future20 | 20 | 15 | 5 | 20 | 0 | 49.87 | 50.00 |
| Future100 | 100 | 84 | 16 | 100 | 0 | 49.48 | 49.94 |

全部快照的 `stop_reason` 均为 `candidate_snapshot_ready`。每个快照的 `query_intent` 都是直接透传：`research_topics`、`methods`、`tasks` 与 `subqueries` 均为空，`complexity_score=0`，只使用原始查询字符串。零命中条目的诊断标志均为 `no_gold_candidate_identity_match`；没有出现强标识符、内部标识符或标题回退的匹配。故这不是已发现的身份映射回退故障。

## 3. 代表性零命中切片

下表只列出后续可审阅的代表性查询，不构成对其失败原因的定论。

| 类别 | Split | query_id | 原始查询 | Gold DOI | 排序前候选 | 已匹配 Gold DOI |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 宽泛单词 | Validation100 | `fb1aaac4-226c-4b5d-b579-c6cd134afd75` | `physics` | 34 | 50 | 0 |
| 宽泛单词 | Future100 | `1994a776-8428-4527-8a03-25d67d34913c` | `figure` | 7 | 50 | 0 |
| 缩写/术语 | Validation100 | `9c78514c-02d8-4dd6-8b1d-587fd8205ea0` | `k -pump` | 8 | 50 | 0 |
| 缩写/术语 | Heldout65 | `1040818f-8046-4ee7-8f53-5bf104d06d45` | `rfid` | 8 | 50 | 0 |
| 缩写/术语 | Future20 | `8e2836d5-dd31-450e-8f27-a237368d8d69` | `duplex pcr` | 6 | 50 | 0 |
| 多词主题 | Future100 | `f6017e70-4b4b-4401-92ef-0c3281f4052d` | `regional planning water supply` | 11 | 50 | 0 |
| 非英语、候选不足 | Validation100 | `1f841355-4fee-4949-9993-8988f12743cc` | `settore med16 - reumatologia` | 4 | 0 | 0 |
| 非英语、候选不足 | Heldout65 | `95248f57-c9ce-4553-b2aa-5166af0a3a07` | 西班牙语长查询（系统工程师与社区发展） | 1 | 6 | 0 |

另有 `12947e59-8b2c-448e-a267-5d5b7b3015a2`（`j12 - marriage marital dissolution family structure domestic abuse`）同时出现在 Validation100 与 Future100，候选数分别为 7、8，均无 DOI 命中。这与四分割汇总发现的跨 split `query_id` 重叠一致；该条只能作为同一查询在两次冻结候选上的诊断线索，不能计为两条独立泛化证据。

## 4. 排除项与边界

- 285 个快照均为 OpenAlex 单源，没有来源降级或停止异常；单源事实可作为实验变量，但不能据此断言其他来源一定能提高 Gold DOI 覆盖。
- Validation100 有 2 条、Heldout65 有 1 条、Future100 有 1 条快照的候选数低于 49；Future20 没有此情况。除这些少数条目外，扩大排序窗口本身不会让未进入 49–50 条候选的 Gold DOI 出现。
- `ranking_candidates_empty` 仅出现 1 次（上述意大利语查询）；它应独立于“候选充足但 DOI 零交集”的大多数模式处理。
- DOI Gold 不含标题、作者和年份等额外字段；本审阅不得把外部检索、补全或人工猜测写回 Gold。

## 5. 后续候选生成实验的受控建议

下一轮仅应提出**新快照**的单 query_id 实验，一次检验一个可观察变量，并与原冻结快照隔离。推荐顺序如下：

1. 先处理候选不足的 `1f841355-4fee-4949-9993-8988f12743cc`：审阅语言识别与查询规范化，保持查询语义不变，验证是否能从 0 篇恢复到可排序候选。
2. 再处理术语表达的 `9c78514c-02d8-4dd6-8b1d-587fd8205ea0` 或 `8e2836d5-dd31-450e-8f27-a237368d8d69`：比较原词与可审阅的术语规范化/子查询策略，不同时改变来源和排序配置。
3. 最后处理 `fb1aaac4-226c-4b5d-b579-c6cd134afd75` 等宽泛词：它们 Gold 数量高而候选零交集，适合检验是否存在可复核的澄清或主题扩展信息；若无新增约束，不应凭空生成狭义意图。

任何实验执行前必须：离线写入新的 `QueryIntent` 与实验标识；运行 `usage-forecast`；由用户审阅单条 forecast 并显式确认对应 `confirmation_sha256`；再通过 `snapshot-export --allow-online-sources` 生成新快照。不得调用 Query Agent、DeepSeek、覆盖分析或多轮搜索作为该入口的隐式副作用，也不得混入既有 A/B/C/D 的候选集合。

## 6. 证据产物

- `data/evaluation/longeval_2025/reports/*-candidate-coverage-v1/query_diagnostics.jsonl`
- `data/evaluation/longeval_2025/runs/*-direct-v1/snapshots/*.snapshot.jsonl`
- `data/evaluation/longeval_2025/subsets/longeval-doi-*.jsonl`

真实数据位于 Git 忽略的 `data/` 目录；本文只记录汇总计数、代表性 query_id 与操作边界。
