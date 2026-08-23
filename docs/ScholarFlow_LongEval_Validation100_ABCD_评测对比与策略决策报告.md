# ScholarFlow LongEval 2025 Sci-Retrieval：Validation100 A/B/C/D 对比与策略决策

生成日期：2026-08-23  
评测范围：LongEval 2025 Train / Validation100，DOI-strict 轨道

## 1. 结论

- **指标优先配置：D（BGE-M3 + Cross Encoder）**。它在本轮所有列示的 DOI-strict 指标中最佳。
- **常规成本/时延优先配置：C（Cross Encoder）**。它与 D 在 F1@5、F1@10 持平，F1@20、MRR、nDCG@20 的差距很小，而本轮观测平均耗时更低。
- **B 不作为默认策略**。它虽优于 A 的早期排序，但被 C 在列示检索指标和本轮耗时观测上同时超过。
- **A 保留为无本地模型的降级基线**，不应作为有本地模型可用时的常规默认。

这是一份开发集的策略决策报告，不是官方 LongEval 成绩；最终选择须在独立 Held-out/Future 集合上复核，且不得把 Validation100 的结论当作泛化证明。

## 2. 可复核范围与共同控制变量

| 项目 | 冻结值 |
| --- | --- |
| Gold | Train Validation100，100 条查询、549 篇 DOI Gold |
| Gold SHA-256 | `7b7b3f05b0cbd6b3de28c9aaa0129f2c58e8ffa0cf53410f431d8dddeba57c1b` |
| 共享候选快照集合 | 100 份有效快照、4,899 篇排序前候选 |
| 候选集合 SHA-256 | `0bb598ca8488b565cb8ba4c8dbbbf37196801d325a5e108e5828c253de2fd0f0` |
| 共同排序前处理 | 规范化、去重、RRF、规则过滤后的同一候选快照 |
| 共同阶段阈值 | source recall 50；语义保留 40；Cross Encoder 保留 20；最终输出 20 |
| 评测 K | 5 / 10 / 20 |
| 本地执行 | CPU、batch size 8；BGE-M3 和/或 Cross Encoder 按配置加载 |
| 本轮新增外部调用 | 学术 API=0、DeepSeek=0；A/B/C/D 仅离线读取既有快照 |

因此，四组差异只能归因于本地重排配置；不涉及来源、查询规划或候选快照差异。

## 3. 配置定义

| 组别 | 排序配置 | 本地模型阶段 |
| --- | --- | --- |
| A | RRF 基线 | 无 |
| B | RRF + BGE-M3 | BGE-M3 |
| C | RRF + Cross Encoder | Cross Encoder |
| D | RRF + BGE-M3 + Cross Encoder | BGE-M3、Cross Encoder |

## 4. DOI-strict 指标对比

| 组别 | Macro F1@5 | Macro F1@10 | Macro F1@20 | Micro F1@20 | Mean MRR | Mean nDCG@20 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 0.0000 | 0.0056 | 0.0081 | 0.0087 | 0.0077 | 0.0095 |
| B | 0.0094 | 0.0075 | 0.0078 | 0.0087 | 0.0188 | 0.0116 |
| C | 0.0104 | 0.0115 | 0.0103 | 0.0119 | 0.0241 | 0.0160 |
| D | **0.0104** | **0.0115** | **0.0111** | **0.0127** | **0.0248** | **0.0169** |

D 相对 C 的增量为：Macro F1@5 = 0、Macro F1@10 = 0、Macro F1@20 = +0.0008、Mean MRR = +0.0007、Mean nDCG@20 = +0.0009。增益集中于 Top-20 的额外命中与更细微的排序提前。

## 5. 本轮耗时观测与取舍

| 组别 | 平均耗时（ms） | P95（ms） | 本地效率代理分* |
| --- | ---: | ---: | ---: |
| A | 3,294.61 | 3,841.54 | 0.9953 |
| B | 9,452.91 | 11,579.44 | 0.9756 |
| C | 6,255.78 | 7,716.84 | 0.9855 |
| D | 10,267.79 | 16,475.76 | 0.9632 |

\*效率代理分为本地观测，非官方分数。报告中“学术 API 逻辑调用数=100”是快照导出时保存的历史观测，**不是**本次 A/B/C/D 离线评分的调用量。

D 的平均耗时约为 C 的 1.64 倍，P95 约为 C 的 2.14 倍；相应换来的是上一节列出的有限指标增量。B 比 C 更慢而指标更低，故不适合作为折中方案。

## 6. 候选覆盖限制

离线覆盖诊断在同一冻结集合中得到：24 / 549 篇 Gold DOI 被候选命中，84 / 100 条查询的 Gold DOI 为零命中。这个事实限定了本轮重排实验的可达上限：未进入候选快照的 judged DOI 不可能被 A/B/C/D 恢复。

该诊断**不能单独**说明问题源于哪个环节；它不应被归因成来源能力、QueryIntent、身份规范化或排序模型的单一缺陷。若后续需改善覆盖，必须为明确的 query_id 另行审阅 QueryIntent、预估用量并由用户显式确认后导出新的候选快照；新快照不得与本集合混入比较。

## 7. 策略决策与执行门

| 目标 | 建议固定配置 | 理由 |
| --- | --- | --- |
| 开发指标最大化 | D | 当前所有列示 DOI-strict 指标最佳。 |
| 日常离线/生产时延控制 | C | 保留 D 的 F1@5、F1@10，且以更低观测时延达到接近的整体效果。 |
| 无本地模型或故障降级 | A | 无模型依赖、可复核的 RRF 兜底。 |

下一阶段必须由项目方在 C 与 D 中明确固定一个主策略；随后才能生成独立 Held-out 或 Future 的执行计划。执行计划应先离线生成并审阅，不得隐式调用学术来源；若需要新候选快照，仍须逐条完成 `usage-forecast` 和确认哈希授权。

2026-08-23，项目方要求继续下一阶段，故以 **D** 作为本次独立 Held-out 评测的指标优先主策略。该决定只固定后续离线重排配置，并不改变已冻结的 Validation100 结果，也不等同于将 D 部署为生产默认策略。

## 8. 证据产物

- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-a/A/report.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-b/B/report.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-c/C/report.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-baseline-d/D/report.md`
- `data/evaluation/longeval_2025/reports/longeval-validation100-candidate-coverage-v1/diagnostic.md`

上述 `data/` 产物为真实评测数据，按仓库规则处于 Git 忽略范围；本报告仅记录其可复核的聚合结论和哈希。
