# ScholarFlow 评测与测试规划（更新版）

## 1. 文档目的

本文件用于规划 ScholarFlow 的离线评测、在线端到端评测、排序消融、运行效率测试和结果归档流程。

评测目标对齐赛题第四部分：

1. **F1 Score：70%**
2. **运行效率：20%**
   - 学术 API 调用次数
   - LLM Token 消耗
   - 端到端延时
3. **回复结果结构化：10%**
   - 排序论文列表
   - 字段完整度
   - 分类、关系图等结构化结果

赛题尚未公开运行效率和结构化子项的官方归一化公式，因此本项目必须同时输出：

- 可复核的原始指标；
- 用于版本比较的本地代理分；
- 明确标注“本地代理分不等同于赛事官方得分”。

本评测体系的核心原则是：

> 在线检索只负责生成候选快照，排序消融和指标计算尽可能离线完成，避免重复调用学术 API 和消耗 LLM Token。

---

## 2. 当前系统参数边界

ScholarFlow 中不同阶段的“数量”含义不同，评测文档和代码不得统一称为 `candidate_count`。

### 2.1 `source_recall_count`

含义：

- 每个学术来源本轮最多召回多少篇论文；
- 影响 OpenAlex、Semantic Scholar、arXiv、DBLP、PubMed 等来源返回规模；
- 会直接影响召回率、API 响应时间和后续排序计算量。

当前 `QueryIntent` 中该字段范围为：

```text
1–100
```

当前自然语言请求入口并未直接暴露该字段。自然语言查询经过 Query Agent 后，才会在 `QueryIntent` 中形成实际来源召回规模。

因此：

- 通过自然语言入口评测时，应记录最终生成的 `source_recall_count`；
- 通过已保存或人工构造的 `QueryIntent` 评测时，可以直接调整该值；
- 仅更新评测文档时，不擅自修改自然语言 API 契约。

### 2.2 `semantic_top_k`

含义：

- BGE-M3 语义粗排后保留多少篇论文；
- 只影响本地排序阶段；
- 不应触发新的学术 API 调用。

该值应作为评测配置参数，而不是在评测代码中写死。

### 2.3 `cross_encoder_top_k`

含义：

- Cross Encoder 精细重排后保留多少篇论文；
- 通常也是进入 DeepSeek 精排的最大候选规模；
- 只影响本地模型和后续 LLM 成本，不应重新调用学术 API。

### 2.4 `target_paper_count`

含义：

- 系统最终期望返回多少篇论文；
- 当前 `QueryIntent` 支持 1–100；
- 当前自然语言请求入口最多允许 20。

该参数属于产品输出目标，不等同于来源召回数量。

### 2.5 `evaluation_top_k`

含义：

- 评分时截取前多少篇结果；
- 例如：

```text
F1@5
F1@10
F1@20
```

系统可以保存 20 篇结果，再离线计算多个 Top-K 指标，无需重新检索。

---

## 3. 数据集来源

### 3.1 PaSa

推荐来源：

- GitHub 项目：`bytedance/pasa`
- Hugging Face 数据集：`CarlanLark/pasa-dataset`

主要数据：

#### RealScholarQuery

- 约 50 条真实复杂学术查询；
- 由研究人员提出；
- 包含人工整理的相关论文集合；
- 最接近本赛题端到端论文搜索任务。

用途：

- 最终端到端外部测试；
- 不用于调阈值；
- 不用于挑选最优配置。

#### AutoScholarQuery

- 约 3.5 万条合成学术查询；
- 含 train、dev、test；
- 更适合开发期回归、参数比较和消融实验。

用途：

- `dev-small`：固定 20 或 50 条；
- `dev-full`：较大规模开发集；
- 不应每次随机重新抽样。

PaSa 数据应由用户手动下载并放入：

```text
data/evaluation/pasa/
```

Codex 不自动下载，不访问 Hugging Face，不提交完整数据集。

### 3.2 LitSearch

推荐来源：

- Hugging Face：`princeton-nlp/LitSearch`
- 论文：`LitSearch: A Retrieval Benchmark for Scientific Literature Search`

数据特点：

- 597 条 ML/NLP 文献检索查询；
- 64,183 篇固定语料；
- 含金标论文 ID；
- 适合固定语料下的检索与排序评测。

用途：

- BGE-M3、Cross Encoder 和排序链路消融；
- 避免实时 API 内容变化造成实验不稳定；
- 单独评估排序质量，不等同于完整在线搜索。

### 3.3 AstaBench PaperFindingBench

推荐来源：

- GitHub：`allenai/asta-bench`
- Hugging Face：`allenai/asta-bench`

用途：

- 后期外部标准化科研 Agent 验证；
- 成本、工具和运行过程评估。

限制：

- gated dataset；
- 需要用户接受条款；
- test 不用于训练和调试；
- 不重新分发原始数据；
- 不作为第一阶段评测模块的前置依赖。

### 3.4 ScholarFlow 自建验收集

建立 20–30 条人工复杂查询，覆盖：

- 中文查询；
- 英文查询；
- 中英混合查询；
- 主题 + 方法；
- 主题 + 数据集；
- 主题 + 年份；
- 主题 + venue；
- 排除条件；
- 医学、计算机和交叉学科；
- 来源降级和限流场景。

用途：

- QueryIntent 解析；
- 来源路由；
- 多轮搜索；
- 中文结构化展示；
- 异常降级；
- 端到端人工验收。

自建集不能替代公开 benchmark。

---

## 4. 评测分层

### 4.1 第一层：完全离线单元测试

目的：

- 验证评测框架正确性；
- 不访问任何网络；
- 不加载真实模型。

覆盖：

- DOI 规范化；
- arXiv ID 版本归一化；
- PMID、OpenAlex、Semantic Scholar、DBLP 标识匹配；
- 重复论文去重；
- Precision、Recall、F1；
- 缺失预测；
- 结构化评分；
- 报告输出。

资源消耗：

```text
学术 API：0
LLM 调用：0
Token：0
```

### 4.2 第二层：已有运行快照回放

读取已有：

```text
run_id
最终论文结果
usage
停止原因
降级信息
```

通过本地 API 或 SQLite 快照导出统一预测文件。

资源消耗：

```text
新增学术 API：0
新增 LLM Token：0
```

### 4.3 第三层：固定候选排序消融

先保存一份规范化、去重、RRF 后的候选快照，再离线执行：

1. RRF
2. RRF + BGE-M3
3. RRF + Cross Encoder
4. RRF + BGE-M3 + Cross Encoder

资源消耗：

```text
新增学术 API：0
DeepSeek Token：0
```

只消耗本地 CPU/GPU 时间。

### 4.4 第四层：低成本在线 Smoke Test

建议固定 5 条查询。

默认预算：

```text
查询数：5
最大轮数：1
每轮来源数：1
source_recall_count：20
target_paper_count：10
查询演化：关闭
Tavily：关闭
BGE-M3：按实验开关
Cross Encoder：按实验开关
DeepSeek：只保留一次最终精排
```

