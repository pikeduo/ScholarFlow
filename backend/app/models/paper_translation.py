"""定义论文标题与摘要按需翻译的稳定响应契约。"""

from typing import Literal  # 限制翻译请求对应的论文文本字段。

from pydantic import BaseModel, Field  # 约束翻译接口返回的公开文本字段。


class PaperTranslationResponse(BaseModel):
    """保存某篇已保存论文由用户主动请求的中文翻译。

    属性：
        paper_id：对应 SQLite 已保存论文的稳定内部标识。
        field：本次翻译的标题或摘要字段。
        text_zh：对应字段的简体中文译文。
        model_name：实际执行翻译的模型名称。
    """

    paper_id: str = Field(min_length=1)  # 绑定翻译结果与不可伪造的已保存论文标识。
    field: Literal["title", "abstract"]  # 明确本次结果只对应用户请求的一个字段。
    text_zh: str = Field(min_length=1, max_length=50000)  # 保留标题或完整摘要译文并限制异常大输出。
    model_name: str = Field(min_length=1, max_length=200)  # 返回实际模型以便界面说明翻译来源。
