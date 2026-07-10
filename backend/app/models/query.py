"""定义由查询理解模块产出的结构化学术检索约束。"""

from pydantic import BaseModel, Field, model_validator  # 提供数据模型、字段约束与跨字段校验。


class QuerySchema(BaseModel):
    """描述一次学术论文检索的结构化意图与筛选约束。

    属性：
        topic：研究主题关键词。
        method：期望匹配的方法或模型关键词。
        dataset：期望涉及的数据集关键词。
        domain：研究领域关键词。
        year_range：发表年份闭区间，格式为（起始年份，结束年份）。
        venue：期望匹配的期刊或会议名称。
        must_include：结果必须尽量包含的关键词。
        exclude：结果应排除的关键词。
        target_count：期望返回的最大论文数量。
    """

    topic: list[str] = Field(default_factory=list)  # 保存研究主题关键词列表。
    method: list[str] = Field(default_factory=list)  # 保存研究方法关键词列表。
    dataset: list[str] = Field(default_factory=list)  # 保存数据集关键词列表。
    domain: list[str] = Field(default_factory=list)  # 保存研究领域关键词列表。
    year_range: tuple[int, int] | None = None  # 允许不限制发表年份。
    venue: list[str] = Field(default_factory=list)  # 保存期刊或会议筛选条件。
    must_include: list[str] = Field(default_factory=list)  # 保存必须匹配的关键词。
    exclude: list[str] = Field(default_factory=list)  # 保存排除关键词。
    target_count: int = Field(default=20, ge=1, le=100)  # 限制单次检索规模以控制成本。

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> "QuerySchema":
        """校验年份区间和相互冲突的包含/排除关键词。

        返回：
            QuerySchema：通过校验的当前模型实例。
        异常：
            ValueError：年份区间倒置或关键词同时被要求包含和排除时抛出。
        """
        if self.year_range and self.year_range[0] > self.year_range[1]:  # 防止产生无效年份筛选。
            raise ValueError("year_range 的起始年份不能晚于结束年份")  # 返回清晰的调用方错误。
        required_terms = {term.strip().casefold() for term in self.must_include if term.strip()}  # 规范化必须包含项。
        excluded_terms = {term.strip().casefold() for term in self.exclude if term.strip()}  # 规范化排除项。
        conflicting_terms = required_terms & excluded_terms  # 识别同一关键词的矛盾约束。
        if conflicting_terms:  # 仅在实际存在冲突时阻止请求进入检索流程。
            raise ValueError("must_include 与 exclude 不能包含相同关键词")  # 避免不可解释的搜索结果。
        return self  # 返回已完成跨字段校验的模型。