目的：

- 验证完整链路；
- 验证预测快照是否可保存；
- 验证 usage 是否完整；
- 不进行大规模正式评测。

### 4.5 第五层：开发集评测

建议：

```text
从完整 AutoScholarQuery dev GoldQuery 确定性封存 20 条
```

完整开发集当前有 1,000 条查询；`gold-subset-select` 必须以显式 `selection_id`、种子、输入哈希和完整 `query_id` 列表封存此 20 条子集，不能把“固定 20 条”误解为原始 dev 文件总量。

通过开发集选择：

- BGE-M3 是否开启；
- Cross Encoder 是否开启；
- 合理的候选规模；
- 最优本地排序链路；
- 是否值得增加 DeepSeek。

### 4.6 第六层：最终测试

最终配置冻结后再运行：

- RealScholarQuery；
- 可选 LitSearch；
- 可选 AstaBench validation/test。

最终测试不再调整阈值或参数。

---

## 5. 在线候选快照

### 5.1 快照生成边界

在线阶段只执行：

```text
查询规划
→ 来源检索
→ 规范化
→ 去重
→ RRF
→ 确定性规则过滤
→ 保存候选快照
```

是否立即执行 BGE、Cross Encoder 和 DeepSeek 应由运行配置决定，但评测系统必须支持保存“排序前候选快照”。

### 5.2 快照必须保存的字段

```json
{
  "snapshot_id": "snapshot-001",
  "query_id": "query-001",
  "run_id": "run-001",
  "query": "原始查询",
  "query_intent": {},
  "source_recall_count": 50,
  "target_paper_count": 20,
  "sources_used": ["openalex"],
  "raw_candidate_count": null,
  "normalized_candidate_count": 50,
  "deduplicated_candidate_count": 45,
  "filtered_candidate_count": 5,
  "ranking_candidate_count": 40,
  "source_counts": {"openalex": 50},
  "filter_reason_counts": {"exclude": 5},
  "papers": [],
  "usage": {
    "academic_api_calls": 1,
    "llm_calls": 1,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "latency_ms": 0,
    "retry_count": 0,
    "rate_limit_count": 0,
    "cache_hit_count": 0
  },
  "stop_reason": null,
  "warnings": []
}
```

快照契约版本 `1.1` 的 `snapshot_stage` 固定为 `pre_semantic_ranking`。`normalized_candidate_count` 是成功映射为统一论文记录、身份去重前的数量，`deduplicated_candidate_count` 是身份去重与 RRF 后、规则过滤前的数量，`filtered_candidate_count` 是确定性规则移除数量，`ranking_candidate_count` 是实际保存并进入本地排序的数量。只有来源适配层确实观测到供应商原始响应条目数时才填写 `raw_candidate_count`，否则必须保持 `null`。

### 5.3 快照复用规则

以下变化不重新生成在线快照：

- 修改论文标识匹配规则；
- 修改 F1 计算；
- 修改报告格式；
- 修改结构化评分；
- 修改 `evaluation_top_k`；
- 修改 `semantic_top_k`；
- 修改 `cross_encoder_top_k`；
- 开启或关闭 BGE-M3；
- 开启或关闭 Cross Encoder；
- 修改本地代理分阈值。

以下变化需要重新生成在线快照：

- 修改来源查询表达式；
- 修改来源路由；
- 修改 `source_recall_count`；
- 修改 QueryIntent；
- 修改 Query Agent Prompt；
- 修改多轮搜索策略；
- 修改来源调用顺序；
- 修改来源过滤或规范化逻辑。

---

## 6. 统一数据契约

### 6.1 金标查询

```json
{
  "query_id": "query-001",
  "query": "复杂学术查询",
  "relevant_papers": [
    {
      "doi": "10.xxxx/xxxx",
      "arxiv_id": null,
      "pmid": null,
      "openalex_id": null,
      "semantic_scholar_id": null,
      "dblp_key": null,
      "title": "论文标题",
      "year": 2024,
      "authors": ["Author A"]
    }
  ],
  "metadata": {
    "dataset": "RealScholarQuery",
    "split": "test"
  }
}
```

### 6.2 系统预测

```json
{
  "query_id": "query-001",
  "snapshot_id": "snapshot-001",
  "run_id": "run-001",
  "ranking_config": {
    "semantic_ranking_enabled": true,
    "semantic_top_k": 40,
    "cross_encoder_ranking_enabled": true,
    "cross_encoder_top_k": 20,
    "deepseek_enabled": false,
    "target_paper_count": 20
  },
  "papers": [],
  "usage": {
    "academic_api_calls": 0,
    "llm_calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "latency_ms": 0,
    "bge_latency_ms": 0,
    "cross_encoder_latency_ms": 0
  },
  "relations": [],
  "warnings": []
}
```

---

## 7. 论文匹配规则

按以下优先级匹配：

1. DOI；
2. arXiv ID；
3. PMID；
4. OpenAlex ID；
5. Semantic Scholar ID；
6. DBLP key；
7. 标准化标题 + 年份；
8. 标准化标题 + 第一作者，仅作为最后回退。

规范化要求：

### DOI

- 转小写；
- 移除 `https://doi.org/`；
- 移除 `http://dx.doi.org/`；
- 移除 `doi:`；
- 移除末尾标点。

### arXiv ID

- 移除 `https://arxiv.org/abs/`；
- 移除 `https://arxiv.org/pdf/`；
- 移除 `.pdf`；
- 移除 `v1`、`v2` 等版本号；
- 转小写。

### 匹配约束

- 预测结果先去重；
- 同一预测论文只能匹配一个金标；
- 同一金标不能被多个预测重复计为 TP；
- 默认评估前 20 篇；
- cutoff 可配置；
- 无强标识符时才允许标题回退；
- 模糊标题匹配不得覆盖强标识符冲突。

---

## 8. 指标定义

### 8.1 检索指标

每条查询计算：

```text
Precision@K
Recall@K
F1@K
```

其中：

```text
Precision@K = Top-K 中命中的相关论文数 / Top-K 去重论文数

Recall@K = Top-K 中命中的相关论文数 / 金标相关论文总数

F1@K = 2 × Precision × Recall / (Precision + Recall)
```

至少输出：

- P@5、P@10、P@20；
- R@5、R@10、R@20；
- F1@5、F1@10、F1@20；
- Micro Precision、Recall、F1；
- Macro Precision、Recall、F1；
- MRR；
- nDCG@10、nDCG@20；
- 重复预测数；
- 缺失标识论文数；
- 缺失预测查询数。

赛事聚合方式未公开时：

- 主报告同时展示 Micro 和 Macro；
- 本地代理总分默认使用 Macro F1@20；
- 配置文件允许切换；
- 不宣称任何一种是官方聚合方式。

### 8.2 运行效率

在线阶段记录：

