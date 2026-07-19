# ScholarFlow 离线评测模块

本目录是与 `backend/` 生产搜索流程分离的评测模块。第一阶段提供指标与报告，第二阶段提供排序前候选快照和 A/B/C/D 离线消融编排，第三阶段提供唯一的受控在线候选导出入口，第四阶段提供本地准备数据集金标导入。`fixture`、`snapshot-check`、`ablation-plan`、`dataset-gold-import` 和 `gold-subset-select` 始终只读取用户显式提供的本地 JSONL/JSON 文件，不读取 `.env`，不访问学术 API、LLM 或本地模型。只有用户手动执行带 `--allow-online-sources` 的 `snapshot-export` 时，才会延迟装配生产学术来源；模块不提供数据集或模型下载命令。

## 第一阶段能力

- `contracts/`：金标、预测、排序数量、usage 和报告的 Pydantic 契约；
- `metrics/`：DOI、arXiv、PMID、OpenAlex、Semantic Scholar、DBLP 标识规范化，保守匹配与去重；
- `metrics/`：Precision、Recall、F1、Micro/Macro、MRR 和二元 nDCG；
- `metrics/`：原始效率汇总、缺失值保持和确定性结构代理分；
- `reports/`：UTF-8 JSON、JSONL 与 Markdown 报告；
- `fixtures/`：不代表真实成绩的纯合成验收数据；
- `tests/`：完全离线的契约、指标与报告测试。

## 第二阶段能力

- `contracts/snapshot.py`：规范化、去重、RRF、确定性规则过滤后且 BGE-M3 前的候选快照；
- `runners/snapshot_loader.py`：只读 JSONL 加载、SHA-256、重复身份和阶段边界校验；
- `contracts/ablation.py`：共享在线召回规模和评分口径的 A/B/C/D 矩阵；
- `adapters/base.py`：不绑定模型库、不会自动加载模型的离线打分协议；
- `runners/offline_ranking.py`：从同一快照深拷贝开始的 BGE-M3/Cross Encoder 消融编排；
- `config/ablation_default.json`：DeepSeek 全部关闭的标准第一轮矩阵；
- `fixtures/candidate_snapshots.jsonl`：已封存且不代表真实结果的纯合成快照。

候选快照契约版本为 `1.1`，`snapshot_stage` 固定为 `pre_semantic_ranking`，对应生产链路中确定性规则过滤完成后、BGE-M3 调用前的候选集合。`normalized_candidate_count` 表示成功映射为统一论文记录的数量，`deduplicated_candidate_count` 表示身份去重和 RRF 后、规则过滤前的数量，`filtered_candidate_count` 表示规则过滤移除数量，`ranking_candidate_count` 表示实际进入离线排序的数量。四者不得互相替代；只有确实观测到供应商原始响应条目数时才填写 `raw_candidate_count`，否则保持 `null`。

加载器要求 `snapshot_hash` 与规范化内容一致，并拒绝重复 `snapshot_id`、重复 `query_id`、重复论文、断裂排名、非确定性 RRF 顺序、来源计数漂移、过滤计数漂移和未声明来源。快照论文按 `rrf_score` 降序、`paper_id` 升序封存。现有 SQLite 中保存的是生产排序后的最终结果，不能直接作为此处排序前候选快照。

`source_recall_count`、`semantic_top_k`、`cross_encoder_top_k`、`target_paper_count` 与 `evaluation_top_k` 是五个不同概念。前四者描述候选生成或排序流水线，`evaluation_top_k` 只控制对既有预测列表的评分截断，改变它不会生成候选或调用 API。

## 第三阶段能力

- `adapters/scholarflow_snapshot.py`：将生产 `CandidateGenerationResult` 映射为 `CandidateSnapshot`，不修改生产 API；
- `runners/snapshot_export.py`：在来源调用前校验单轮、零网页、零本地模型和新输出路径，并将一份快照原子写成单条 JSONL；
- `snapshot-export`：唯一可能读取生产配置并调用真实学术来源的 CLI，必须显式提供 `--allow-online-sources`；
- 导出器只复用 `CandidateGenerationService` 的来源路由、规范化、身份融合/RRF 和确定性规则过滤，不创建 `SearchRunState`，不进入 BGE-M3、Cross Encoder、DeepSeek、覆盖分析或多轮搜索；
- 导出结果将逻辑学术来源调用数、缓存命中和候选阶段耗时写入 `usage`；当前无法可靠观测的实际 HTTP 请求、重试和限流次数保持 `null`，LLM 调用和 Token 明确为零。

