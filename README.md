# ScholarFlow

ScholarFlow 是一个面向复杂科研问题的多源论文搜索与推荐系统。

系统先将自然语言研究问题解析为结构化检索意图，再根据研究领域和检索轮次选择学术数据源。多轮候选生成结束后，系统统一完成语义排序、约束核验和结果保存，并提供论文比较、引用关系图和个人文献库等功能。

## 主要功能

- **自然语言查询规划**：由 Query Agent 生成检索式、结构化查询条件和补充子查询。
- **多源论文检索**：支持 OpenAlex、Semantic Scholar，并按领域使用 arXiv、DBLP 和 PubMed。
- **有限轮次检索**：使用 LangGraph 编排来源选择、候选合并、覆盖评估和查询调整。
- **论文融合与去重**：统一不同来源的论文信息，按照 DOI、arXiv ID、PMID、来源平台 ID 以及标题和作者识别重复论文，并保留版本关系。
- **分层筛选与排序**：每轮先完成 RRF 融合和确定性规则过滤；全部轮次结束后，可选使用 BGE-M3 和 Cross Encoder，并由 DeepSeek 辅助核验复杂语义条件和生成推荐理由。
- **搜索记录与结果恢复**：SQLite 保存搜索运行状态、候选快照和最终结果，页面刷新后可以直接恢复已有搜索。
- **个人文献库**：支持论文收藏、阅读状态、关键词、备注、论文比较和语义检索。
- **离线评测与消融实验**：支持 Precision、Recall、F1、MRR、nDCG、效率统计、候选快照和分层排序消融。
- **可选 Redis**：用于学术来源缓存、限流、多进程协调和可替换的事件发布。
- **网页补充发现**：Tavily 结果单独展示，不参与正式论文的身份去重、排序和引用关系图。

## 系统架构

<!-- TODO: 将 PPT 中修正后的系统架构图导出为 README/system-architecture.png，并在此处插入。 -->

> 系统架构图待补充。建议图片路径：`README/system-architecture.png`

系统主要分为以下几层：

1. Vue 3 前端负责查询输入、结构化条件确认、SSE 进度展示和结果管理。
2. FastAPI 提供 REST API、SSE 接口和业务服务入口。
3. Query Agent 在进入 LangGraph 之前，将自然语言问题转换为 `QueryIntent`。
4. LangGraph 控制有限轮次的来源召回、覆盖评估、查询演化和停止判断。
5. OpenAlex、Semantic Scholar、arXiv、DBLP 和 PubMed 提供正式论文数据；Tavily 仅提供独立的网页补充结果。
6. SQLite 保存搜索状态、论文结果和个人文献库；Redis 提供可选缓存与协调能力；FAISS 保存个人文献库的可重建向量索引。

## LangGraph 检索工作流

<!-- TODO: 将 PPT 中修正后的 LangGraph 工作流图导出为 README/langgraph-workflow.png，并在此处插入。 -->

> LangGraph 工作流图待补充。建议图片路径：`README/langgraph-workflow.png`

系统采用有限轮次的候选生成策略：

1. Query Agent 将用户问题转换为检索式和 `QueryIntent`。
2. LangGraph 初始化 `SearchRunState`，并在调用外部来源前保存运行状态。
3. 每轮根据研究领域、当前轮次和可用来源执行论文召回。
4. 本轮结果依次经过规范化、身份去重、版本关联、RRF 融合和确定性规则过滤。
5. 系统将本轮论文合并到累计候选集合，并评估目标数量、条件覆盖、来源状态、边际收益和预算。
6. 未满足停止条件时，根据覆盖缺口选择或生成下一条补充查询，进入下一轮检索。
7. 达到目标数量、最大轮次、预算边界、来源限制或没有可执行新查询时结束候选生成。
8. 系统对全部轮次的候选统一执行一次可选 BGE-M3、可选 Cross Encoder 和 DeepSeek 语义核验，随后保存最终结果与用量快照。

BGE-M3、Cross Encoder 和 DeepSeek 不在每一轮重复执行，而是在候选生成结束后统一运行一次。

## 技术栈

- **后端**：Python 3.12、FastAPI、LangGraph、Pydantic
- **前端**：Vue 3、TypeScript、Vite
- **数据与索引**：SQLite、FAISS，可选 Redis
- **模型**：DeepSeek、BGE-M3、BGE Reranker
- **测试与评测**：Pytest、Vitest、PaSa AutoScholarQuery 开发集适配

## 快速开始

### 创建 Conda 环境

```powershell
conda create -n scholarflow python=3.12 -y
conda activate scholarflow
```

### 安装后端依赖

