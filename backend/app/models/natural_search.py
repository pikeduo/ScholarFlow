"""定义自然语言搜索入口请求契约。"""

from pydantic import BaseModel, Field, model_validator  # 校验用户查询和显式覆盖条件。

from backend.app.models.query_intent import SearchMode  # 复用稳定搜索模式枚举。


class NaturalSearchRequest(BaseModel):
    """保存前端自然语言问题及用户显式填写的约束。"""

    query: str = Field(min_length=1, max_length=1200)  # 保存待 Query Agent 解析的自然语言问题。
    search_mode: SearchMode = "standard"  # 保存标准或深度模式选择。
    year_range: tuple[int, int] | None = None  # 保存用户显式填写的年份闭区间。
    must_include: list[str] = Field(default_factory=list)  # 保存不可被模型降级的显式必须条件。
    should_include: list[str] = Field(default_factory=list)  # 保存用户显式软偏好。
    exclude: list[str] = Field(default_factory=list)  # 保存用户显式排除条件。
    domains: list[str] = Field(default_factory=list)  # 保存用户显式领域标签。
    requires_web_evidence: bool = False  # 保存网页补充证据开关。
    target_paper_count: int = Field(default=20, ge=1, le=20)  # 当前产品最终最多返回二十篇。

    @model_validator(mode="after")
    def validate_year_range(self) -> "NaturalSearchRequest":
        """拒绝倒置年份范围。"""
        if self.year_range and self.year_range[0] > self.year_range[1]:  # 防止产生不可执行年份条件。
            raise ValueError("year_range 的起始年份不能晚于结束年份")  # 返回稳定输入错误。
        return self  # 返回通过校验的请求。
