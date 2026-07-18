# ScholarFlow 离线评测模块

本目录是与 `backend/` 生产搜索完全隔离的第一阶段评测模块。它只读取用户显式提供的本地 JSONL/JSON 文件，计算检索、排序、效率与结构指标，并写出本地报告；不会读取 `.env`，不会访问学术 API、LLM 或本地模型，也不提供数据集或模型下载命令。

## 第一阶段能力

- `contracts/`：金标、预测、排序数量、usage 和报告的 Pydantic 契约；
- `metrics/`：DOI、arXiv、PMID、OpenAlex、Semantic Scholar、DBLP 标识规范化，保守匹配与去重；
- `metrics/`：Precision、Recall、F1、Micro/Macro、MRR 和二元 nDCG；
- `metrics/`：原始效率汇总、缺失值保持和确定性结构代理分；
- `reports/`：UTF-8 JSON、JSONL 与 Markdown 报告；
- `fixtures/`：不代表真实成绩的纯合成验收数据；
- `tests/`：完全离线的契约、指标与报告测试。

`source_recall_count`、`semantic_top_k`、`cross_encoder_top_k`、`target_paper_count` 与 `evaluation_top_k` 是五个不同概念。前四者描述候选生成或排序流水线，`evaluation_top_k` 只控制对既有预测列表的评分截断，改变它不会生成候选或调用 API。

## 输入文件

金标 JSONL 每行符合 `GoldQuery`：

```json
{"query_id":"q-001","query":"example query","relevant_papers":[{"doi":"10.1000/example","title":"Example"}]}
```

预测 JSONL 每行符合 `PredictionRecord`。`papers` 必须按预测顺序保存；`usage` 缺失字段保持 `null`，不会按零补齐。`ranking_config` 明确记录各候选数量与模型开关，但不触发任何模型执行。

合成输入位于 `evaluation/fixtures/`，默认代理阈值位于 `evaluation/config/default.json`。效率代理分按“每查询学术 API 调用数、每查询 Token 数、P95 耗时”计算，以免查询数量直接改变评分尺度；这些阈值只用于同一数据集上的本地比较，不是赛题官方公式。

## 用户手动运行

前置条件：使用已安装根目录 `requirements-dev.txt` 的当前项目解释器。先执行测试：

```powershell
pytest evaluation/tests -q
```

再按需运行纯合成 fixture：

```powershell
python -m evaluation fixture `
  --gold evaluation/fixtures/gold.jsonl `
  --predictions evaluation/fixtures/predictions.jsonl `
  --config evaluation/config/default.json `
  --output-dir evaluation/results/fixture
```

输出目录被 Git 忽略，包含：

- `report.json`：完整机器可读汇总与查询明细；
- `query_metrics.jsonl`：每条金标查询一行指标；
- `report.md`：人工审阅报告。

效率分、结构分和综合分始终标记为“本地代理分（非官方）”。只要代理分所需效率观测不完整，效率代理分和综合代理分就保持缺失，避免用零伪造观测。

## 后续边界

第一阶段不包含候选快照采集、公开数据集适配、BGE-M3/Cross Encoder 推理或 DeepSeek 对比。下一阶段应先定义规范化、去重后的候选快照契约；在线搜索只负责生成一次快照，本地排序消融、Top-K、指标和报告调整复用该快照。
