"""定义小集合论文比较的稳定请求与事实型响应契约。"""

from pydantic import BaseModel, Field, field_validator  # 提供请求数量、标识符和响应字段校验。

from backend.app.models.paper import PaperRecord  # 从已保存规范化论文中提取可核验对比字段。


class ComparePapersRequest(BaseModel):
    """描述一次仅允许选择两至五篇已保存论文的比较请求。"""

    paper_ids: list[str] = Field(min_length=2, max_length=5)  # 限制比较规模，避免把大集合交给页面或后续模型。

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, paper_ids: list[str]) -> list[str]:
        """清理论文标识并拒绝空值与重复选择。

        参数：
            paper_ids：客户端提交的内部论文标识列表。
        返回：
            list[str]：去除首尾空白后的原始顺序标识。
        异常：
            ValueError：存在空标识或重复论文时抛出。
        """
        normalized_ids = [paper_id.strip() for paper_id in paper_ids]  # 规范化用户选择的每个资源标识。
        if any(not paper_id for paper_id in normalized_ids):  # 空白标识没有可比较的持久化事实。
            raise ValueError("论文标识不能为空")  # 在 SQLite 查询前阻止无效请求。
        if len(set(normalized_ids)) != len(normalized_ids):  # 同一论文重复出现没有比较价值且会破坏固定列。
            raise ValueError("比较论文不能重复")  # 返回明确的输入错误。
        return normalized_ids  # 保持前端选择顺序作为对比列顺序。


class PaperComparisonItem(BaseModel):
    """表示一篇论文可由现有元数据和核验证据支持的事实型对比列。"""

    paper_id: str  # 绑定每列到稳定内部论文标识。
    title: str  # 展示论文标题。
    publication: str  # 汇总作者、年份、venue 与论文类型等出版事实。
    keywords: list[str] = Field(default_factory=list)  # 原始关键词可能包含方法或数据集，但不作臆测分类。
    abstract: str = ""  # 保留来源提供的摘要，不生成 PDF 全文结论。
    recommendation_reason: str | None = None  # 复用已有的证据化推荐理由。
    constraint_status: str | None = None  # 复用已保存的约束核验状态。
    constraint_evidence: list[str] = Field(default_factory=list)  # 绑定当前论文的公开元数据证据片段。
    sources: list[str] = Field(default_factory=list)  # 展示可追溯的融合来源。

    @classmethod
    def from_paper(cls, paper: PaperRecord) -> "PaperComparisonItem":
        """将完整 PaperRecord 投影为不含未验证推断的比较列。

        参数：
            paper：从 SQLite 最终搜索结果快照恢复的规范化论文。
        返回：
            PaperComparisonItem：可安全并列展示的事实型字段。
        """
        author_names = "、".join(author.name for author in paper.authors if author.name) or "作者信息暂缺"  # 汇总已有作者名称，不补全缺失作者。
        publication_parts = [author_names, str(paper.year) if paper.year else "年份暂缺", paper.venue or "Venue 暂缺"]  # 保持出版元数据的原始可用边界。
        if paper.paper_type:  # 论文类型仅在来源实际提供时加入。
            publication_parts.append(paper.paper_type)  # 不为缺失类型推断会议或期刊属性。
        source_names = [record.source for record in paper.source_records if record.source] or [paper.source]  # 优先展示融合溯源，缺失时回退主来源。
        return cls(paper_id=paper.paper_id, title=paper.title, publication=" · ".join(publication_parts), keywords=paper.keywords, abstract=paper.abstract, recommendation_reason=paper.recommendation_reason, constraint_status=paper.constraint_status, constraint_evidence=paper.constraint_evidence, sources=list(dict.fromkeys(source_names)))  # 去重来源并完整保留已核验证据。


class ComparePapersResponse(BaseModel):
    """返回按用户选择顺序排列的事实型论文比较结果。"""

    items: list[PaperComparisonItem] = Field(min_length=2, max_length=5)  # 保证前端始终渲染有效固定列数量。
