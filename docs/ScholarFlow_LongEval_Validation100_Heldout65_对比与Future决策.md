# ScholarFlow LongEval：Validation100 与 Heldout65 对比及 Future 决策

生成日期：2026-08-23  
评测轨道：LongEval 2025 Sci-Retrieval，DOI-strict-v1  
固定排序策略：D（RRF + BGE-M3 + Cross Encoder）

## 结论

**不应在当前直接检索配置下立即执行全量 Future（455 条）快照导出。**

Heldout65 的独立结果显示，当前冻结候选的 DOI 覆盖显著低于 Validation100，D 的重排能力无法恢复未进入候选集的 judged DOI。下一阶段应先完成小规模、可复核的 Future20 基线准备与覆盖门检查；只有该门通过或项目方明确接受“当前直接检索基线”定位，才扩展到 Future 全量。

这不是对 BGE-M3、Cross Encoder、来源或 QueryIntent 任一单独组件的因果否定。两个 split 的候选均由不同真实查询产生，覆盖差异只能证明候选集合与 Gold DOI 的交集不同。

## 1. 冻结输入与可比性

| 项目 | Validation100 | Heldout65 |
| --- | ---: | ---: |
| DOI Gold 查询数 | 100 | 65 |
| DOI Gold 论文数 | 549 | 206 |
| 排序前候选数 | 4,899 | 3,204 |
| 检索配置 | D，CPU，batch size 8 | D，CPU，batch size 8 |
| 候选构造 | 单轮、`source_recall_count=50` | 单轮、`source_recall_count=50` |
| 最终输出 / 评分 K | 20 / 5,10,20 | 20 / 5,10,20 |
| 本轮排序新增外部调用 | 学术 API=0、DeepSeek=0 | 学术 API=0、DeepSeek=0 |

两个 split 各自在导出后冻结候选、再执行离线 D 排序和 DOI-strict 评分。它们不是同一查询集合，因而跨 split 数值用于泛化风险判断，不用于宣称训练集上的相对增益可直接复现。

## 2. 候选覆盖对比

| 指标 | Validation100 | Heldout65 |
| --- | ---: | ---: |
| Gold DOI 命中候选 | 24 / 549（4.37%） | 3 / 206（1.46%） |
| Gold DOI 零命中查询 | 84 / 100（84.00%） | 62 / 65（95.38%） |

Heldout65 只有 3 条查询包含至少一个 judged DOI 候选；因此 62 条查询不论采用 A/B/C/D 的任何后续重排均不可能取得 DOI-strict hit。覆盖率约为 Validation100 的三分之一，零命中查询占比增加 11.38 个百分点。

## 3. D 组 DOI-strict 结果对比

| 指标 | Validation100 | Heldout65 | Heldout 相对变化 |
| --- | ---: | ---: | ---: |
| Macro F1@5 | 0.0104 | 0.0000 | -100.0% |
| Macro F1@10 | 0.0115 | 0.0000 | -100.0% |
| Macro F1@20 | 0.0111 | 0.0012 | -89.2% |
| Micro F1@20 | 0.0127 | 0.0013 | -89.8% |
| Mean MRR | 0.0248 | 0.0013 | -94.8% |
| Mean nDCG@20 | 0.0169 | 0.0014 | -91.7% |

Heldout65 的 Top-20 仍产生极少量命中，但候选覆盖限制使排序差异无法获得充分观测。报告中的历史“学术 API 逻辑调用数”来自对应的快照导出；两次评分本身均为零 API、零模型调用。

## 4. Future 决策门

Future DOI Gold 有 455 条查询，规模约为 Heldout65 的 7 倍。若不改变当前一条查询一次单轮快照的协议，全量 Future 导出最多会带来 455 次逻辑学术 API 调用与 1,820 次 HTTP 尝试上限；这在 Heldout 覆盖未改善的情况下不具备足够的策略验证价值。

| 决策门 | 当前状态 | 要求 |
| --- | --- | --- |
| D 排序策略冻结 | 通过 | Future 继续使用 D，不在 Future 中重新挑选 A/B/C/D。 |
| Heldout 独立复核 | 完成但低分 | 如实保留为泛化风险证据。 |
| 候选覆盖 | 未通过 | 先定位候选零命中的可复核分布，不能以重排替代候选覆盖。 |
| 全量 Future 导出 | 暂缓 | 除非项目方明确接受其仅作为当前直接检索基线。 |