- 学术 API 逻辑调用数；
- 实际 HTTP 请求数；
- 每来源调用数；
- 重试次数；
- 429 次数；
- 缓存命中数；
- Query Agent 调用数；
- DeepSeek 调用数（按已尝试的小批次计，失败批次也留档）；
- 输入 Token；
- 输出 Token；
- 总 Token；
- 端到端耗时；
- P50、P95 延时。

离线排序记录：

- BGE-M3 输入候选数；
- BGE-M3 输出候选数；
- BGE-M3 延时；
- Cross Encoder 输入候选数；
- Cross Encoder 输出候选数；
- Cross Encoder 延时；
- 总排序耗时；
- 本地模型设备；
- 批大小；
- OOM 降批次数。

本地效率代理分采用配置化阈值：

```text
值 <= target：1
target < 值 < limit：线性下降
值 >= limit：0
```

缺失指标必须标记为缺失，不自动填 0。

### 8.3 回复结构化

建议组成：

- 排序论文列表合法性：40%；
- 关键字段完整度：40%；
- 关系或分类结构合法性：20%。

关键字段：

- 标题；
- 年份；
- 作者；
- venue；
- 来源；
- 稳定标识符或合法链接；
- 相关性分数或等级；
- 推荐理由。

关系结构检查：

- `source`；
- `target`；
- `type`；
- 节点必须指向本次结果集合；
- 不使用 LLM 推断不存在的引用关系。

结构化评分使用确定性规则，不使用 LLM Judge。

---

## 9. BGE-M3 与 Cross Encoder 消融

### 9.1 固定候选快照

四组实验必须共享：

- 相同查询；
- 相同 QueryIntent；
- 相同 `source_recall_count`；
- 相同来源；
- 相同规范化结果；
- 相同去重结果；
- 相同 RRF 候选快照；
- 相同金标；
- 相同 `evaluation_top_k`。

不得为每种本地排序配置重新调用学术 API。

### 9.2 第一阶段：固定规模比较开关

默认建议：

```text
source_recall_count = 50
semantic_top_k = 40
cross_encoder_top_k = 20
target_paper_count = 20
evaluation_top_k = [5, 10, 20]
DeepSeek = 关闭
```

配置：

| 编号 | BGE-M3 | Cross Encoder | DeepSeek |
|---|---:|---:|---:|
| A | 关 | 关 | 关 |
| B | 开 | 关 | 关 |
| C | 关 | 开 | 关 |
| D | 开 | 开 | 关 |

比较：

- F1@5、@10、@20；
- Recall@20；
- MRR；
- nDCG@20；
- 本地排序耗时；
- GPU/CPU 设备；
- 各阶段候选数量。

### 9.3 第二阶段：只调整最佳链路的候选规模

假设 D 最优，再比较：

| 规模 | source_recall_count | semantic_top_k | cross_encoder_top_k | target_paper_count |
|---|---:|---:|---:|---:|
| Small | 20 | 20 | 10 | 10 |
| Medium | 50 | 40 | 20 | 20 |
| Large | 100 | 60 | 20 | 20 |

避免测试全部参数的笛卡尔积。

### 9.4 第三阶段：只对最佳配置增加 DeepSeek

配置：

| 编号 | 本地排序 | DeepSeek |
|---|---|---:|
| D | BGE-M3 + Cross Encoder | 关 |
| E | BGE-M3 + Cross Encoder | 开 |

只对开发集最优配置调用 DeepSeek。

DeepSeek 候选量建议：

```text
10–20 篇
```

批大小建议：

```text
每批 10 篇
```

这样 20 篇通常只产生两次论文精排调用。

---

## 10. 低成本执行流程

### 10.1 阶段一：离线 fixture

```text
5 条 fixture
0 API
0 Token
```

验证评测模块后再继续。

### 10.2 阶段二：导出已有 run_id

```text
读取已有 SQLite 结果与 usage
0 外部 API
0 新增 Token
```

优先使用已有运行结果调试报告。

### 10.3 阶段三：在线 Smoke

建议：

```text
5 条查询
1 轮
1 来源
source_recall_count = 20
target_paper_count = 10
查询演化关闭
Tavily 关闭
```

可以预先保存 QueryIntent，避免重复调用 Query Agent。

### 10.4 阶段四：反复离线调试

使用同一批候选快照反复调整：

- 标识匹配；
- F1；
- Top-K；
- BGE 开关；
- Cross Encoder 开关；
- 候选截断；
- 报告格式；
- 代理评分。

该阶段不重新调用学术 API。

### 10.5 阶段五：开发集

固定：

```text
从完整 AutoScholarQuery dev 中已封存的 20 条
```

先比较 A/B/C/D，再只对最佳配置调整候选规模。

### 10.6 阶段六：DeepSeek 对比

只对最佳本地排序配置运行一次 DeepSeek 对比。

### 10.7 阶段七：最终测试

最终配置冻结后：

- RealScholarQuery 只运行一次；
- 保存所有原始预测、快照、usage 和错误；
- 后续只离线生成报告。

---

## 11. 实验命名与结果归档

### 11.1 实验 ID

建议：

```text
<dataset>-<split>-<ranking>-<size>-<timestamp>
```

例如：

```text
pasa-dev-bge_ce-medium-20260718
```

### 11.2 每次实验目录

```text
evaluation/results/<experiment_id>/
├─ experiment.json
├─ queries.jsonl
├─ query_intents.jsonl
├─ candidate_snapshots.jsonl
├─ predictions.jsonl
├─ query_metrics.jsonl
├─ summary.json
├─ report.md
├─ usage.jsonl
└─ errors.jsonl
```

### 11.3 `experiment.json`

至少保存：

```json
{
  "dataset": "AutoScholarQuery",
  "split": "dev",
  "query_ids_hash": "sha256",
  "query_count": 20,
  "source_recall_count": 50,
  "semantic_ranking_enabled": true,
  "semantic_top_k": 40,
  "cross_encoder_ranking_enabled": true,
  "cross_encoder_top_k": 20,
  "deepseek_enabled": false,
  "target_paper_count": 20,
  "evaluation_top_k": [5, 10, 20],
  "candidate_snapshot_hash": "sha256",
  "model_versions": {},
  "code_commit_sha": null,
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601"
}
```

本地工作区未提交时，`code_commit_sha` 可以记录当前基线提交和工作区状态，不强制执行 Git 操作。

---

## 12. 评测模块目录

```text
evaluation/
├─ README.md
├─ __main__.py
├─ cli.py
├─ config/
│  ├─ default.json
│  ├─ datasets.json
│  └─ experiments/
├─ contracts/
│  ├─ gold.py
│  ├─ prediction.py
│  └─ snapshot.py
├─ adapters/
│  ├─ base.py
│  ├─ pasa.py
│  ├─ litsearch.py
│  ├─ astabench.py
│  └─ scholarflow.py
├─ metrics/
│  ├─ identifiers.py
│  ├─ retrieval.py
│  ├─ ranking.py
│  ├─ efficiency.py
│  ├─ structure.py
│  └─ aggregate.py
├─ runners/
│  ├─ fixture.py
│  ├─ snapshot_export.py
│  ├─ offline_ranking.py
│  └─ online_search.py
├─ reports/
│  ├─ json_report.py
│  └─ markdown_report.py
├─ fixtures/
├─ tests/
└─ results/
```

