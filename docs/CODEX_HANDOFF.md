# ScholarWeave 阶段交接记录

记录日期：2026-07-11  
记录依据：当前 `master` 分支代码、`git log`、`git diff`、工作区状态与本次对话中的实际操作。

## 1. 当前项目目标与本阶段目标

项目目标是构建 ScholarWeave（研索）：面向复杂科研问题的多源智能论文搜索与推荐系统，主链路为“复杂查询 → 结构化意图 → 多源检索 → 规范化与去重 → 排序与核验 → 可解释结果”。

本阶段目标是完成多源检索的工程基础：统一领域模型、来源适配器、动态来源路由和多源召回协调，不进入复杂排序、图谱、LLM 编排或持久化实现。

## 2. 已完成的工作

- 基础工程已具备 FastAPI、SQLite 初始化、统一控制台与滚动文件日志、UTF-8 配置、`requirements.txt`、`requirements-dev.txt` 和根目录 pytest 配置。
- 已冻结并测试核心契约：`QueryIntent`、`PaperRecord`、`SearchRunState`、`SearchResult`。
- `QueryIntent` 已支持领域标签 `domains` 和显式网页证据开关 `requires_web_evidence`。
- 已实现统一学术来源协议 `AcademicSearchAdapter`，并实现 OpenAlex、Semantic Scholar、arXiv、DBLP 搜索适配器。
- 已实现 `WebDiscoveryAdapter` 与 Tavily 补充网页发现适配器；Tavily 返回 `SupplementalDiscoveryItem`，并固定标记为不可合并论文。
- 已实现 `SourceRouter`：OpenAlex 为主源；AI/计算机领域附加 arXiv、DBLP；Tavily 仅在 `requires_web_evidence=True` 且 Key 可用时选择；Semantic Scholar 仅在 Key 已配置且 `semantic_scholar_enabled=True` 时选择。
- Semantic Scholar 已在配置模板中启用，来源级限流为每秒最多一次。
- 已实现 `MultiSourceRecallCoordinator`：按 `SourceRoutePlan` 并发召回学术来源与网页补充来源，隔离单源失败，返回来源级数量和已净化错误摘要。

## 3. 当前采用的关键技术决策及原因

| 决策 | 当前做法 | 原因 |
|---|---|---|
| 论文来源协议 | `AcademicSearchAdapter.search(QueryIntent) -> list[PaperRecord]` | 让编排和业务层不依赖供应商字段。 |
| 网页补充来源 | Tavily 使用独立 `WebDiscoveryAdapter.discover` | 网页搜索结果缺少可靠论文 ID、引用和书目字段，不能伪装成论文。 |
| 主源与动态来源 | OpenAlex 为主；Semantic Scholar 为已启用核心补充；AI/计算机领域再选 arXiv、DBLP | 避免无条件调用所有外部 API。 |
| Semantic Scholar | `SCHOLARFLOW_SEMANTIC_SCHOLAR_ENABLED=true` 且 `SCHOLARFLOW_SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND=1` | Key 已获批；按用户提供的官方每秒一次限制执行。 |
| 故障策略 | 召回协调器捕获单源异常，保留其他来源结果并记录安全摘要 | 单一外部来源不可用不应阻断整次检索。 |
| 当前融合边界 | 协调器只汇总，尚不跨源去重 | 防止在身份规则和 provenance 合并未设计完整前错误丢失来源信息。 |
| 环境配置 | `.env` 与 `.env.example` 结构应一致，真实 Key/令牌可不同 | 保持部署可复现，同时禁止读取、输出或提交敏感值。 |

## 4. 已修改、新增和删除的文件

以下为本阶段已提交的主要文件，依据最近提交 `36857bd` 至 `8828fdd` 的 Git 统计整理。

### 新增

