"""验证 PaperRecord 跨来源身份融合、版本族与 RRF 计算。"""

import pytest  # 提供无效 RRF 配置的异常断言。

from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 构造带来源溯源的统一论文记录。
from backend.app.services.paper_fusion import PaperFusionService  # 导入待测的跨来源论文融合服务。


def _record(
    source: str,
    paper_id: str,
    title: str = "A Study of Retrieval",
    **overrides: object,
) -> PaperRecord:
    """构造保留来源主标识与原始排名的最小融合测试记录。"""
    raw_rank = overrides.pop("raw_rank", 1)  # 允许测试覆盖 RRF 所需的来源原始排名。
    source_records = overrides.pop("source_records", [PaperSourceRecord(source=source, external_id=paper_id, raw_rank=raw_rank)])  # 默认写入完整来源溯源记录。
    return PaperRecord(  # 构造可由不同来源互补字段覆盖的统一论文。
        paper_id=paper_id,  # 提供来源内稳定论文标识。
        title=title,  # 提供用于展示或标题回退的论文标题。
        source=source,  # 标记当前来源。
        source_records=source_records,  # 保留来源原始排名与后续 RRF 输入。
        **overrides,  # 注入当前测试关注的可选元数据。
    )


def test_fusion_merges_doi_records_and_preserves_complementary_provenance() -> None:
    """相同 DOI 的跨来源记录应融合字段、作者来源 ID 和来源溯源。"""
    openalex = _record(  # 构造提供 DOI、摘要和 OpenAlex 作者 ID 的记录。
        "openalex",
        "https://openalex.org/W1",
        doi="https://doi.org/10.1000/Example.",
        abstract="A concise abstract.",
        authors=[PaperAuthor(name="Ada Lovelace", source_author_ids={"openalex": "A1"})],
        openalex_id="https://openalex.org/W1",
        raw_rank=2,
    )
    semantic_scholar = _record(  # 构造提供开放访问、关键词和 Semantic Scholar 作者 ID 的记录。
        "semantic_scholar",
        "S2-1",
        doi="doi:10.1000/example",
        abstract="A substantially more complete abstract for retrieval evaluation.",
        authors=[PaperAuthor(name="Ada Lovelace", institution="Scholar Lab", source_author_ids={"semantic_scholar": "S-A1"})],
        keywords=["Retrieval", "Evaluation"],
        semantic_scholar_id="S2-1",
        is_open_access=True,
        open_access_url="https://example.org/paper",
        raw_rank=4,
    )

    result = PaperFusionService().fuse([openalex, semantic_scholar])  # 执行跨来源身份融合。

    assert result.input_count == 2  # 验证统计保留原始输入数量。
    assert result.fused_count == 1  # 验证相同 DOI 形成单个身份组。
    assert result.merged_count == 1  # 验证一条来源记录被并入融合结果。
    fused = result.papers[0]  # 读取融合后的规范论文。
    assert fused.abstract == semantic_scholar.abstract  # 验证融合保留信息量更高的摘要。
    assert fused.openalex_id == "https://openalex.org/W1"  # 验证 OpenAlex 平台 ID 未被丢弃。
    assert fused.semantic_scholar_id == "S2-1"  # 验证 Semantic Scholar 平台 ID 已补全。
    assert fused.is_open_access is True  # 验证任一来源确认开放即可保留开放状态。
    assert [record.source for record in fused.source_records] == ["openalex", "semantic_scholar"]  # 验证两个来源溯源均被保留。
    assert fused.authors[0].source_author_ids == {"openalex": "A1", "semantic_scholar": "S-A1"}  # 验证作者来源 ID 被合并。
    assert fused.rrf_score == pytest.approx(1 / 62 + 1 / 64)  # 验证按每个来源原始排名计算标准 RRF。
    assert fused.work_family_id is not None  # 验证融合记录获得可追踪版本族标识。


def test_fusion_normalizes_arxiv_versions_and_uses_source_weights() -> None:
    """同一 arXiv 不同版本应融合，并按注入的来源权重计算 RRF。"""
    arxiv = _record("arxiv", "2501.00001", arxiv_id="arXiv:2501.00001v1", raw_rank=1)  # 构造预印本第一版记录。
    semantic_scholar = _record("semantic_scholar", "S2-2", arxiv_id="https://arxiv.org/abs/2501.00001v3", raw_rank=3)  # 构造同一预印本的来源补充记录。

    result = PaperFusionService(rrf_k=10, source_weights={"arxiv": 2.0, "semantic_scholar": 0.5}).fuse([arxiv, semantic_scholar])  # 使用可替换策略执行融合。

    assert result.fused_count == 1  # 验证忽略 arXiv 版本号后能够融合。
    assert result.papers[0].rrf_score == pytest.approx(2 / 11 + 0.5 / 13)  # 验证来源权重仅影响对应来源的 RRF 贡献。


