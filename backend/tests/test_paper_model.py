"""验证统一论文领域模型的正常与边界数据。"""

import pytest  # 提供异常断言工具。
from pydantic import ValidationError  # 捕获模型字段校验异常。

from backend.app.models.paper import Paper, PaperAuthor  # 导入待测论文与作者模型。


def test_paper_model_preserves_normalized_metadata() -> None:
    """论文模型应保存跨数据源检索流程需要的规范化字段。"""
    paper = Paper(  # 构造包含规划书核心字段的有效论文。
        paper_id="https://openalex.org/W123",  # 提供来源内稳定论文标识。
        title="Large Language Models for Forecasting",  # 提供论文标题。
        abstract="A study about forecasting.",  # 提供论文摘要。
        authors=[PaperAuthor(name="张三", institution="ScholarFlow Lab")],  # 提供规范化作者信息。
        year=2025,  # 提供发表年份。
        venue="NeurIPS",  # 提供发表会议。
        doi="10.1000/example",  # 提供 DOI 标识。
        arxiv_id="2501.00001",  # 提供预印本标识。
        pmid="12345678",  # 提供 PubMed 标识。
        citation_count=12,  # 提供非负引用计数。
        references=["https://openalex.org/W456"],  # 提供引用关系标识。
        source="openalex",  # 标记当前元数据来源。
    )
    assert paper.paper_id == "https://openalex.org/W123"  # 验证论文标识被保留。
    assert paper.authors[0].name == "张三"  # 验证嵌套作者模型被正确解析。
    assert paper.pmid == "12345678"  # 验证 PubMed 标识被正确保存。
    assert paper.citation_count == 12  # 验证引用数被正确保存。


def test_paper_model_rejects_negative_citation_count() -> None:
    """引用数为负数时应拒绝无效的论文元数据。"""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):  # 断言返回引用数下界错误。
        Paper(paper_id="paper-1", title="示例论文", citation_count=-1, source="manual")  # 构造无效引用数。


def test_paper_model_rejects_unknown_source() -> None:
    """未知数据源不能进入规范化论文模型。"""
    with pytest.raises(ValidationError, match="Input should be"):  # 断言返回来源枚举错误。
        Paper(paper_id="paper-1", title="示例论文", source="unknown")  # 构造未受支持的数据源。