完整数据集和结果目录不提交仓库。

建议本地数据：

```text
data/evaluation/
├─ pasa/
├─ litsearch/
├─ astabench/
└─ manual/
```

---

## 13. 固定 PaSa 20 条端到端初步评测

已封存的 `pasa-auto-dev-ranking20.gold.jsonl` 与对应 manifest 可用于一次完整自然语言入口评测，不得重新抽样、替换零命中查询、使用 QueryIntent 直通或复用候选快照。

流程固定为：

1. `pasa-end-to-end-plan` 完全离线核验固定 20 条并写出自然语言请求计划；
2. 用户手动启动后端后，显式运行 `pasa-end-to-end-execute --allow-online-end-to-end`；该步骤依次调用 `/api/v1/search/natural-multi-round`，再只读读取同一 `run_id` 的论文、usage 与受限引用图；
3. `pasa-end-to-end-score` 完全离线写出 `report.json`、`query_metrics.jsonl` 与 `report.md`。

失败、超时、空结果和来源异常必须原样归档并保留在 20 条分母。报告以 P/R/F1@20 的 Macro 与 Micro、原始运行效率、列表字段完整率和图事实合法性为主体，并固定注明“PaSa AutoScholarQuery dev固定20条初步评测，非完整数据集成绩，非赛事官方成绩”。当前生产只读快照尚未公开实际 HTTP 请求、重试、429 和 LLM 分阶段调用明细；这些字段在报告中必须为 `N/A` 与可观测性缺口，绝不能以零填充或派生为官方分数。

## 14. 文档同步要求

实施评测模块前，应检查并同步以下文档：

### 13.1 `docs/05_阶段五_语义排序与智能核验.md`

需要更新：

- 固定候选数量改为“默认建议值 + 可配置参数”；
- 明确 `semantic_top_k` 和 `cross_encoder_top_k`；
- 修正与当前 BGE/Cross Encoder 独立开关不一致的旧描述；
- 加入使用同一候选快照进行本地排序消融的原则；
- 加入各阶段候选数和耗时记录要求。

### 13.2 `docs/07_阶段七_系统集成与交付.md`

需要增加：

- benchmark/evaluation 测试层；
- 固定候选快照；
- 离线指标计算；
- 参数化消融；
- 结果归档；
- API、Token、延时和本地模型耗时统计；
- 数据集许可和不提交完整 benchmark 数据的规则。

### 13.3 `docs/acceptance/文献搜索端到端验收清单.md`

需要增加：

- 低成本 Smoke Test；
- 运行前预计 API/LLM 调用量；
- 同一 `run_id` 只读恢复不重新搜索；
- 保存候选规模、模型开关和停止原因；
- 失败后不重复提交同一高成本查询。

### 13.4 `AGENTS.md`

只加入长期稳定规则，不复制整份评测文档。

建议增加：

```markdown
## 评测与消融规则

- 评测逻辑必须与生产搜索流程分离。
- 必须区分 source_recall_count、semantic_top_k、cross_encoder_top_k、target_paper_count 和 evaluation_top_k。
- BGE-M3 和 Cross Encoder 消融必须使用同一份规范化、去重后的候选快照。
- 本地排序和指标调整不得重复调用学术 API。
- 只有来源查询、来源召回规模、QueryIntent 或多轮搜索策略改变时，才重新生成候选快照。
- DeepSeek 只对开发集最优的少量本地排序配置执行。
- 每次评测必须保存数据集、split、查询 ID、配置、快照 ID、模型版本、候选数量、API、Token、耗时和停止原因。
- 本地代理分不得宣称为赛事官方得分。
- 数据集下载、真实 API、模型运行和完整 benchmark 必须由用户显式执行。
```

---

## 14. 第一阶段实施范围

第一阶段只实现：

1. 统一 JSONL 数据契约；
2. 论文标识符规范化；
3. Precision、Recall、F1；
4. Micro/Macro 聚合；
5. MRR、nDCG；
6. 效率统计；
7. 结构化评分；
8. 报告输出；
9. fixture；
10. 完全离线测试。

第一阶段不实现：

- PaSa 下载；
- LitSearch 下载；
- AstaBench 下载；
- 在线批量搜索；
- BGE-M3 真实推理；
- Cross Encoder 真实推理；
- DeepSeek 真实调用；
- Git 操作。

---

## 15. 第一阶段验收条件

- fixture 可以完全离线运行；
- 不访问网络；
- 不读取 `.env`；
- DOI、arXiv、PMID、平台 ID 测试通过；
- 重复预测不重复计 TP；
- 缺失预测按空结果评分；
- 同时输出 P/R/F1@5、@10、@20；
- 输出 Micro 和 Macro；
- 缺失效率指标标记为缺失；
- 本地代理分明确标注非官方；
- 输出 JSON、JSONL 和 Markdown；
- 不改变 ScholarFlow 生产搜索行为；
- 不添加不必要依赖；
- 不生成虚假真实评测结果。

---

## 16. 提交给 Codex 的说明

