"""ScholarFlow 外部学术数据源适配器包。"""

from backend.app.adapters.openalex import OpenAlexClient, build_openalex_work_params, map_openalex_work_to_paper  # 对外导出 OpenAlex 适配器接口。

__all__ = ["OpenAlexClient", "build_openalex_work_params", "map_openalex_work_to_paper"]  # 限制适配器包的公共接口。