def test_fusion_uses_title_year_and_first_author_only_when_cross_source_ids_are_missing() -> None:
    """无跨源 ID 的相同标题、年份和首作者应融合；有 DOI 的记录不应被标题回退误合并。"""
    title_only_openalex = _record(  # 构造缺少 DOI、arXiv 和 PMID 的 OpenAlex 记录。
        "openalex",
        "W-title",
        title="ＮＦＫＣ   Retrieval Study",
        year=2024,
        authors=[PaperAuthor(name="Grace Hopper")],
    )
    title_only_dblp = _record(  # 构造同标题、同年、同首作者但不同来源的记录。
        "dblp",
        "conf/test/Hopper24",
        title="NFKC Retrieval Study",
        year=2024,
        authors=[PaperAuthor(name="Grace  Hopper")],
    )
    doi_record = _record(  # 构造同标题但具有不同跨源稳定 ID 的记录。
        "semantic_scholar",
        "S-title",
        title="NFKC Retrieval Study",
        year=2024,
        authors=[PaperAuthor(name="Grace Hopper")],
        doi="10.2000/different-work",
    )

    result = PaperFusionService().fuse([title_only_openalex, title_only_dblp, doi_record])  # 执行保守标题回退融合。

    assert result.fused_count == 2  # 验证仅两个缺少跨源 ID 的记录按标题回退融合。
    assert result.merged_count == 1  # 验证标题回退只合并一条重复记录。


def test_fusion_merges_source_records_and_assigns_explicit_linked_work_family() -> None:
    """同一来源 ID 的重复记录应合并命中子查询，并以明确 DOI/arXiv 映射生成同一版本族。"""
    first = _record(  # 构造同时携带 DOI 和 arXiv 的来源记录。
        "semantic_scholar",
        "S-family",
        doi="10.3000/family",
        arxiv_id="2502.00002",
        source_records=[PaperSourceRecord(source="semantic_scholar", external_id="S-family", raw_rank=5, matched_subqueries=["method"])],
    )
    repeated = _record(  # 构造同一平台记录的后续命中。
        "semantic_scholar",
        "S-family",
        doi="10.3000/family",
        arxiv_id="2502.00002v2",
        source_records=[PaperSourceRecord(source="semantic_scholar", external_id="S-family", raw_rank=2, matched_subqueries=["dataset", "method"])],
    )

    result = PaperFusionService().fuse([first, repeated])  # 执行同源 ID 和显式版本标识融合。

    fused = result.papers[0]  # 读取唯一的融合记录。
    assert fused.source_records[0].raw_rank == 2  # 验证保留同一来源中的最佳原始排名。
    assert fused.source_records[0].matched_subqueries == ["method", "dataset"]  # 验证子查询合并去重且保持出现顺序。
    assert fused.work_family_id is not None  # 验证显式 DOI/arXiv 映射产生版本族标识。
    assert result.work_family_count == 1  # 验证统计记录唯一版本族数量。


def test_fusion_links_preprint_and_formal_version_without_merging_identity_records() -> None:
    """标题、首作者和年份跨度均满足严格条件时，预印本与正式版应共享版本族但保持独立记录。"""
    preprint = _record(  # 构造没有 DOI 的预印本记录。
        "arxiv",
        "2503.00003",
        title="Versioned Retrieval",
        year=2024,
        authors=[PaperAuthor(name="Lin Chen")],
        paper_type="preprint",
        arxiv_id="2503.00003",
    )
    conference = _record(  # 构造具有独立 DOI 的会议正式版记录。
        "dblp",
        "conf/test/Chen25",
        title="Versioned Retrieval",
        year=2025,
        authors=[PaperAuthor(name="Lin Chen")],
        paper_type="conference",
        doi="10.4000/versioned-retrieval",
    )

    result = PaperFusionService().fuse([preprint, conference])  # 执行身份融合和版本族关联。

    assert result.fused_count == 2  # 验证不同稳定 ID 的预印本和会议版不会被误融合为同一记录。
    assert result.papers[0].work_family_id == result.papers[1].work_family_id  # 验证两个版本共享同一版本族。
    assert result.work_family_count == 1  # 验证统计以共享版本族计为一个作品。


def test_fusion_returns_empty_result_and_rejects_invalid_rrf_configuration() -> None:
    """空输入应返回零统计，无效 RRF 配置应在服务构造阶段失败。"""
    result = PaperFusionService().fuse([])  # 执行空集合融合。
    assert result.model_dump() == {"papers": [], "input_count": 0, "fused_count": 0, "merged_count": 0, "work_family_count": 0}  # 验证空输入具有稳定输出。
    with pytest.raises(ValueError, match="rrf_k"):  # 断言零平滑常数被明确拒绝。
        PaperFusionService(rrf_k=0)  # 构造无效 RRF 配置。
    with pytest.raises(ValueError, match="source_weights"):  # 断言负来源权重被明确拒绝。
        PaperFusionService(source_weights={"openalex": -1.0})  # 构造无效来源权重配置。
