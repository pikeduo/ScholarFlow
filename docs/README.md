# ScholarFlow 技术规划文档集

本目录包含一份总体技术规划和七份详细阶段文档。阶段文档不仅说明“做什么”，还包含建议目录、领域模型、接口、数据库、算法流程、错误处理、测试、依赖和验收条件，可直接作为 Codex 分阶段开发的输入。

## 文档列表

1. `00_ScholarFlow_总体规划.md`
2. `01_阶段一_工程基础与数据基线.md`
3. `02_阶段二_多源学术检索.md`
4. `03_阶段三_查询理解与工作流编排.md`
5. `04_阶段四_规范化去重与多源融合.md`
6. `05_阶段五_语义排序与智能核验.md`
7. `06_阶段六_后端接口与前端应用.md`
8. `07_阶段七_系统集成与交付.md`
9. `ScholarFlow_评测与测试规划.md`
10. `acceptance/文献搜索端到端验收清单.md`

## 已实现的评测入口

- 第一阶段评测模块说明：`../evaluation/README.md`；
- 合成 fixture、候选快照、JSONL 契约、检索/排序指标、效率与结构代理分、A/B/C/D 离线消融编排及报告均位于独立 `evaluation/` 目录；
- 排序前候选快照固定在确定性规则过滤后、BGE-M3 前，并使用 SHA-256 封存；同一矩阵的 BGE-M3/Cross Encoder 配置共享快照哈希，计划生成不会执行模型；
- 生产搜索已将来源路由与调用、身份融合/RRF 和规则过滤抽取为不依赖排序模型的 `CandidateGenerationService`，现有公共 API 仍继续执行完整排序链；
- `fixture`、`snapshot-check` 和 `ablation-plan` 保持完全离线，不读取 `.env`，不调用生产搜索、学术 API、LLM 或本地模型；
- `dataset-gold-import` 同样完全离线：只把用户已准备的公开数据集金标 JSONL 校验并转换为现有 `GoldQuery`，不下载、猜测或解析未经确认的第三方原始格式；
- `pasa-gold-import` 已支持经本地样例确认的 `AutoScholarQuery/dev.jsonl` 原生字段，严格配对论文标题与 arXiv ID；PaSa 原始重复标注会按统一身份规则保留首次论文并审计 `pasa_duplicate_answer_count`，而通用导入仍拒绝重复金标；RealScholarQuery 原始格式仍须先确认；
- `scripts/download_pasa_dataset.py` 是用户显式执行的 PaSa gated 数据集选择性下载工具：默认仅下载 `AutoScholarQuery/dev.jsonl`，可选增加 RealScholarQuery 与论文 ID 映射，使用本机 `hf auth login` 凭据且不保存 Token；
- `snapshot-export` 是唯一受控在线例外：只在用户显式提供 `--allow-online-sources` 后读取已准备的单轮 `QueryIntent`、延迟装配 `CandidateGenerationService` 并写出一份排序前快照；它拒绝网页发现和已存在输出，不运行 Query Agent、BGE-M3、Cross Encoder 或 DeepSeek；
- 真实数据集、模型推理和完整 benchmark 仍必须由用户显式执行。

## 使用原则

- 开发前先阅读总体规划、`AGENTS.md` 和当前阶段文档。
- 一次只让 Codex 实现一个小阶段，不要一次生成整个项目。
- Codex 先检查当前仓库和前置阶段完成情况，再提出实现计划。
- 实现必须复用现有模块，不得随意替换技术栈。
- 新增依赖必须固定版本并更新 requirements 或 package.json。
- 代码执行、模型下载、真实 API 调用和 Git 写操作由用户完成。
- 每个小阶段完成后按文档中的测试和验收条件检查。
- 阶段结束后再进入下一阶段，避免先做 UI、后补核心数据结构。

## 推荐的 Codex 单次任务形式

```text
请阅读：
1. AGENTS.md
2. docs/plans/00_ScholarFlow_总体规划.md
3. docs/plans/当前阶段文档.md

本次只实现“小阶段 X.X”。
先检查现有代码和前置条件，给出文件级实施计划；确认后再修改代码。
不要提前实现后续小阶段。
完成后列出修改文件、依赖变化、用户执行命令、Git 建议、下一步小阶段，并检查是否需要更新 AGENTS.md。
```
