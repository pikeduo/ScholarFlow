# ScholarWeave（研索）协作规范

## 1. 开始任务

- 每次任务先阅读本文件、`docs/ScholarWeave_Project_Plan_v1.1.md`，再按任务读取最少必要的阶段规划和模块代码；用户最新指令优先。
- 根目录只保留 `README.md` 与 `AGENTS.md`；其他文档放入 `docs/`。`docs/` 默认被 Git 忽略，已跟踪文件不会自动取消跟踪。
- 项目品牌为 ScholarWeave（研索）；仓库名和既有 `SCHOLARFLOW_` 环境变量不作无关迁移。
- 一次变更只完成一个可验收闭环；不混入无关重构、阶段或基础设施。

## 2. 产品与架构边界

- ScholarWeave 是复杂科研查询的多源论文搜索与推荐系统，不是通用 RAG。前端为 Vue 3，后端为 FastAPI，检索由 LangGraph 编排。
- 保持 Query、Search、Analysis、Ranking、Knowledge Management 职责分离；Codex 仅用于开发辅助，不进入生产链路。
- 核心契约为 `QueryIntent`、`PaperRecord`、`SearchRunState`、`SearchResult`。网页发现必须使用 `SupplementalDiscoveryItem`，不得伪装成论文。
- 身份去重优先 DOI、arXiv ID、PMID、来源 ID、标题+年份+作者；版本族与引用关系只使用已保存的事实，不得由 LLM 推断。
- 存储职责固定：SQLite 保存业务与运行快照，Redis 用于可选缓存、限流和临时状态，FAISS 用于向量索引。
- 排序顺序固定为规则过滤、BGE-M3、Cross Encoder、LLM 精排与理由生成；不得以微调或强化学习替代该分层。

## 3. 来源、模型与搜索边界

- OpenAlex 与已启用的 Semantic Scholar 是核心来源；AI/计算机按需加入 arXiv、DBLP，医学按需加入 PubMed；Tavily 仅在 `requires_web_evidence=true` 时作补充证据，不能参与论文身份、去重或引用元数据。
- 所有幂等学术 GET 必须经共享执行器，保留来源独立 RPS，并处理 408、429、5xx 和连接/读取超时：优先受限的 `Retry-After`，否则 15、30、60 秒退避加抖动，最多三次重试。最终 429 必须同步本地与 Redis 冷却至少 30 秒；Redis 不可用时回退进程内机制。
- 来源适配器独立封装认证、分页、映射、重试、限流和错误；LangGraph 只依赖统一协议。密钥和模型配置只从环境变量或配置读取，日志不得泄露。
- 标准搜索最多三轮，每轮只调用一个学术来源；每轮只做候选生成、融合与规则过滤，终态后才对累计候选统一排序。达到目标、无新增高质量结果、约束覆盖或预算上限时停止。
- 标准搜索必须有 DeepSeek 精排、约束核验和理由；BGE-M3 与 Cross Encoder 由用户选择且应提示耗时。论文精排每批 5–10 篇，默认最多 10 篇；单批失败只降级该批。
- 历史、详情、比较、技术路线、引用图、综合报告和用量接口只读取同次 SQLite 终态快照，不触发来源、模型或 PDF。运行中记录不得清理；终态清理由用户明确确认。

## 4. 工程规则

- 新模块先定义输入、输出、错误边界和可替换接口；第三方服务只能位于适配层或配置层。
- Python 依赖写入根目录 `requirements.txt` 并精确锁定；开发依赖写入 `requirements-dev.txt`。前端依赖写入 `frontend/package.json`。不为规划预装依赖。
- `.env` 必须与 `.env.example` 的字段、顺序、注释和非密钥默认值一致；不得读取、输出、覆盖、暂存或提交 `.env`。
- 代码使用 UTF-8、类型标注、中文文档注释和必要的中文行内注释；稳定 API 需要明确的错误契约。日志同时写控制台和受忽略日志文件，异常记录堆栈但不暴露敏感数据。
- 后端使用 `backend.app...` 绝对导入；从根目录以 `uvicorn backend.app.main:app --reload` 和 `pytest` 运行。测试使用 mock/fixture，不依赖网络、真实密钥、真实模型或用户数据。

## 5. 测试、交付与 Git

- 每个可独立测试模块至少覆盖正常、空/边界和外部异常路径；变更后执行相称的静态检查、定向测试或 `python -m compileall`。
- 交付说明写明阶段、变更文件链接、验证结果、用户应手动执行的精确命令、未验证项、下一步规划与本文件是否需要更新。
- 提交前检查状态与差异，只包含本任务文件；不得提交 `.env`、密钥、日志、数据库、缓存、模型、构建产物或真实数据。未经用户明确要求，不提交、推送、建分支、建 PR 或改写历史。
- 提交信息使用中文 Conventional Commits；每次产生文件变更时附上用户可手动执行的提交命令。

## 6. 当前规划

文献搜索闭环、运行快照、SSE、只读结果分析、技术路线、引用图、用量和综合报告已完成。下一步是按 `docs/acceptance/文献搜索端到端验收清单.md` 完成真实环境验收；通过后再决定是否扩展文献库。
