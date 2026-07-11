# ScholarWeave（研索）协作规范

本文件是 ScholarWeave（研索）的开发协作准则。开始任何实现前，先阅读本文件、
`ScholarWeave_Project_Plan_v1.1.md` 与相关模块的现有代码；如规则冲突，以用户最新指令为准。
旧版 `ScholarFlow_项目规划书.md` 仅作历史参考。仓库目录名和已有 `SCHOLARFLOW_` 环境变量前缀暂不改名，除非用户明确发起专门迁移。

## 1. 项目目标与边界

ScholarWeave（研索）是面向复杂科研查询的多源智能论文搜索与推荐系统，而不是普通关键词搜索或简单 RAG。

- 前端使用 Vue 3，后端使用 Python 与 FastAPI。
- 检索编排使用轻量多 Agent + LangGraph 工作流，保持 Query、Search、Analysis、Ranking 与 Knowledge Management 职责分离。
- Codex 仅用于受控开发辅助，不得成为生产运行链路、产品功能或部署依赖。
- 当前核心检索源为 OpenAlex 与 Semantic Scholar；Semantic Scholar 已获批并启用，必须遵守每秒最多一次请求的来源级限制。AI/计算机领域优先按需接入 arXiv、DBLP；Tavily 仅作为补充发现与网页证据来源，不能替代学术来源的论文身份与引用元数据。不要无条件调用所有数据源。
- 持久化使用 SQLite，短期缓存、限流和工作流临时状态使用 Redis；语义向量索引使用 FAISS。
- 排序遵循“规则过滤 → BGE-M3 粗排 → Cross Encoder 重排 → LLM 精排与理由生成”的分层设计。初期不得以模型微调或强化学习替代该方案。
- 核心领域契约为 `QueryIntent`、`PaperRecord`、`SearchRunState` 与 `SearchResult`；补充网页发现使用独立的 `SupplementalDiscoveryItem`，不得伪装为论文记录。Python 模块可渐进兼容演进，但不得在没有迁移计划时随意改写已有公开字段。去重优先级为 DOI、arXiv ID、PMID、来源平台 ID、标题+年份+作者。
- 前端优先实现“文献搜索”和“我的文献库”两大模块。搜索结果应能呈现查询解析、搜索过程、论文列表、推荐理由、收藏能力，以及引文关系图、技术路线分类等可视化入口。
- 图谱体验可参考 PaperGraph：以论文为节点、以引文或语义关系为边，支持按关系、聚类或时间维度理解文献；但必须先完成可用的检索与文献库闭环。

## 2. 实施与规划规则

