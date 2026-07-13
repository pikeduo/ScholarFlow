"""从已保存论文关键词构建不调用模型的保守技术路线。"""

from collections.abc import Sequence  # 标注已按请求顺序恢复的论文集合。

from backend.app.models.paper import PaperRecord  # 读取来源提供的关键词事实。
from backend.app.models.technical_routes import TechnicalRoute, TechnicalRoutesResponse  # 使用稳定路线响应契约。


class TechnicalRouteService:
    """按来源关键词聚合当前小集合论文，拒绝生成未经证据支持的路线解释。"""

    def build(self, papers: Sequence[PaperRecord]) -> TechnicalRoutesResponse:
        """以大小写无关关键词聚合论文并回显原始证据。

        参数：
            papers：从 SQLite 最终结果快照恢复的论文集合。
        返回：
            TechnicalRoutesResponse：按论文数量和关键词排序的保守路线。
        """
        groups: dict[str, tuple[str, list[str]]] = {}  # 保存规范化键、首个展示词和关联论文标识。
        for paper in papers:  # 逐篇处理来源已保存的关键词。
            for keyword in paper.keywords:  # 不从摘要或标题猜测缺失关键词。
                display_keyword = keyword.strip()  # 清理展示关键词两端空白。
                if not display_keyword:  # 空关键词不构成路线证据。
                    continue  # 跳过无效输入。
                key = display_keyword.casefold()  # 使用大小写无关键聚合英文词形。
                if key not in groups:  # 首次出现时保留来源原始展示词。
                    groups[key] = (display_keyword, [])  # 初始化关键词路线成员。
                if paper.paper_id not in groups[key][1]:  # 同一论文重复关键词只算一次。
                    groups[key][1].append(paper.paper_id)  # 保持请求论文顺序作为代表顺序。
        routes = [TechnicalRoute(route_id=f"keyword:{key}", name=name, summary=f"由 {len(paper_ids)} 篇已保存论文的关键词“{name}”聚合，不包含模型推断。", paper_ids=paper_ids, representative_paper_ids=paper_ids[:3], evidence=[name]) for key, (name, paper_ids) in groups.items()]  # 将每个关键词投影为可审计路线。
        routes.sort(key=lambda route: (-len(route.paper_ids), route.name.casefold()))  # 优先展示覆盖论文更多的路线并保持稳定排序。
        return TechnicalRoutesResponse(routes=routes)  # 返回只基于关键词事实的路线集合。
