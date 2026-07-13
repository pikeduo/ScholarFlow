"""定义论文标题与摘要按需翻译的稳定响应契约。"""

from pydantic import BaseModel, Field  # 约束翻译接口返回的公开文本字段。


class PaperTranslationResponse(BaseModel):
    """保存某篇已保存论文由用户主动请求的中文翻译。

    属性：
        paper_id：对应 SQLite 已保存论文的稳定内部标识。
        title_zh：论文标题的简体中文翻译。
        abstract_zh：论文摘要的简体中文翻译。
        model_name：实际执行翻译的模型名称。
    """

    paper_id: str = Field(min_length=1)  # 绑定翻译结果与不可伪造的已保存论文标识。
    title_zh: str = Field(min_length=1, max_length=2000)  # 限制标题翻译长度防止异常响应撑破页面。
    abstract_zh: str = Field(min_length=1, max_length=50000)  # 保留完整摘要译文并限制异常大输出。
    model_name: str = Field(min_length=1, max_length=200)  # 返回实际模型以便界面说明翻译来源。