输入 `QueryIntent` 必须由用户提前准备，并满足：`retrieval_round=1`、显式设置 `source_recall_count`、`requires_web_evidence=false`、`enable_semantic_ranking=false`、`enable_cross_encoder_ranking=false`。输出路径必须尚不存在，避免在线生成后覆盖已有快照。候选服务返回网页来源或网页发现项时，导出也会失败，不会把它们伪装为论文候选。

## 第四阶段能力

- `contracts/dataset.py`：定义 `prepared-dataset-gold-v1` 的严格本地输入契约；
- `adapters/prepared_dataset.py`：为 `dataset_id:split:source_query_id` 分配稳定命名空间，并冻结来源、切分与转换版本元数据；
- `runners/dataset_import.py`：只读转换、统一身份去重校验和拒绝覆盖的原子 JSONL 写入；
- `dataset-gold-import`：将用户手动准备的公开数据集查询金标转换为现有 `GoldQuery` JSONL，供 `fixture` 评分器直接使用。

该阶段刻意不猜测 PaSa、RealScholarQuery 或其他公开数据集的原始字段格式与版本。用户先在本地将已下载且有权使用的数据整理为每行一条 `prepared-dataset-gold-v1` 记录：

```json
{"source_query_id":"pasa-dev-001","query":"example academic query","relevant_papers":[{"doi":"10.1000/example","title":"Example Paper","year":2024,"authors":["Author A"]}],"metadata":{"source_version":"user-confirmed"}}
```

`metadata` 只允许 JSON 标量值，且不得包含 `dataset`、`split`、`source_query_id` 或 `import_schema_version`；这些字段由导入器统一写入。导入器拒绝重复 `source_query_id` 和按 ScholarFlow 统一身份规则重复的相关论文，不会补全缺失 DOI、标题或作者，更不会访问外部来源验证论文。

## 第五阶段能力

- `contracts/pasa.py`：严格解析经本地文件样例确认的 `AutoScholarQuery/dev.jsonl` 字段：`qid`、`question`、`answer`、`answer_arxiv_id`、`source_meta`；
- `adapters/pasa.py`：按索引配对标题和 arXiv ID，先按统一身份规则保留 PaSa 重复标注的首次论文并记录 `pasa_duplicate_answer_count`，再复用通用命名空间规则；
- `runners/pasa_import.py`：只读转换已下载的 PaSa 开发集，原子写入新的 `GoldQuery` JSONL；
- `pasa-gold-import`：当前只接受 `--split auto-dev`，避免未确认 RealScholarQuery 原始字段时进行猜测性解析。

转换命令完全离线，不读取 `.env`，不访问论文 API、LLM 或本地模型：

```powershell
python -m evaluation pasa-gold-import `
  --input data/evaluation/pasa/AutoScholarQuery/dev.jsonl `
  --split auto-dev `
  --output evaluation/inputs/pasa-auto-dev.gold.jsonl
```

`answer` 与 `answer_arxiv_id` 非空时必须等长；适配器不会通过 `paper_database/id2paper.json` 补全或猜测缺失字段。RealScholarQuery/test.jsonl 的原始结构尚未在本地确认，后续须先检查样例再新增独立适配器。

## 第六阶段能力

- `contracts/subset.py`：定义 `gold-subset-manifest-v1`，冻结选择策略、显式种子、输入/输出哈希和完整 `query_id` 列表；
- `runners/gold_subset.py`：以 `sha256-query-id-v1` 对完整本地 GoldQuery 进行与行顺序无关的稳定排序，不访问任何服务；
- `gold-subset-select`：要求显式 `--count`、`--selection-id`、`--seed`、新 GoldQuery 输出和新 manifest 输出，拒绝覆盖其中任一文件。

开发集评测的 20 条查询必须从完整 PaSa 开发集显式封存，而不是把原始 dev 文件误写成仅有 20 条。以下命令只创建本地子集与 manifest，不生成候选、不读取 `.env`、不调用学术 API、LLM 或本地模型：

```powershell
python -m evaluation gold-subset-select `
  --input evaluation/inputs/pasa-auto-dev.gold.jsonl `
  --count 20 `
  --selection-id pasa-auto-dev-ranking-v1 `
  --seed 20260719 `
  --output evaluation/inputs/pasa-auto-dev-ranking20.gold.jsonl `
  --manifest evaluation/inputs/pasa-auto-dev-ranking20.manifest.json