- `backend/app/adapters/base.py`
- `backend/app/adapters/semantic_scholar.py`
- `backend/app/adapters/arxiv.py`
- `backend/app/adapters/dblp.py`
- `backend/app/adapters/tavily.py`
- `backend/app/models/query_intent.py`
- `backend/app/models/search_run.py`
- `backend/app/models/discovery.py`
- `backend/app/models/source_routing.py`
- `backend/app/models/multi_source_recall.py`
- `backend/app/services/source_router.py`
- `backend/app/services/multi_source_recall.py`
- `backend/tests/fixtures/semantic_scholar_paper.json`
- `backend/tests/fixtures/arxiv_feed.xml`
- `backend/tests/fixtures/dblp_publication.json`
- `backend/tests/fixtures/tavily_search.json`
- `backend/tests/test_core_models.py`
- `backend/tests/test_semantic_scholar_client.py`
- `backend/tests/test_semantic_scholar_mapper.py`
- `backend/tests/test_openalex_unified_adapter.py`
- `backend/tests/test_arxiv_client.py`
- `backend/tests/test_arxiv_mapper.py`
- `backend/tests/test_dblp_client.py`
- `backend/tests/test_dblp_mapper.py`
- `backend/tests/test_tavily_client.py`
- `backend/tests/test_tavily_mapper.py`
- `backend/tests/test_source_router.py`
- `backend/tests/test_multi_source_recall.py`

### 修改

- `AGENTS.md`
- `.env.example`
- `backend/app/core/config.py`
- `backend/app/core/logging.py`
- `backend/app/adapters/__init__.py`
- `backend/app/adapters/openalex.py`
- `backend/app/models/__init__.py`
- `backend/app/models/paper.py`
- `backend/app/models/search.py`
- `backend/app/services/__init__.py`
- `backend/tests/fixtures/openalex_work.json`
- `backend/tests/test_openalex_config.py`
- `backend/tests/test_openalex_mapper.py`

### 删除

- 最近阶段提交的 Git 统计中未记录删除文件。

## 5. 已执行的测试及结果

- 已多次执行与改动文件对应的 `python -m compileall -q`，静态编译未报告语法错误。
- 已多次执行 `git diff --check`，未报告空白错误。
- 未由 Codex 执行 pytest、外部 API 请求、服务启动或依赖安装；这些操作按协作规则由用户执行。
- 用户曾运行 `backend/tests/test_semantic_scholar_client.py`：其中 2 项通过、1 项失败。失败原因是测试期望路径为 `/paper/search`，实际请求路径为 `/graph/v1/paper/search`；测试已修正为实际基地址包含的版本前缀。用户尚未反馈修正后的再次运行结果。

## 6. 已知问题、失败方案和不能重复踩的坑

- 多源协调器已存在，但尚未接入 FastAPI 路由或应用装配；当前生产 API 仍只有旧的 OpenAlex 搜索接口。
- 多源协调结果尚未执行跨源去重、字段合并、版本族关联或 RRF；不能把当前 `papers` 当作最终论文列表。
- 旧 `deduplicate_papers` 使用 `Paper` 并保留首次出现记录，不会合并 `PaperRecord.source_records`；不要直接把它当作多源融合实现。
- Tavily 结果必须保持在 `discoveries` 中，不能放入 `papers`、`PaperRecord`、引用图或论文去重。
- Semantic Scholar HTTPX 基地址包含 `/graph/v1`，mock 测试的实际路径必须是 `/graph/v1/paper/search`，不是 `/paper/search`。
- Semantic Scholar 只能按 1 RPS 调用；不要提高 `SCHOLARFLOW_SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND`。
- arXiv 返回 Atom XML，不是 JSON；连续请求默认应至少间隔三秒。
- DBLP 单条命中的 `hit` 字段可为对象而非数组，映射必须兼容两种结构。
- `.env` 禁止提交和输出；修改 `.env.example` 后必须同步 `.env` 的结构，同时保留真实 Key/令牌。
- `backend/app/services/__init__.py` 当前有两个连续模块文档字符串，功能不受影响，但后续整理时应合并为一个。

## 7. 尚未完成的任务（按优先级）