- 按阶段交付，优先顺序为：基础工程 → 多源检索与规范化/去重 → 排序系统 → 搜索页与文献库/图谱 → 缓存、成本统计与策略优化。
- 当前实施顺序为：核心领域契约、OpenAlex、Semantic Scholar、arXiv、DBLP 与 Tavily 适配器、动态来源路由、多源召回协调，以及 `PaperRecord` 规范化融合、身份去重、版本族关联和 RRF 均已完成；下一步将融合结果接入应用装配与稳定 API，再进入复杂排序或图谱开发。OpenAlex 与已启用的 Semantic Scholar 为核心源；AI/计算机领域按需加入 arXiv 与 DBLP；Tavily 仅在 `QueryIntent.requires_web_evidence=true` 且配置可用时启用。每个来源先实现 `search`，再增加详情、引用和被引能力。
- 一次变更以一个可验收的功能闭环为边界，可合并 2–4 个紧密相关的小任务（如实现、测试及必要配置/文档）；完成后立即停止并等待用户确认下一步。不得将无关模块、多个开发阶段或复杂基础设施一次性混入同一变更。
- 规划完成后，必须在交付说明中明确写出“下一步规划”，并检查本文件是否仍准确；若架构、目录、命令、依赖管理或协作流程发生变化，必须同时更新 `AGENTS.md`，否则说明“AGENTS.md 无需更新”。
- 每次交付说明必须列出本轮新增或更新的文件，并使用可点击的本地文件链接；说明每个文件的主要变更，方便用户直接审阅。
- 对话上下文达到 180k 时停止扩展范围并准备交接；达到 220k 时生成 `docs/handoffs/` 交接文档并建议新建会话；接近 272k 时不得继续常规开发。
- 新模块先定义清晰的输入、输出、错误边界和可替换接口；第三方 API、模型、存储与缓存必须放在适配层或配置层，禁止散落在业务逻辑中。
- 学术搜索 API 先封装为 `adapters/` 中可替换、可单测的客户端方法；仅当 LangGraph 工作流需要自主选择数据源时，才将客户端方法包装为 Agent Tool，Tool 不得直接承载 HTTP、鉴权或响应解析细节。
- 密钥、令牌、数据库地址和模型配置必须从环境变量或配置文件读取。提交 `.env.example`，不得提交真实密钥、令牌、用户数据、下载模型、数据库、缓存文件或运行日志。
- `.env` 必须与 `.env.example` 保持相同字段名、字段顺序、注释结构及非密钥默认值；仅 API Key、令牌等真实敏感值可以不同。每次新增或修改 `.env.example` 时，必须同步检查 `.env` 的结构并补齐缺失项；不得读取、输出、删除或覆盖 `.env` 中已有敏感值，且不得将 `.env` 纳入 Git 暂存或提交。
- OpenAlex 适配器使用 `SCHOLARFLOW_OPENALEX_API_BASE_URL`、`SCHOLARFLOW_OPENALEX_API_KEY` 和 `SCHOLARFLOW_OPENALEX_TIMEOUT_SECONDS` 配置；调用前必须通过配置方法校验 API 密钥，日志中不得输出该密钥。
- 每个来源适配器必须独立封装认证、字段映射、分页、超时、重试、限流、错误映射与健康状态；Tavily 必须实现独立的 `WebDiscoveryAdapter` 并返回不可合并的 `SupplementalDiscoveryItem`，不得进入论文去重、引用关系或学术元数据排序。LangGraph 只依赖统一适配器协议，不得依赖供应商字段。
- 检索迭代必须设置停止条件：目标数量已满足、连续一轮无新增高质量论文、约束已覆盖，或 API/Token 预算达到上限。

## 3. Python 依赖与运行规则

- 必须保留仓库根目录的 `requirements.txt`。其应记录运行时直接依赖及明确版本；开发工具统一记录在根目录 `requirements-dev.txt`，并通过 `-r requirements.txt` 继承运行时依赖。
- 创建或修改 Python 代码且新增第三方依赖时，必须同步将“包名==版本号”加入 `requirements.txt`，去重并保持合理分组；交付时明确提醒用户执行 `pip install -r requirements.txt`。
- 创建或修改 Vue/Node 代码且新增第三方依赖时，必须同步将依赖及精确版本写入 `frontend/package.json`；交付时明确提醒用户在 `frontend/` 执行 `npm install`，不得将 npm 包写入 Python 的 `requirements.txt`。
- 不得仅因规划而预装 FastAPI、LangGraph、Redis、FAISS、模型库等依赖；仅在相关代码真正落地时加入。
- 由用户负责实际运行服务、下载数据集和下载模型。助手可以进行静态检查或 `python -m compileall`，但不得主动启动服务、执行会访问网络的下载、调用外部 API、写入真实业务数据或运行长时任务。
- 后端是根目录下的 `backend` Python 包。后端代码与测试统一使用 `backend.app...` 绝对导入；从仓库根目录使用 `uvicorn backend.app.main:app --reload` 启动、使用 `pytest` 运行测试，不得依赖 `--app-dir` 或 `pythonpath` 改写导入路径。
- 提供可复制的运行、迁移、测试或模型下载命令，并说明其前置条件；不要声称未经用户执行验证的运行结果。
- 每次交付必须明确列出用户应手动运行的具体文件或目标命令，例如指定的 `pytest backend/tests/test_xxx.py`、迁移脚本或服务入口；不得只笼统要求运行全部 `pytest`。
- 仓库文本统一使用 UTF-8；Python 文本读写和文本子进程必须显式指定编码。Windows 入口应配置 UTF-8 输出，终端日志使用 `[OK]`、`[WARN]`、`[ERROR]` 等 ASCII 标记，JSON 需要可读中文时使用 `ensure_ascii=False`。

## 4. 代码质量与中文注释

