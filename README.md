# ScholarFlow

ScholarFlow 是一个面向复杂科研问题的多源论文搜索与推荐系统。

系统将自然语言研究问题解析为结构化检索意图，根据研究领域动态选择学术数据源，并通过检索、融合、排序与约束核验生成可追溯的文献结果。

## 主要功能

- 自然语言查询规划：生成检索式与结构化查询条件。
- 多源论文检索：支持 OpenAlex、Semantic Scholar，并按领域使用 arXiv、DBLP、PubMed。
- 多阶段排序：结合规则过滤、RRF、BGE-M3、Cross Encoder 与 DeepSeek。
- LangGraph 多轮检索工作流：支持来源路由、结果融合、缺口评估和迭代检索。
- 搜索快照与文献库：SQLite 保存运行状态，支持论文详情、比较、收藏和语义搜索。
- 可选 Redis：提供缓存、限流和多进程协同能力。

## 整体架构

```mermaid
%%{init: