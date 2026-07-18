"""导出离线标识匹配、检索、效率和结构指标。"""

from evaluation.metrics.aggregate import aggregate_query_metrics  # 导出聚合入口。
from evaluation.metrics.efficiency import EfficiencyProxyConfig, summarize_efficiency  # 导出效率代理配置与汇总。
from evaluation.metrics.identifiers import deduplicate_papers, papers_match  # 导出去重和匹配入口。
from evaluation.metrics.retrieval import evaluate_query  # 导出查询级评测入口。
from evaluation.metrics.structure import evaluate_structure  # 导出结构代理入口。

__all__ = ["EfficiencyProxyConfig", "aggregate_query_metrics", "deduplicate_papers", "evaluate_query", "evaluate_structure", "papers_match", "summarize_efficiency"]