1. P0：实现 `PaperRecord` 跨源身份解析与融合，按 DOI、arXiv ID、PMID、来源平台 ID、标题+年份+首作者顺序识别重复项。
2. P0：合并重复记录的 `source_records`、来源专有 ID、作者来源 ID、开放获取信息和更完整元数据，避免只保留第一条记录。
3. P0：建立预印本、会议版、期刊版的版本族标识，并为多源结果计算 RRF 融合分数。
4. P0：将 `MultiSourceRecallCoordinator` 接入应用装配和稳定 API，返回来源统计与降级状态。
5. P1：补充来源级缓存、统一重试和健康状态；当前适配器已有部分限流与错误边界，但没有统一缓存/重试协调层。
6. P1：实现 QueryIntent 生成、LangGraph 工作流、SSE 进度、预算和停止条件。
7. P1：实现 SQLite 持久化、Redis 缓存、BGE-M3/FAISS、排序与 LLM 核验。
8. P2：实现前端文献搜索、文献库、引文图与技术路线可视化。

## 8. 下一步建议从哪个文件和哪个函数开始

建议从 `backend/app/services/deduplication.py` 的 `deduplicate_papers` 开始，但不要直接扩展其“保留第一条”的行为。

推荐新建 `backend/app/services/paper_fusion.py`，先实现以下职责：

1. 复用或迁移 `_normalize_doi`、`_normalize_arxiv_id`、`_normalize_pmid` 和 `_build_title_key`；
2. 接收 `list[PaperRecord]` 并产生身份组；
3. 为每个身份组生成合并后的 `PaperRecord`，合并 `source_records` 而非丢弃后续来源；
4. 返回融合统计，供 `MultiSourceRecallCoordinator.recall` 的下一层调用；
5. 先用离线 fixture 编写 DOI、arXiv、PMID、标题回退、版本族和 provenance 合并测试。

## 9. 运行项目和验证修改所需的命令

前置条件：使用 Python 3.12 的 `scholarflow` Conda 环境；在仓库根目录执行；`.env` 已按 `.env.example` 配置，且真实 Key 仅保存在本地。

```powershell
conda activate scholarflow
pip install -r requirements-dev.txt
uvicorn backend.app.main:app --reload
```

建议按文件定向验证，而不是直接运行全量 pytest：

```powershell
pytest backend/tests/test_core_models.py
pytest backend/tests/test_openalex_config.py
pytest backend/tests/test_openalex_unified_adapter.py
pytest backend/tests/test_semantic_scholar_client.py
pytest backend/tests/test_semantic_scholar_mapper.py
pytest backend/tests/test_arxiv_client.py
pytest backend/tests/test_arxiv_mapper.py
pytest backend/tests/test_dblp_client.py
pytest backend/tests/test_dblp_mapper.py
pytest backend/tests/test_tavily_client.py
pytest backend/tests/test_tavily_mapper.py
pytest backend/tests/test_source_router.py
pytest backend/tests/test_multi_source_recall.py
```

运行当前旧 OpenAlex API 路由的服务入口：

```powershell
uvicorn backend.app.main:app --reload
```

## 10. 当前 Git 分支、未提交改动和需要注意的配置

- 当前分支：`master`。
- 当前最新提交：`8828fdd feat: 增加多源论文召回协调器`。
- 生成本交接文档前，工作区只有 `.gitignore` 的未提交改动；该改动属于用户现有改动，内容和用途未经本记录变更。
- 本交接文档本身会作为新的未跟踪文件出现，需由用户审阅后决定是否提交。
- `.env` 必须不纳入 Git 暂存或提交。需要至少确认：
  - `SCHOLARFLOW_OPENALEX_API_KEY` 已按实际部署配置；
  - `SCHOLARFLOW_SEMANTIC_SCHOLAR_API_KEY` 已填写真实获批 Key；
  - `SCHOLARFLOW_SEMANTIC_SCHOLAR_ENABLED=true`；
  - `SCHOLARFLOW_SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND=1`；
  - Tavily 仅在需要网页补充发现时配置 `SCHOLARFLOW_TAVILY_API_KEY`。
- 当前 `.env.example` 是配置结构的基线；同步 `.env` 时不得输出真实值。
