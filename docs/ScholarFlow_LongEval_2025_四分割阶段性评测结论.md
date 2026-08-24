# ScholarFlow LongEval 2025：四分割阶段性评测结论

生成日期：2026-08-24  
评测轨道：LongEval 2025 Sci-Retrieval，DOI-strict-v1  
冻结排序策略：D（RRF + BGE-M3 + Cross Encoder，CPU，batch size 8）

## 1. 阶段结论

当前直接检索基线的四个已封存子集均已完成候选覆盖诊断、D 组离线排序和 DOI-strict 评分。D 应继续作为本轮**冻结的指标优先排序基线**，但证据表明总体瓶颈在候选覆盖而非后续重排：各 split 的 Gold DOI 候选覆盖仅为 1.46% 至 10.96%，零命中查询占比为 75.00% 至 95.38%。

因此，**不授权 Future455 全量快照导出，也不把四个 split 合并成单一主分数。** 后续若要提高可观测的 DOI-strict 结果，应先在已诊断的零命中查询中，以明确 `query_id` 设计候选生成策略实验，并生成新的、独立的候选快照；不得将新旧候选混用，也不得以重排替代候选覆盖改进。

## 2. 冻结输入与覆盖诊断

| Split | 查询数 | Gold DOI | 排序前候选 | Gold DOI 命中候选 | 覆盖率 | 零命中查询 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Validation100 | 100 | 549 | 4,899 | 24 | 4.37% | 84 / 100（84.00%） |
| Heldout65 | 65 | 206 | 3,204 | 3 | 1.46% | 62 / 65（95.38%） |
| Future20 | 20 | 73 | 998 | 8 | 10.96% | 15 / 20（75.00%） |
| Future100 | 100 | 366 | 4,955 | 20 | 5.46% | 84 / 100（84.00%） |

覆盖诊断均仅读取已封存候选与 DOI Gold，不重新排序、不加载模型、不调用 DeepSeek 或学术 API。它只能说明 judged DOI 是否已进入当前候选，不能将零命中单独归因于来源、QueryIntent、身份映射或排序。

## 3. D 组 DOI-strict 结果

| Split | Macro F1@5 | Macro F1@10 | Macro F1@20 | Micro F1@20 | Mean MRR | Mean nDCG@20 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Validation100 | 0.0104 | 0.0115 | 0.0111 | 0.0127 | 0.0248 | 0.0169 |
| Heldout65 | 0.0000 | 0.0000 | 0.0012 | 0.0013 | 0.0013 | 0.0014 |
| Future20 | 0.0468 | 0.0432 | 0.0251 | 0.0254 | 0.0733 | 0.0553 |
| Future100 | 0.0096 | 0.0139 | 0.0121 | 0.0127 | 0.0337 | 0.0242 |

这些都是 DOI-strict 本地指标，且只适用于各自冻结的候选集合。各评测报告中的“学术 API 逻辑调用数”是此前在线快照导出的历史观测；每次离线评分的 score manifest 均记录新增学术 API=0、DeepSeek=0、本地模型=0。报告中的效率、结构和综合分是本地代理分，不能作为赛题官方成绩。

## 4. 可比性与样本边界

所有 D 组使用同一矩阵：`source_recall_count=50`、BGE-M3 保留 40、Cross Encoder 保留 20、最终输出 20，评分 K 为 5/10/20。因此同一 split 内的覆盖与排序结果可复核；跨 split 数字只用于风险判断，不用于汇总为总分或宣称严格的总体泛化排名。

本次对已封存子集的只读 `query_id` 交集核验还发现：Validation100/Heldout65=0、Validation100/Future20=0、Heldout65/Future20=2、Validation100/Future100=9、Heldout65/Future100=10、Future20/Future100=0。Future100 在选择时已显式排除 Future20，但并未排除另外两个 split 的 query_id。因此，Future20 与 Future100 可以作为互斥的 Future 样本观察；Future100 不应被表述为对 Validation100 或 Heldout65 完全独立的查询样本。

## 5. 决策

1. 保持 D 为当前固定的指标优先离线基线；不在已封存的四个集合中重新选择 A/B/C/D。
2. 保留 Heldout65 的低覆盖、低分结果作为泛化风险证据；Future20 的较高分不能抵消该风险。
3. Future100 将 Future20 的正向试点扩展到了更大样本，但覆盖率下降至 5.46%，且 84% 查询无可重排 judged DOI；不足以授权 Future455 全量导出。
4. 下一个可执行闭环应是只读审阅各 split 的 `query_diagnostics.jsonl`，明确少量代表性零命中 `query_id` 的覆盖失败模式，并据此提交新的候选生成实验设计。任何真实快照重建仍必须先生成 forecast，并获得用户对对应 confirmation SHA-256 的显式确认。

## 6. 证据产物

- `data/evaluation/longeval_2025/reports/longeval-validation100-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/reports/longeval-heldout65-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/reports/longeval-future20-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/reports/longeval-future100-candidate-coverage-v1/diagnostic.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-heldout65-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-future20-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-future100-baseline-d/D/report.md`

真实数据与运行产物位于 Git 忽略的 `data/` 目录；本文件只保存可复核的汇总、边界和决策。
