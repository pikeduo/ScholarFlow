# ScholarFlow 离线评测模块

本模块用于以已封存输入评估检索、排序和端到端结果。默认完全离线；真实学术来源、LLM、本地模型和数据下载均由用户显式授权并手动执行。

## 核心原则

- 在线阶段只生成排序前候选快照；A/B/C/D 排序消融、评分和报告必须复用同一快照。
- 明确区分来源召回量、排序保留量、最终返回量与评分 Top-K；修改指标、Top-K、报告或本地排序开关不得重调学术 API。
- 金标和预测按 DOI、arXiv（去版本）、PMID、来源 ID 依次匹配；强标识冲突不回退标题。PaSa 稀疏 Gold 标题审计与通用指标分开报告。
- 输出不得覆盖已有归档；执行和评分结果以 JSONL、manifest 与 SHA-256 保存。缺失观测保持 `null` 或 `N/A`，不以零代替。

## 命令分层

| 范围 | 命令 | 是否访问外部资源 |
| --- | --- | --- |
| 离线验证 | `fixture`、`snapshot-check`、`coverage-diagnose` | 否 |
| 本地准备 | `dataset-gold-import`、`pasa-gold-import`、`gold-subset-select`、`snapshot-collection-assemble` | 否 |
| 离线排序 | `ablation-plan`、`ablation-execute`、`ablation-score` | 仅在显式授权时加载本地模型 |
| 受控在线 | `query-agent-plan`、`snapshot-export`、`pasa-end-to-end-execute` | 是，须显式授权 |
| 调用前预估 | `usage-forecast` | 否 |

运行任一命令前可查看参数：

```powershell
python -m evaluation <command> --help
```

## 快速离线验证

前置条件：当前项目解释器已安装根目录 `requirements-dev.txt`。

```powershell
pytest evaluation/tests -q

python -m evaluation fixture `
  --gold evaluation/fixtures/gold.jsonl `
  --predictions evaluation/fixtures/predictions.jsonl `
  --config evaluation/config/default.json `
  --output-dir evaluation/results/fixture

python -m evaluation snapshot-check `
  --snapshots evaluation/fixtures/candidate_snapshots.jsonl

python -m evaluation ablation-plan `
  --snapshots evaluation/fixtures/candidate_snapshots.jsonl `
  --matrix evaluation/config/ablation_default.json `
  --output evaluation/results/ablation-plan.json
```

## 推荐工作流

1. 将用户已确认的本地金标导入为 `GoldQuery`；PaSa 原生导入当前只支持已确认字段的 `AutoScholarQuery/dev.jsonl`。
2. 使用 `gold-subset-select` 封存开发集子集及 manifest；不得重抽样或替换零命中查询。
3. 用户先以 `usage-forecast` 审阅调用上限，再以 `snapshot-export --allow-online-sources` 逐条生成并校验候选快照。该入口不调用 Query Agent、DeepSeek 或本地模型。
4. 使用 `snapshot-collection-assemble` 将唯一有效的单查询快照组装为共享集合；有多个成功重试时，必须使用 `--snapshot-override` 选择。
5. 对共享集合运行 `ablation-plan`，再按需运行 `ablation-execute` 和 `ablation-score`。B/D 需要 BGE-M3，C/D 需要 Cross Encoder；模型目录必须已存在且包含 `config.json`，并提供 `--allow-local-models`。
6. 若 A/B/C/D 全部零命中，先运行 `coverage-diagnose`。只有确认需要新检索表达式时，才可对少量查询执行 `query-agent-plan`，并将新快照视为独立实验。

## 受控调用边界

- `query-agent-plan`、`snapshot-export` 和 DeepSeek 消融都必须先运行 `usage-forecast`，并提供预估文件与 `confirmation_sha256`。
- `snapshot-export` 只允许一轮候选生成；`--allow-online-sources` 是唯一来源调用授权。
- DeepSeek 消融使用独立 E 实验，不能与 A/B/C/D 本地排序报告混合；调用数按尝试的小批次计，Token 与费用只累计成功响应的 usage。
- `pasa-end-to-end-execute --allow-online-end-to-end` 会调用用户已启动的后端、真实学术来源和生产 LLM。固定 PaSa 20 条流程必须先计划、后执行、再离线评分。

## 数据与结果

- 真实数据放在受 Git 忽略的 `data/evaluation/`；不要提交数据、模型、密钥、运行日志或评测结果。
- PaSa 下载需用户自行接受数据条款并完成本机认证；可手动运行 `python scripts/download_pasa_dataset.py`，默认仅下载 `AutoScholarQuery/dev.jsonl`。
- LongEval 2025 CORE Sci-Retrieval 下载也只由用户手动执行。`python scripts/download_longeval_dataset.py --allow-download --split train` 仅下载官方 abstract 训练包；`--split test` 下载测试 abstract 包，`--split test-qrels` 下载单独发布的测试 qrels 包，`--split all` 获取三者。脚本校验官方 MD5，默认不下载 20 GiB 以上 fulltext。已下载 ZIP 可通过 `python scripts/download_longeval_dataset.py --extract-only --split all` 在零网络条件下安全解压到 `data/evaluation/longeval_2025/raw/extracted/<split>/`。
- LongEval 下载和解压完成后，运行 `python -m evaluation longeval-audit` 扫描全部本地 Train、Held-out、Future 的 queries、qrels 与 documents，并写出 DOI 覆盖、`excluded_no_doi_gold` 和逐 query eligibility 审计；该命令不调用学术 API、LLM 或本地模型。审计完成后运行 `python -m evaluation longeval-gold-import`：它会重新核验 raw SHA-256，生成每个 split 的 DOI-strict GoldQuery、正相关 evidence、排除/冲突 ledger 与 manifest；任何重复 document ID 的 DOI 冲突均不会进入 Gold。
- 对已有 LongEval Gold 和本地 `PredictionRecord` 运行 `python -m evaluation doi-track-score --gold <gold.jsonl> --predictions <predictions.jsonl> --output-dir <new-report-dir>`。该命令严格只匹配有效 DOI，缺 DOI、非法 DOI 与重复 DOI 都不会命中或扩大 Precision 分母；不加载模型，也不会重调来源。
- 评测输出是本地代理分，不等同于赛事官方成绩。效率、结构化与可观测性字段不足时必须明确标记缺失。

详细的评测口径与阶段计划见 `docs/ScholarFlow_评测与测试规划.md`。