```text
请检查当前 ScholarFlow 仓库的 feature 分支。

本次只修改本地工作区，不执行任何 Git 操作，包括：
git add、git commit、git push、git checkout、创建分支或 PR。

开始前完整阅读：

1. AGENTS.md
2. docs/00_ScholarFlow_总体规划.md
3. docs/05_阶段五_语义排序与智能核验.md
4. docs/07_阶段七_系统集成与交付.md
5. docs/acceptance/文献搜索端到端验收清单.md
6. docs/evaluation/ScholarFlow_评测规划.md
7. 当前 PaperRecord、QueryIntent、NaturalSearchRequest、SearchRunState、usage、
   BGE-M3 排序、Cross Encoder 排序、多轮检索和结果读取代码及其测试

本任务分两步。

第一步只做检查和规划，不立即修改文件。

请核对：

1. source_recall_count 的真实范围和传递路径；
2. NaturalSearchRequest 是否直接支持来源召回数量；
3. semantic_top_k 的实际配置位置；
4. cross_encoder_top_k 或 candidate_limit 的实际配置位置；
5. target_paper_count 与最终分页结果的关系；
6. BGE-M3 和 Cross Encoder 独立开关的当前实际行为；
7. 各阶段候选数量是否已记录到 SearchRunState 或 usage；
8. docs/05、docs/07、验收清单和 AGENTS.md 中是否存在过时描述。

必须区分：

- source_recall_count：每个来源召回数量；
- semantic_top_k：BGE-M3 后保留数量；
- cross_encoder_top_k：Cross Encoder 后保留数量；
- target_paper_count：最终返回目标数量；
- evaluation_top_k：F1@5、F1@10、F1@20 的评分截断。

不得把它们统一命名为 candidate_count。

评测原则：

1. 在线搜索只生成候选快照；
2. BGE-M3 和 Cross Encoder 消融必须复用同一候选快照；
3. 调整本地排序、Top-K、指标或报告时，不重新调用学术 API；
4. 只有来源查询、source_recall_count、QueryIntent 或多轮策略改变时，
   才重新生成在线候选快照；
5. 第一轮消融关闭 DeepSeek；
6. DeepSeek 只对开发集表现最好的少量本地配置调用；
7. 不使用 LLM Judge；
8. 不自动下载 PaSa、LitSearch 或 AstaBench；
9. 不自动运行完整 benchmark；
10. 不修改 .env；
11. 不生成虚假的真实 F1。

请先给出文件级实施规划，至少包含：

- 当前实现状态；
- 文档与代码不一致；
- 需要新增或修改的文件；
- 每个文件的具体修改内容；
- 是否需要修改生产代码；
- 候选快照格式；
- BGE/Cross Encoder 消融矩阵；
- API 和 Token 控制方案；
- AGENTS.md 需要增加的长期规则；
- 用户后续手动执行步骤。

我确认规划后，再进行文档同步：

A. 更新 docs/evaluation/ScholarFlow_评测规划.md；
B. 更新 docs/05_阶段五_语义排序与智能核验.md；
C. 更新 docs/07_阶段七_系统集成与交付.md；
D. 视需要更新 docs/acceptance/文献搜索端到端验收清单.md；
E. 同步 AGENTS.md。

文档修改完成后停止，不要提前实现评测代码。

后续我再次确认后，才进入评测模块第一阶段：
只实现离线契约、指标、fixture、pytest 和报告输出。
```

---

## 17. 下一步规划

1. 先让 Codex 根据本文件检查当前代码和现有文档；
2. 只输出文件级更新计划；
3. 确认后同步 `docs/05`、`docs/07`、验收清单和 `AGENTS.md`；
4. 再确认后实现评测模块第一阶段；
5. fixture 验收通过后，用户手动准备 PaSa；
6. 接入固定开发集；
7. 保存候选快照；
8. 运行 BGE-M3/Cross Encoder 离线消融；
9. 只对最优配置增加 DeepSeek；
10. 最终配置冻结后运行 RealScholarQuery。

---

## 18. 实施状态（2026-07-18）

评测模块第一阶段已按本规划落地到独立 `evaluation/` 目录，未修改生产 API 和搜索工作流：

- 已实现金标、预测、排序数量、usage 和报告的 JSONL/Pydantic 契约；
- 已实现 DOI、arXiv、PMID、OpenAlex、Semantic Scholar、DBLP 标识规范化、保守匹配和保序去重；
- 已实现 P/R/F1、Micro/Macro、MRR 与二元 nDCG；
- 已实现缺失值不补零的效率汇总，以及不使用 LLM Judge 的确定性结构代理分；
- 已实现 JSON、查询级 JSONL 和 Markdown 报告，所有效率、结构和综合分均明确标记为本地代理分（非官方）；
- 已提供纯合成 fixture 与完全离线测试；
- 未实现也未自动运行数据集下载、在线候选生成、BGE-M3、Cross Encoder、DeepSeek 或完整 benchmark。

第一阶段命令、输入契约和目录说明见 `../evaluation/README.md`。下一阶段应先定义排序前候选快照契约和只读加载器，再接入由用户显式生成的候选快照；不得直接把现有 SQLite 最终结果快照冒充排序前候选快照。

---

## 19. 第二阶段实施状态（2026-07-18）

候选快照和离线排序消融编排已落地，仍未修改生产 API 或搜索工作流：

- 新增 `CandidateSnapshot` 契约并升级为 `1.1`，阶段固定为 `pre_semantic_ranking`，明确表示规范化、身份去重、RRF 与确定性规则过滤后且 BGE-M3/Cross Encoder 前；
- 快照保存 `source_recall_count`、`target_paper_count`、来源、成功映射数、身份去重后数量、规则过滤数、实际排序输入数、冻结 QueryIntent、在线 usage、停止原因和带时区时间；未观测的供应商原始条目数保持空值；
- 快照以不含 `snapshot_hash` 自身的规范化 JSON 计算 SHA-256，加载时校验哈希、连续排名、RRF 与论文 ID 稳定顺序、来源计数、过滤计数、来源覆盖和论文身份唯一性；
- 新增标准 A/B/C/D 矩阵，四组共享 `source_recall_count` 与 `evaluation_top_k`，并强制关闭 DeepSeek；
- A 保持 RRF，B 执行 BGE-M3，C 的 Cross Encoder 直接读取完整快照，D 的 Cross Encoder 读取 BGE-M3 保留候选；
- 离线运行器只依赖显式注入的 `OfflineRankingScorer`，不会实例化、下载或加载真实模型；
- 每个配置都从同一快照的深拷贝开始，并保存相同 `snapshot_hash`、阶段候选数量和本地模型统计；
- 新增 `snapshot-check` 与 `ablation-plan` CLI，前者只读校验，后者只生成 `academic_api_calls=0`、`deepseek_calls=0` 的任务计划，不执行排序模型；
- 合成快照和纯替身测试已覆盖篡改、重复候选、召回规模冲突、DeepSeek 禁用和 A/B/C/D 候选传递。

第二阶段完成时尚不包含生产候选快照导出、真实 BGE-M3/Cross Encoder 适配器、DeepSeek 对比、公开数据集下载或完整 benchmark。生产导出边界已确认位于规则过滤后、BGE-M3 前；不得直接读取现有 SQLite 最终结果代替排序前快照。候选导出随后在第 21 节落地。

---

## 20. 第三阶段内部候选边界实施状态（2026-07-18）

生产搜索已完成行为保持型内部拆分，尚未增加快照导出命令或评测 HTTP API：

- 新增 `CandidateGenerationService`，统一执行来源路由与调用、已规范化 `PaperRecord` 汇总、身份融合/RRF、确定性规则过滤和独立网页发现；
- 新增 `CandidateGenerationResult`，分别保存学术来源与网页来源数量和错误，并校验成功映射数、身份去重后数量、合并数、过滤数和实际排序输入数；
- 候选生成服务不依赖、构造或调用 BGE-M3、Cross Encoder、DeepSeek 和覆盖分析；
- `MultiSourceRecallCoordinator` 改为先调用候选生成服务，再按原顺序执行 BGE-M3、Cross Encoder、DeepSeek 和覆盖分析；
- 生产组合根分别缓存候选生成服务和完整排序协调器，现有公共 FastAPI 请求与响应契约不变；
- `MultiSourceRecallResult.raw_paper_count` 因公共兼容仍保留原字段名，但其真实含义明确为适配器成功映射并进入身份融合的统一论文数，不代表供应商原始响应条目数；
- 新增候选生成服务的纯替身测试，覆盖正常融合与过滤、来源异常、未注册来源和网页发现分流，不访问网络或模型。