```powershell
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 安装前端依赖

```powershell
cd frontend
npm install
cd ..
```

### 配置环境变量

在仓库根目录复制配置模板：

```powershell
Copy-Item .env.example .env
```

根据启用的功能配置 DeepSeek、OpenAlex、Semantic Scholar 和 Tavily 等 API Key。

不要将 `.env`、API Key、数据库、日志、模型缓存、评测原始数据或真实用户数据提交到仓库。

### 启动项目

后端：

```powershell
uvicorn backend.app.main:app --reload
```

前端：

```powershell
cd frontend
npm run dev
```

前端默认地址为 `http://127.0.0.1:30000`，FastAPI OpenAPI 文档默认地址为 `http://127.0.0.1:8000/docs`。

## API 与数据源

业务 API 统一使用 `/api/v1` 前缀，完整接口和请求参数请查看 FastAPI OpenAPI 文档。

常用能力包括：

- 自然语言查询规划
- 多轮论文检索与 SSE 进度推送
- 搜索运行状态、结果快照与异常恢复
- 论文详情、字段翻译和多论文比较
- 引用关系图与技术路线整理
- 文献收藏、阅读状态、备注和语义检索
- 搜索用量与综合报告

主要数据源：

| 来源 | 用途 |
| --- | --- |
| OpenAlex | 核心论文检索与元数据获取 |
| Semantic Scholar | 论文检索与引用信息 |
| arXiv | 预印本检索 |
| DBLP | 计算机领域论文检索 |
| PubMed | 医学和生命科学论文检索 |
| Tavily | 可选网页补充发现 |

Tavily 的网页发现与正式论文结果相互独立，不参与论文身份去重、分层排序或引用关系图构建。

## 模型与下载

模型按需加载。关闭相应功能时，不会加载对应本地模型。

| 用途 | 默认模型 |
| --- | --- |
| 查询规划、复杂语义条件核验、推荐理由和翻译 | DeepSeek |
| 语义嵌入与粗排 | `BAAI/bge-m3` |
| 精细重排序 | `BAAI/bge-reranker-v2-m3` |

如需提前下载本地模型，可在已激活的 Conda 环境中执行：

```powershell
python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3')"
python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3')"
```

首次执行会从模型托管服务下载并缓存模型。离线部署时，应预先准备模型缓存，或者将模型名称替换为本地模型目录。

## 离线评测

`evaluation/` 是与生产搜索流程分离的离线评测模块，详细说明见 [`evaluation/README.md`](evaluation/README.md)。

当前能力包括：

- 统一论文标识规范化和保守匹配
- Precision、Recall、F1、MRR 和二元 nDCG
- Micro、Macro 和效率指标汇总
- 排序前候选快照校验
- RRF、BGE-M3、Cross Encoder 和 DeepSeek 的离线消融
- 查询覆盖诊断和报告生成
- PaSa `AutoScholarQuery/dev.jsonl` 的本地金标导入
- 显式授权后的在线候选快照导出

评测模块默认只读取用户明确提供的本地文件。只有手动执行带 `--allow-online-sources` 的候选快照导出命令时，才会访问真实学术来源。

PaSa 原始数据应由用户自行获取并保存在 Git 忽略目录中。当前只支持已经确认字段结构的 AutoScholarQuery 开发集，不对尚未确认格式的 RealScholarQuery 数据进行推测性解析。

## 项目结构

```text
ScholarFlow/
├─ backend/
│  ├─ app/                 # FastAPI、LangGraph、来源适配器、排序与数据存储
│  └─ tests/               # 后端测试
├─ frontend/
│  ├─ src/                 # Vue 3 前端应用
│  └─ tests/               # 前端测试
├─ evaluation/             # 离线评测、PaSa 导入、消融实验与报告
├─ scripts/                # 数据准备与候选快照导出脚本
├─ README/                 # README 使用的图片资源
├─ data/                   # SQLite、FAISS 与本地评测数据，不提交
├─ logs/                   # 运行日志，不提交
├─ .env.example            # 环境变量模板
├─ requirements.txt        # 后端运行依赖
├─ requirements-dev.txt    # 开发与测试依赖
├─ pytest.ini              # Pytest 配置
└─ AGENTS.md               # 项目开发约定
```

## 测试

后端：

```powershell
pytest backend/tests
```

离线评测模块：

```powershell
pytest evaluation/tests -q
```

前端：

```powershell
cd frontend
npm test
npm run build
```

## 效果演示

### 检索页面

![检索页面](README/image-20260714210902982.png)

### 检索报告

![检索报告](README/image-20260714210928980.png)

### 检索结果

![检索结果](README/image-20260714211027414.png)

### 文献库

![个人文献库](README/image-20260714211057337.png)
