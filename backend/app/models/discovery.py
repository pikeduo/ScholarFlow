"""定义补充网页发现结果及其独立来源协议所需的数据契约。"""

from typing import Literal  # 限制当前可追溯的补充发现来源名称。

from pydantic import BaseModel, Field  # 提供网页发现结果的结构化校验。


DiscoverySource = Literal["tavily"]  # 标记非论文元数据来源的稳定名称。


class SupplementalDiscoveryItem(BaseModel):
    """保存不能直接并入论文集合的补充网页发现结果。

    属性：
        source：提供网页发现的补充来源。
        title：来源返回的网页标题。
        url：来源返回的网页地址。
        snippet：可展示的简短网页摘要，不保存完整原文。
        relevance_score：来源返回的可选相关性分数。
        raw_rank：结果在该来源当前响应中的原始名次。
        mergeable_as_paper：固定为 False，阻止该条目进入论文去重或引文图流程。
    """

    source: DiscoverySource  # 强制记录补充发现来源以便界面明确标识。
    title: str = Field(min_length=1)  # 确保发现项始终具有可展示的标题。
    url: str = Field(min_length=1, pattern=r"^https?://")  # 仅接受来源返回的 HTTP 或 HTTPS 网页地址。
    snippet: str = ""  # 仅保留可展示摘要，缺失时不虚构正文内容。
    relevance_score: float | None = Field(default=None, ge=0)  # 保留来源可选相关性分数且禁止负值。
    raw_rank: int = Field(ge=1)  # 保留来源原始名次便于解释与后续排序。
    mergeable_as_paper: Literal[False] = False  # 明确禁止将网页发现项直接视为论文元数据。