```

`selection_id`、种子和 manifest 的 `selected_query_ids` 必须随候选快照、排序配置和报告一同保存。改变 `count`、种子或选择标识会生成新的开发集输入，后续如需线上候选只能由用户显式重新授权；仅调整 `evaluation_top_k`、离线评分或报告不得重新调用学术 API。

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

只读校验合成候选快照：

```powershell
python -m evaluation snapshot-check `
  --snapshots evaluation/fixtures/candidate_snapshots.jsonl
```

生成 A/B/C/D 任务计划但不执行模型：

```powershell
python -m evaluation ablation-plan `
  --snapshots evaluation/fixtures/candidate_snapshots.jsonl `
  --matrix evaluation/config/ablation_default.json `
  --output evaluation/results/ablation-plan.json
```

将用户本地准备的数据集金标转换为评分器可读取的 `GoldQuery`：

```powershell
python -m evaluation dataset-gold-import `
  --input evaluation/fixtures/prepared_dataset_gold.jsonl `
  --dataset synthetic-public `
  --split dev `
  --output evaluation/results/synthetic-public-dev.gold.jsonl
```

真实 PaSa 或其他公开数据应由用户手动下载并保留在 `data/evaluation/` 的受 Git 忽略目录；先按上面的准备格式导出一个本地 JSONL，再执行导入命令。Codex 不下载、读取或提交这些真实数据。导入结果可直接替换 `fixture --gold` 的金标输入。

PaSa 是 gated 数据集。用户在 Hugging Face 页面接受 `CarlanLark/pasa-dataset` 条款并完成本机 `hf auth login` 后，可手动运行选择性下载脚本；脚本不保存或输出 Token，默认只下载 `AutoScholarQuery/dev.jsonl`：

```powershell
python scripts/download_pasa_dataset.py
```

按需增加 RealScholarQuery 测试集和论文 ID 映射，或冻结特定 revision：

```powershell
python scripts/download_pasa_dataset.py `
  --subset auto `
  --subset real `
  --subset paper-database `
  --revision main
```

脚本只向 `snapshot_download` 传递这三个预定义相对路径的 `allow_patterns`，不会下载完整仓库；`--force` 才会要求重新下载。默认输出目录是受 Git 忽略的 `data/evaluation/pasa/`，完成后会显示每个文件的绝对路径和大小。

按需生成真实排序前候选快照时，由用户检查 `QueryIntent`、API 配置和输出路径后手动执行：

```powershell
python -m evaluation snapshot-export `
  --query-intent evaluation/inputs/query-intent-q001.json `
  --query-id q-001 `
  --snapshot-id q-001-openalex-20260719 `
  --output evaluation/snapshots/q-001-openalex-20260719.jsonl `
  --allow-online-sources
```

该命令是上述离线命令的唯一在线例外，可能读取 `.env` 中的来源配置并调用真实学术 API。它不会调用 Query Agent、LLM 或本地排序模型，也不会下载模型或数据集。Codex 不自动执行该命令；真实运行及生成文件的内容审阅由用户负责。

对于已封存的 PaSa 20 条 QueryIntent，可由用户手动调用批处理脚本；默认每批只导出下一条尚无有效快照的查询，并在写入后立即运行完全离线的 `snapshot-check`。脚本会跳过已有、无“学术来源降级”警告且校验通过的快照，失败立即停止，不会自动递归执行完整开发集：

```powershell
.\scripts\export_pasa_snapshot_batch.ps1 -BatchSize 1
```

该脚本仅编排已有 `snapshot-export`，仍会显式传递 `--allow-online-sources`；因此必须由用户在正确的项目环境中手动运行。不要直接运行旧的 `evaluation/inputs/*-snapshot-export-commands.ps1` 命令清单，它会顺序执行全部命令且不具备已验证快照跳过与逐条复核边界。

当 20 条单查询快照均已完成后，先离线组装为唯一共享候选集合，再生成 A/B/C/D 计划。组装器会按 QueryIntent manifest 的稳定顺序校验每份 SHA-256、`source_recall_count`、`target_paper_count` 和来源降级警告；多个成功重试必须由 `--snapshot-override` 明确选择。以下命令固定第 001 条使用最终验证的 retry7，其余查询自动选择唯一有效快照：