## 5. 建议的下一阶段

1. 离线封存 Future20，生成直接 QueryIntent 与逐条 forecast；此步不调用来源或模型。
2. 审阅 Future20 的 20 个确认哈希后，单独导出候选快照、组装集合并先运行 DOI-strict 覆盖诊断。
3. 只有 Future20 的覆盖诊断达到项目方预先接受的门槛，才授权 D 的本地排序和评分；否则先处理明确 query_id 的候选策略实验，且必须新建快照集合，不得混入旧集合。
4. Future20 的结果只作为时间泛化试点；全量 455 条仍需独立 forecast 与显式确认。

## 6. 证据产物

- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-heldout65-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/reports/longeval-heldout65-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/processed/longeval-doi-gold/manifest.json`

真实评测数据位于 Git 忽略的 `data/` 目录；本文仅保存可复核的汇总结果与决策依据。

## 7. Future20 覆盖试点状态（2026-08-23）

Future20 已按上述流程完成候选快照导出、共享集合封存和 DOI-strict 覆盖诊断。集合 SHA-256 为 `336d4019e3b11515eb1e90c625f377ff6360c41775c9d87f58d24fe6fea1f4c7`；20 条查询有 73 篇 Gold DOI、998 篇排序前候选，其中 8 篇 Gold DOI 命中候选（10.96%），15 条查询零命中（75.00%）。

该覆盖率高于 Validation100（4.37%）和 Heldout65（1.46%），因此足以执行一次受限的 D 排序试点以获得时间泛化观测；但它不是此前未定义的“覆盖门阈值”自动通过，也不能支持直接扩展到全量 Future455。D 试点完成后仍须先审阅 DOI-strict 报告，再决定是否进行任何更大规模的来源调用。

### Future20 D 组评分与后续决策

Future20 的 D 组已完成 CPU、batch size 8 排序及 DOI-strict 离线评分：Macro F1@5=0.0468、@10=0.0432、@20=0.0251；Micro F1@20=0.0254；Mean MRR=0.0733；Mean nDCG@20=0.0553。评分 manifest 记录评分阶段新增学术 API=0、DeepSeek=0、本地模型=0；报告中的逻辑学术 API=20 是候选快照导出的历史观测。

这说明本试点的候选覆盖能够让冻结的 D 策略产生可观测的 DOI-strict 命中，故**允许进入 Future100 的离线准备阶段**。但 Future20 只有 20 条确定性子集查询，不足以支持 Future455 全量导出或对时间泛化做总体结论。Future100 仍应先封存 Gold 子集、生成逐条 forecast 并获得新的显式 confirmation；先完成其覆盖诊断，再决定是否运行本地排序。

Future100 已从 Future20 之外的 435 条 DOI Gold 中稳定封存 100 条：与 Future20 的 query_id 交集为 0，Gold SHA-256 为 `01020ea362e42ff264f255b7d255740de996e606307e335ca05ac945ab546731`。其 100 份直接 QueryIntent、100 份 `snapshot-export` forecast 及 100 个 confirmation SHA-256 均已离线生成；任何导出仍须由项目方对该新 manifest 单独确认。

### Future100 覆盖状态（2026-08-24）

Future100 已完成快照导出、共享集合封存与 DOI-strict 覆盖诊断。集合 SHA-256 为 `3a73c25a5ce6969359363a90f68e4bd762db6ad3dddbe964e8bafd1c0c9ea22a`；100 条查询有 366 篇 Gold DOI、4,955 篇排序前候选，其中 20 篇 Gold DOI 命中候选（5.46%），84 条查询零命中（84.00%）。

覆盖略高于 Validation100（4.37%），且 16 条查询含有至少一个 judged DOI 候选，足以进行受限的 D 排序试点并观察较大样本的时间泛化；但绝大多数查询仍无可重排命中，Future455 全量导出继续暂缓。Future100 的 D 报告须与 Validation100、Heldout65、Future20 分开呈现，不能合并为单一主分数。