该阶段完成后安排的下一项是由用户显式触发的单轮快照导出适配层和文件写入边界。该入口必须接收已经准备好的 `QueryIntent`，默认拒绝网页发现，不运行 Query Agent、本地排序模型或 DeepSeek；现有离线命令不得隐式调用生产候选服务。实施结果见第 21 节。

---

## 21. 第三阶段候选快照导出实施状态（2026-07-19）

单轮在线候选已能由用户显式封存为离线消融输入，生产公共 API 和完整搜索流程未修改：

- 新增生产结果到评测契约的适配器，将 `CandidateGenerationResult` 映射为 `CandidateSnapshot 1.1`，并按 RRF 降序、论文 ID 升序稳定封存；
- 新增 `snapshot-export` CLI，必须同时提供已有 `QueryIntent` 文件、`query_id`、`snapshot_id`、尚不存在的输出路径和 `--allow-online-sources`；
- 所有静态条件与输出冲突在候选服务装配前校验；未授权时不读取 QueryIntent、不读取生产配置，也不创建来源适配器；
- 输入必须为第一轮，显式设置 `source_recall_count`，关闭网页发现、BGE-M3 和 Cross Encoder；导出链路不调用 Query Agent、DeepSeek、覆盖分析或多轮控制器；
- 导出器只调用一次 `CandidateGenerationService.generate`，把规则过滤后的结果写成单条 UTF-8 JSONL，并以 SHA-256 校验内容；已存在目标不会被覆盖；
- `usage.academic_api_calls` 表示路由学术来源的逻辑调用数，缓存命中和候选生成耗时按现有可观测值冻结；当前无法可靠聚合的实际 HTTP 请求、重试和限流次数保持 `null`，LLM 调用和 Token 固定为零；
- `fixture`、`snapshot-check` 和 `ablation-plan` 仍为完全离线命令，不会因新增导出入口而装配生产服务；
- 测试只注入合成候选生成器，覆盖映射、阶段计数、哈希、已有文件保护、不安全 QueryIntent 拒绝和 CLI 授权，不访问真实来源、LLM 或本地模型。

为减少 20 条开发集手动复制命令的错误，新增 `scripts/export_pasa_snapshot_batch.ps1` 作为用户显式执行的外层编排器：默认 `-BatchSize 1`，按 QueryIntent manifest 冻结顺序跳过已通过 `snapshot-check` 且没有“学术来源降级”警告的快照；每条新导出后立即离线复核，遇到导出或复核失败立即停止。脚本只调用既有 `snapshot-export --allow-online-sources`，不读取 `.env`、不保存认证信息、不加载模型，也不自动递归执行全部 20 条。

用户手动命令和输入要求见 `../evaluation/README.md`。本入口不会下载数据集或模型，也不会由 Codex 自动运行。生成一次快照后，BGE-M3/Cross Encoder 保留数量、`evaluation_top_k`、指标和报告的任何调整都必须离线复用该快照，不得重新调用学术 API。

当开发集已存在 20 份单查询快照时，必须先使用 `python -m evaluation snapshot-collection-assemble` 组装为一个共享 JSONL 与集合 manifest，再生成消融计划。组装器只接受 `query-intent-manifest-v1`，按其 `query_id_order` 固定输出顺序，逐份复用正式快照加载器验证 SHA-256、身份去重及来源统计，并要求 `source_recall_count`、`target_paper_count` 和快照内 QueryIntent 与 manifest 一致。带“学术来源降级”警告的历史失败产物会被排除；同一查询存在多个其余有效重试时必须通过 `--snapshot-override query_id=目录内相对路径` 显式冻结选择。候选数量低于 `target_paper_count` 的真实成功快照仍保留并在集合 manifest 记录，不能以此重调学术 API。

该阶段完成后安排的下一项是公开评测数据到现有 `GoldQuery`/fixture 契约的纯离线适配与校验边界，不自动下载 PaSa、RealScholarQuery 或其他数据集；实施结果见第 22 节，真实数据准备和完整转换继续由用户显式执行。

---

## 22. 第四阶段本地数据集金标导入实施状态（2026-07-19）

已新增完全离线的 `prepared-dataset-gold-v1` 导入边界，将用户手动准备的公开数据集金标转换为既有 `GoldQuery` JSONL：

- 输入记录只包含 `source_query_id`、查询、相关论文和可 JSON 序列化的来源标量元数据；
- CLI 要求用户显式指定 `dataset`、`split`、输入与尚不存在的输出路径，输出查询标识固定为 `dataset:split:source_query_id`；
- 导入器冻结 `dataset`、`split`、`source_query_id` 和转换版本，拒绝来源元数据覆盖这些保留字段；
- 导入前校验重复原始查询标识，并以现有 DOI、arXiv、PMID、平台 ID、标题回退规则拒绝重复相关论文；不静默去重或补全；
- 输入和输出均为本地 UTF-8 JSONL，输出通过同目录临时文件发布且不得覆盖已有结果；
- 不读取 `.env`，不下载、访问或猜测 PaSa、RealScholarQuery 或其他公开数据集的原始格式，也不调用学术 API、LLM 或模型；
- 合成 fixture 与纯离线测试覆盖命名空间、审计元数据、重复边界、已有文件保护和 CLI 闭环。

本阶段不宣称支持任何未经用户确认字段版本的原生公开数据集。下一步需由用户提供特定数据集版本的字段说明或脱敏样例后，才针对该版本新增独立原生适配器；数据下载和完整 benchmark 仍由用户显式执行。

---

## 23. PaSa 选择性下载脚本实施状态（2026-07-19）

已新增 `scripts/download_pasa_dataset.py`，仅供用户显式下载已获授权的 PaSa 数据：

- 使用 `huggingface_hub.snapshot_download` 和 `repo_type="dataset"` 请求 `CarlanLark/pasa-dataset`；
- 默认只允许下载 `AutoScholarQuery/dev.jsonl`，可通过可重复的 `--subset` 增加 `RealScholarQuery/test.jsonl`、`paper_database/id2paper.json` 或显式选择 `all`；
- 支持 `--output-dir`、`--revision` 和 `--force`，默认输出到受 Git 忽略的 `data/evaluation/pasa/`；
- 通过 `token=True` 仅使用本机 `hf auth login` 保存的凭据，代码、日志和参数均不保存或输出 Token；
- 对 gated 条款/未授权、未登录、网络或超时、仓库或文件缺失、以及下载后本地文件缺失分别输出可操作的安全错误；
- 下载完成仅报告实际文件路径与字节大小；离线测试注入 mock 下载函数，不联网；
- 脚本不由 Codex 自动运行，用户必须先在 Hugging Face 页面接受数据集条款并手动完成登录。