- 新增或修改代码必须使用必要的中文注释。除极其简单、语义自明的语句外，每行代码都应有简短中文行尾注释；需要说明原因、边界或算法的长注释放在代码上一行。
- 注释解释意图、数据含义、业务规则、边界条件和失败处理，不能机械复述代码。
- 函数、类、接口和数据模型必须有中文文档注释，说明职责、参数、返回值与可能异常。
- 保持模块低耦合，使用类型标注、输入校验和明确异常。对外 API 的响应和错误格式应稳定，方便 Vue 前端消费。
- 不修改无关文件；保留用户现有改动。新增目录和文件名采用清晰、一致的英文命名。

## 5. 日志、错误与可观测性

- 使用 Python 标准 `logging` 建立统一日志配置，日志必须同时输出到控制台和受 Git 忽略的日志文件。日志目录建议为 `logs/`，使用按日期或大小滚动，避免无限增长。
- `SCHOLARFLOW_LOG_DIR` 使用相对路径时，必须相对仓库根目录解析，不能随 pytest、IDE 或服务启动时的当前工作目录变化；绝对路径配置保持原样。
- 记录阶段性完成信息：数据集下载/处理信息、模型下载/加载信息、索引构建信息、检索轮次、数据源调用结果、缓存命中、排序数量和关键耗时。
- 记录运行输出中的关键统计信息，例如论文召回数、去重数、过滤数、最终返回数、Token/API 调用次数与错误信息。
- 捕获异常时必须使用 `logger.exception` 或等效方式记录完整堆栈，并向调用方返回不泄露密钥、内部路径或原始响应的可理解错误。
- 日志不得写入 API 密钥、访问令牌、完整用户敏感查询、受限论文全文或个人数据。

## 6. 测试与验收

- 每个新增可独立测试的后端模块都应添加对应测试，至少覆盖正常路径、空结果/边界条件和外部服务异常。
- 外部 API、Redis、模型与文件系统在单元测试中应使用可控的 mock 或测试替身；测试不能依赖真实密钥、网络或本地已下载模型。
- 修改后至少执行与改动相符的静态检查、测试或 `python -m compileall`；若未执行，应明确说明原因和用户可执行的验证命令。
- 完成汇报应说明：可点击的变更文件及其内容、实现结果、已执行验证、用户应手动运行的具体文件/命令、未验证项、依赖安装提醒（如适用）、下一步规划，以及 `AGENTS.md` 是否已检查/更新；每次产生文件变更后，必须附上由用户手动执行的 Git 提交命令。

## 7. Git 提交规范

- 提交前检查 `git status` 与差异，确认只包含本任务相关文件；不得覆盖或清理用户已有的无关改动。
- 提交信息使用中文并遵循 Conventional Commits：`feat:`、`fix:`、`docs:`、`refactor:`、`test:`、`chore:`，例如 `feat: 增加 OpenAlex 检索适配器`。
- 一次提交只包含一个逻辑变更，并包含所需代码、测试、依赖清单和文档更新；不要将格式化、重构和功能改动无关混合。
- 提交前不得包含 `.env`、密钥、`logs/`、SQLite 数据库、Redis 持久化文件、下载模型、构建产物、覆盖率文件或大体积数据集。必要时更新 `.gitignore`。
- 未经用户明确要求，不执行提交、推送、创建分支、创建 Pull Request、变基、强制重置或任何会改写 Git 历史的操作；仅生成改动并报告建议的提交命令。

## 8. 推荐目录演进

在基础工程阶段，优先保持如下职责分层；可以按实际实现调整，但调整后同步更新本文件：

```text
backend/              # 可直接从仓库根目录导入的 Python 包
  app/
    api/            # FastAPI 路由与请求/响应模型
    agents/         # LangGraph 节点及工作流
    services/       # 查询、检索、去重、排序等业务服务
    adapters/       # OpenAlex、Semantic Scholar、LLM、模型等外部适配器
    repositories/   # SQLite、Redis、FAISS 的访问封装
    models/         # 领域与持久化数据模型
    core/           # 配置、日志、异常与通用基础设施
  tests/           # Python 测试包，确保 pytest 从仓库根目录导入 backend
frontend/           # Vue 3 应用
logs/                # 运行日志，不提交
data/                # 本地数据、索引与数据库，不提交
requirements.txt
requirements-dev.txt # 测试等开发依赖，通过 requirements.txt 继承运行时依赖
.env.example         # 可提交的环境变量配置示例
pytest.ini            # 后端测试的导入路径和收集范围配置
```