```powershell
python -m evaluation snapshot-collection-assemble `
  --collection-id pasa-auto-dev-ranking-v1 `
  --query-intent-manifest evaluation/inputs/pasa-auto-dev-ranking20-query-intents.manifest.json `
  --snapshot-dir evaluation/inputs/pasa-auto-dev-ranking20-snapshots `
  --snapshot-override "pasa:auto-dev:AutoScholarQuery_dev_806=001_AutoScholarQuery_dev_806.retry7.snapshot.jsonl" `
  --output evaluation/inputs/pasa-auto-dev-ranking20.candidate-snapshots.jsonl `
  --manifest evaluation/inputs/pasa-auto-dev-ranking20.candidate-snapshots.manifest.json
```

该命令只读取本地文件并写出新的 JSONL / manifest，不读取 `.env`、不调用学术 API、LLM 或本地模型；两个输出都不得预先存在。历史带“学术来源降级”警告的零候选失败产物会被排除，而候选数量少于 `target_paper_count` 的真实成功快照仍被保留并写入集合 manifest。

任务计划固定显示新增学术 API 调用为零、DeepSeek 调用为零。`evaluation.adapters.bge_m3.BgeM3OfflineScorer` 与 `evaluation.adapters.cross_encoder.CrossEncoderOfflineScorer` 分别实现为可注入的 BGE-M3、Cross Encoder 适配器：两者只接受用户明确提供、已存在且含 `config.json` 的本地模型目录，不接受远程仓库名；构造及空候选评分不加载模型，首次非空 `score` 才延迟导入并加载本地模型。调用方必须显式创建评分器或通过 CLI 提供对应本地目录，绝不回退加载生产模型。独立 E DeepSeek 对比只读取封存快照，并在执行前预估、确认后才允许调用。

执行已审核的计划内 A/B/C/D 子集时，用户必须显式运行 `ablation-execute`。该命令会复核集合快照、矩阵与 `ablation-plan` 的快照 ID/SHA-256 对应关系；输出 JSONL 和 manifest 都必须尚不存在，并通过同目录临时文件原子发布。B/D 需要 BGE-M3，本地 Cross Encoder 则用于 C/D：

```powershell
python -m evaluation ablation-execute `
  --run-id pasa-auto-dev-ranking-v1-bge-v1 `
  --snapshots evaluation/inputs/pasa-auto-dev-ranking20.candidate-snapshots.jsonl `
  --matrix evaluation/config/ablation_default.json `
  --plan evaluation/results/pasa-auto-dev-ranking20-ablation-plan.json `
  --experiment A `
  --experiment B `
  --bge-model-path D:\models\bge-m3 `
  --bge-device cpu `
  --bge-batch-size 8 `
  --allow-local-models `
  --output evaluation/results/pasa-auto-dev-ranking20-ab.results.jsonl `
  --manifest evaluation/results/pasa-auto-dev-ranking20-ab.manifest.json
```

该命令只在用户手动执行时加载本地模型；不会读取 `.env`、调用学术 API 或 DeepSeek。C/D 必须显式传入 `--cross-encoder-model-path`，D 同时需要 BGE-M3 路径；四组均复用同一份排序前快照。

执行完成后，使用 `ablation-score` 对已有 A/B 结果评分；该命令只读取本地归档和金标，不加载模型：

```powershell
python -m evaluation ablation-score `
  --results evaluation/results/pasa-auto-dev-ranking20-ab.results.jsonl `
  --run-manifest evaluation/results/pasa-auto-dev-ranking20-ab.manifest.json `
  --gold evaluation/inputs/pasa-auto-dev-ranking20.gold.jsonl `
  --config evaluation/config/default.json `
  --output-dir evaluation/results/pasa-auto-dev-ranking20-ab-score
```

输出目录被 Git 忽略，包含：

- `report.json`：完整机器可读汇总与查询明细；
- `query_metrics.jsonl`：每条金标查询一行指标；
- `report.md`：人工审阅报告。

效率分、结构分和综合分始终标记为“本地代理分（非官方）”。只要代理分所需效率观测不完整，效率代理分和综合代理分就保持缺失，避免用零伪造观测。

如果同一共享候选快照上的 A/B/C/D 均为零检索命中，先运行只读覆盖诊断；它不会重跑模型或候选生成：

```powershell
python -m evaluation coverage-diagnose `
  --gold evaluation/inputs/pasa-auto-dev-ranking20.gold.jsonl `
  --snapshots evaluation/inputs/pasa-auto-dev-ranking20.candidate-snapshots.jsonl `
  --output-dir evaluation/results/pasa-auto-dev-ranking20-coverage-diagnostic
```