该脚本只解决“取得用户有权访问的原始文件”，不改变现有 `prepared-dataset-gold-v1` 契约，也不自动解析 PaSa 原始字段。下一步仍需用户提供特定 PaSa revision 的字段说明或脱敏样例后，再实现原生格式适配器。

---

## 24. PaSa AutoScholarQuery 原生金标导入实施状态（2026-07-19）

用户已在本地显式下载 `AutoScholarQuery/dev.jsonl`，并确认当前记录字段为 `qid`、`question`、`answer`、`answer_arxiv_id` 与 `source_meta`。已新增完全离线的 `pasa-gold-import`：

- 只接受上述已确认字段且拒绝未知字段，避免对 gated 数据集做猜测性兼容；
- 标题与 arXiv ID 必须按相同索引配对；非空 ID 列表长度不一致、空白标题/标识均直接报错；
- PaSa `source_meta` 的标量字段会加 `pasa_source_` 前缀，另冻结原始映射版本；
- PaSa 原始 `answer` 若按统一论文身份规则出现重复，适配器保留首次出现论文并将移除数量冻结为 `pasa_duplicate_answer_count`；这只处理已确认的来源标注重复，通用准备数据导入仍拒绝重复金标；
- 转换结果仍复用 `prepared-dataset-gold-v1` 的数据集命名空间和原子新文件写入边界；
- 当前 CLI 仅允许 `--split auto-dev`，输入和输出全部由用户显式指定；不读取 `.env`，不访问网络、API、LLM、模型或论文数据库；
- 合成离线测试覆盖字段配对、未知字段、PaSa 来源重复审计、已有输出保护和 CLI 闭环，不使用真实 PaSa 内容。

`RealScholarQuery/test.jsonl` 的原始字段尚未在本地确认，不能假定与 AutoScholarQuery 相同。下一步如需支持它，先由用户显式下载该文件并提供允许读取的样例，再新增独立契约和适配器。

## 25. 开发集子集封存实施状态（2026-07-19）

完整 PaSa 开发集 GoldQuery 已由用户转换并只读验收为 1,000 条查询、2,531 篇去重后金标论文；这与“开发集评测固定 20 条”的子集规模并不冲突。新增完全离线的 `python -m evaluation gold-subset-select`：

- 仅读取用户明确指定的完整 `GoldQuery` JSONL，要求显式 `--count`、`--selection-id` 与 `--seed`，不会把 20 或其他数量写死在实现中；
- 以 `sha256-query-id-v1` 对策略名、选择标识、种子和 `query_id` 计算稳定排序，输入行顺序改变不会改变成员和排名；
- 写入新的子集 `GoldQuery` JSONL 与独立 `gold-subset-manifest-v1`，后者保存输入原始字节 SHA-256、输出规范化 SHA-256、总数、选择数及完整 `selected_query_ids`；
- 两个输出必须均为新文件，输入重复 `query_id`、数量越界、空选择标识或空种子均明确报错；命令不读取 `.env`、不调用学术 API、LLM、模型或生产搜索；
- 子集 manifest 必须与后续 `QueryIntent`、候选快照、排序矩阵和报告一同保存。改变子集成员需要用户显式重新授权相应查询的候选生成；只改离线 Top-K、评分或报告不需要也不得重新调用学术 API。

---

## 26. 本地 BGE-M3 评分适配器实施状态（2026-07-19）

已在独立 `evaluation/` 层新增可注入的 `BgeM3OfflineScorer`，未修改生产 API、生产 `BgeM3Encoder`、候选快照或在线搜索流程：

- 适配器实现 `OfflineRankingScorer` 所需的 `score` 边界，输入始终是已经规范化、身份去重、RRF 与确定性规则过滤后的同一候选快照；它不调用学术 API、Query Agent、LLM 或 DeepSeek；
- 构造时只接受用户明确提供的本地模型目录，并要求目录已经存在且含 `config.json`；远程模型仓库名、缺失路径和不完整目录都会在模型库导入前被拒绝，因此不会以补全路径为由下载模型；
- 模型导入和加载均延迟到用户显式发起的首个非空评分；空候选只返回零长度审计结果，不创建编码器或加载模型；
- 论文文本固定为独立的 `evaluation_bge_m3_text_v1`：标题、关键词、摘要、venue 和年份按稳定顺序构造，版本会与模型标识一同写入阶段审计；只使用快照中已冻结的公开字段，不复用生产模型实例，也不混入来源原始 JSON、引用量或未冻结状态；
- 评分记录实际设备、批大小、耗时和最多一次 OOM 降批重试；分数数量或 dense 向量维度异常直接失败，绝不静默错位排序；
- 离线测试仅注入编码器替身，覆盖文本稳定性、空候选不加载模型、一次 OOM 降批和本地路径边界；不读取 `.env`、不访问网络，也不需要真实模型权重。

本轮仅提供可替换适配层，不新增自动执行模型的 CLI，也不生成真实预测或报告。下一步应单独实现必须带用户显式本地模型授权的离线结果执行与原子归档入口；随后再以独立闭环实现 Cross Encoder 适配器。两步都必须复用当前的 20 条集合快照和 80 任务计划，不能重新调用学术 API。

---

## 27. 计划内本地排序执行与归档实施状态（2026-07-19）

已新增 `python -m evaluation ablation-execute`，用于由用户显式授权后执行既有计划的离线排序子集；未修改生产 API、候选生成或在线搜索：

- 命令必须同时读取已封存候选集合、矩阵和已生成的 `ablation-plan`，逐项核对 `matrix_id`、快照 ID、快照内容 SHA-256 与计划实验范围；不匹配时拒绝执行，不能替换在线候选；
- 执行者必须显式给出一个或多个 `--experiment`，当前仅支持 A（RRF）和 B（BGE-M3）；C/D 因 Cross Encoder 适配器尚未实现而明确失败，不会生成伪造结果；
- B 必须同时提供 `--allow-local-models`、本地 BGE-M3 目录、设备和批大小；构造期仍不加载模型，真实加载只会发生在用户手动执行的非空评分阶段；A 不允许额外传入 BGE 路径以免误导模型已执行；
- 每个实际任务结果都写为 `OfflineAblationResult` JSONL；配套 `offline-ranking-run-manifest-v1` 冻结矩阵、计划、集合和结果的原始字节 SHA-256，以及实际实验、快照哈希、任务数与本地阶段；不记录模型目录或查询正文；
- 结果 JSONL 和 manifest 必须是新路径，并以同目录临时文件原子发布；任何计划校验、模型或阶段错误都不写正式输出；
- 离线测试以合成快照和替身 BGE 覆盖 A/B 执行、归档、同名输出拒绝与 C/D 拒绝，不访问网络、不读取 `.env`、不加载真实模型。

