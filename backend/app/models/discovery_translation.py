"""定义补充网页发现标题与摘要片段的按需翻译响应契约。"""

from typing import Literal  # 限制网页发现允许翻译的公开字段。

from pydantic import BaseModel, Field  # 约束接口返回的稳定公开数据。


class DiscoveryTranslationResponse(BaseModel):
    """保存用户主动请求的补充网页发现中文译文。

    属性：
        discovery_id：由来源和 URL 生成的稳定匿名缓存标识。
        field：本次翻译的网页标题或摘要片段。
        text_zh：对应字段的简体中文译文。
        model_name：实际执行翻译的模型名称。
    """

    discovery_id: str = Field(min_length=1)  # 绑定译文与独立网页发现缓存项，绝不伪装成论文标识。
    field: Literal["title", "snippet"]  # 标题和摘要片段必须独立请求与缓存。
    text_zh: str = Field(min_length=1, max_length=50000)  # 限制可展示译文长度，防止异常模型输出。
    model_name: str = Field(min_length=1, max_length=200)  # 回传实际模型用于页面最低层级说明。