输出的 `diagnostic.json` 冻结金标和候选集合 SHA-256，`query_diagnostics.jsonl` 按查询记录强标识符可比性、匹配数和事实性标记，`diagnostic.md` 供人工摘要阅读。它严格复用 `papers_match-v1`，不输出查询正文或论文正文；零命中不能单独归因于来源、查询、规范化或排序。

若覆盖诊断表明需要补足隐含术语，用户可单独、一次性选择少量查询运行 Query Agent。此命令不是第一轮 A/B/C/D 排序消融的一部分：它会产生新的检索表达式和后续新的候选快照，不能与旧快照上的离线报告混合。

```powershell
python -m evaluation query-agent-plan `
  --input-manifest evaluation/inputs/pasa-auto-dev-ranking20-query-intents.manifest.json `
  --query-id pasa:auto-dev:AutoScholarQuery_dev_806 `
  --output-dir evaluation/inputs/pasa-auto-dev-ranking20-query-agent-v3 `
  --manifest evaluation/inputs/pasa-auto-dev-ranking20-query-agent-v3.manifest.json `
  --allow-query-agent
```

该命令会调用一次真实 Query Agent LLM，并记录实际 Token、费用和耗时；它只读取输入 manifest 映射的 QueryIntent 的 `original_query`、搜索模式和已显式条件，严格不读取 GoldQuery、Gold 标题、作者、arXiv ID、候选快照或报告，不调用学术 API 或本地模型。输出目录和 manifest 均必须尚不存在。生成后先人工审阅新的 QueryIntent，再由用户对其显式运行 `snapshot-export --allow-online-sources`；只调整离线排序、Top-K、指标或报告时不得使用本命令。

## 后续边界

在用户执行 `query-agent-plan` 或 `snapshot-export` 前，先运行 `usage-forecast`。它不会调用 DeepSeek 或学术 API：Query Agent 预估按源 QueryIntent 的原始问题和每次 3,000 输出 Token 上限计算保守 Token/费用上限；快照预估固定记录第一轮一个逻辑学术来源调用及默认三次重试下最多四次 HTTP 尝试。预估 JSON 含确认 SHA-256，且不写查询正文、Gold 或密钥。

新的 `query-agent-plan` 与 `snapshot-export` 必须同时提供 `--forecast <预估 JSON>` 和 `--confirm-forecast <confirmation_sha256>`；只要输入文件、查询顺序或快照标识变更，就必须重新预估。确认不匹配时命令在创建 DeepSeek 或学术来源客户端前失败。

DeepSeek 核验使用独立 `config/ablation_deepseek_rrf.json` 的 E 实验，不与 A/B/C/D 本地排序报告混合。先对封存候选集合生成 `ablation-deepseek` 预估；再以同一预估文件的 `confirmation_sha256` 执行 `ablation-execute --allow-deepseek`。DeepSeek 仅读取快照与其冻结 QueryIntent，不重新调用学术 API。结果 manifest 的 `deepseek_calls` 和每条预测 usage 的 `llm_calls` 都按实际尝试的小批次计，失败批次也会记录；Token 与费用只累计供应商成功返回的 usage。

当前模块已包含由用户显式执行的单轮生产候选快照导出、PaSa 选择性下载脚本、通用准备金标导入，以及已确认 `AutoScholarQuery/dev.jsonl` 字段的原生 PaSa 开发集转换；还包含不自动下载的 BGE-M3、Cross Encoder 评分适配器、零命中覆盖诊断、受控 Query Agent 规划与独立 E DeepSeek 排序对比，但没有 RealScholarQuery 原生解析器。后续排序消融必须只读取已封存快照；改变 BGE-M3/Cross Encoder 保留数量、`evaluation_top_k`、指标或报告不得再次调用学术 API。若首轮均零命中，应先审阅覆盖诊断，再决定是否需要用户显式运行少量 Query Agent 查询策略实验并重建对应候选；数据下载和完整 benchmark 仍由用户显式执行。