当前 80 个 A/B/C/D 计划任务中，A/B 共 40 个已具备用户显式执行的实现边界；C/D 的 40 个仍待 Cross Encoder 适配器。后续指标和报告只读取这些归档 JSONL 与既有金标，不得重新调用学术 API；下一独立闭环是 Cross Encoder 本地适配器及其同等授权边界。

---

## 28. 消融结果离线评分实施状态（2026-07-19）

已新增 `python -m evaluation ablation-score`：它先核验 `OfflineAblationResult` JSONL 与执行 manifest 的结果 SHA-256，再按实验将已归档 `PredictionRecord` 按 GoldQuery 顺序写入独立预测文件，并复用既有指标、代理分和报告写入器生成独立报告。评分输出目录必须尚不存在，临时目录内全部实验成功后才原子发布。该命令不读取 `.env`、不加载 BGE-M3/Cross Encoder、不调用 DeepSeek 或学术 API；修改 `evaluation_top_k`、代理分配置或报告格式只需重新运行评分，不得重建候选快照。

---

## 29. Cross Encoder 执行与零命中候选覆盖诊断实施状态（2026-07-19）

离线执行器现已支持标准 A/B/C/D 矩阵的 Cross Encoder 阶段：

- 新增独立 `CrossEncoderOfflineScorer`，只接受用户已下载、含 `config.json` 的本地目录；构造和空候选不导入或加载模型，非空评分才延迟调用本地 `FlagReranker`；
- C（RRF + Cross Encoder）直接读取同一份排序前候选快照，D（RRF + BGE-M3 + Cross Encoder）只读取该快照中的 BGE-M3 保留候选；二者均不重新调用学术 API；
- `ablation-execute` 选择 C/D 时必须同时显式提供 `--allow-local-models` 与 `--cross-encoder-model-path`；D 另需 BGE-M3 目录，缺少任一评分器时在写出输出前失败；
- 修正 CLI 参数说明和离线测试，使其与当前 A/B/C/D 实现一致；DeepSeek 仍不在第一轮矩阵内。

针对首次 A/B/C/D 评分全部为零检索命中的情况，新增 `python -m evaluation coverage-diagnose`：

- 它只读取已封存 GoldQuery JSONL 与排序前 `CandidateSnapshot` JSONL：默认要求两侧 `query_id` 集合完全相同；对少量 Query Agent 新快照可显式重复提供 `--query-id`，仅要求这些选中 ID 在两侧均存在，禁止把新快照复制进旧集合伪造同一 QueryIntent 来源；
- 输出目录必须不存在，发布 `diagnostic.json`、`query_diagnostics.jsonl` 与 `diagnostic.md`；`coverage-diagnostic-v2` 冻结两个输入的原始字节 SHA-256 和实际 `selected_query_ids`，并按查询记录金标数、候选数、强标识符数量、匹配数量和事实性标记；输出不复制查询正文或论文正文，局部诊断不能外推为开发集结论；
- 该诊断不读取 `.env`，不重跑 BGE-M3/Cross Encoder，不调用 DeepSeek、学术 API 或本地模型；它不对零命中归因，只用于先区分“现有身份规则下无覆盖”和“标识符可比性不足”等可观测事实；
- 只有诊断结果确认需要改变来源查询、来源召回规模、QueryIntent 或多轮策略时，才由用户决定是否显式重新生成在线候选；只修复离线身份映射、评分或报告时仍必须复用现有快照。

---

## 30. 受控 Query Agent 查询策略实验实施状态（2026-07-19）

针对候选覆盖诊断已确认“现有第一轮来源召回没有覆盖金标”的情况，新增 `python -m evaluation query-agent-plan`。该入口是重新生成候选前的独立查询策略实验，不属于同一候选快照上的 A/B/C/D 离线排序消融：

- 用户必须显式提供 `--allow-query-agent`、现有 `query-intent-manifest-v1`、少量 `--query-id`、全新的输出目录和审计 manifest；缺少授权开关时连输入 manifest 都不读取；
- 输入契约只允许 QueryIntent 文件映射。运行器只将源 QueryIntent 的 `original_query`、搜索模式和已显式设置的条件传给生产 Query Agent，严格不读取 GoldQuery、Gold 标题、作者、arXiv ID、候选快照、诊断或评分报告；
- 每个选择的查询仅调用一次 Query Agent，输出仍强制为第一轮，继承原 `source_recall_count` 与 `target_paper_count`，关闭网页发现、BGE-M3、Cross Encoder 和子查询；Query Agent 不得擅自改变候选规模或开启后续排序；
- 输出 manifest 冻结输入 manifest SHA-256、稳定查询顺序、QueryIntent 文件映射、每条输出哈希、模型名、Token、费用和耗时；本阶段固定记录学术 API=0、本地模型=0；
- 生成后的 QueryIntent 必须先由用户审阅，再由用户对新文件显式运行 `snapshot-export --allow-online-sources`。新快照和报告是新的查询策略实验产物，不得与现有共享快照上的 A/B/C/D 离线结果混合；只调整指标、Top-K、离线排序或报告时不得调用 Query Agent。

## 31. 真实调用前资源预估实施状态（2026-07-19）

新增 `python -m evaluation usage-forecast`，在 Query Agent 或单轮候选快照调用前只读冻结调用范围、输入 SHA-256、DeepSeek Token/费用上限或学术 API 次数上限及确认 SHA-256。该命令不读取 `.env`、不访问网络、不创建 DeepSeek 或学术来源客户端；来源 RPS、429 冷却、重试和超时仍是供应商保护，不属于可删除的策略调用限制。

`query-agent-plan` 和 `snapshot-export` 现强制接收 `--forecast` 与 `--confirm-forecast`。执行前重新计算预估内容哈希，并核验输入 SHA-256、稳定 query_id 顺序以及快照标识；不匹配时不得装配任何真实客户端。

## 32. DeepSeek 评测核验契约演进（2026-07-19）

离线消融契约已允许 `deepseek_enabled=true`，并新增 `deepseek` 阶段轨迹枚举。真实执行器尚未接入前，该开关不会触发调用；后续必须通过独立适配器、调用前预估确认和单独结果归档实现，禁止只修改矩阵 JSON 伪造已执行的 DeepSeek 实验。

## 33. DeepSeek RRF 核验实验实施状态（2026-07-19）

新增独立 E 矩阵 `evaluation/config/ablation_deepseek_rrf.json`，只比较 RRF 上游排序后的 DeepSeek 核验，固定 `source_recall_count=50`、`target_paper_count=20` 与 `evaluation_top_k=[5,10,20]`。E 不与 A/B/C/D 本地排序结果混合；执行前必须对封存候选、矩阵、计划和实验 E 生成 `ablation-deepseek` 预估，并在 `ablation-execute` 中提供 `--allow-deepseek`、预估路径及确认 SHA-256。实际结果仅在全部任务成功后原子归档，记录模型、降级、预估哈希以及真实小批次调用数；失败批次也计入调用数，而 Token 与费用只累计供应商成功返回的 usage，学术 API 恒为 0。
