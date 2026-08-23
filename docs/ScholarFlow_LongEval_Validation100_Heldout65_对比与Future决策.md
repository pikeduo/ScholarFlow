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
