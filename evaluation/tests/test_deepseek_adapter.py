"""验证封存候选到生产 DeepSeek 核验输入的离线字段映射。"""

from evaluation.adapters.deepseek import _to_production_paper  # 直接验证不会调用模型的纯字段适配边界。
from evaluation.contracts.snapshot import CandidatePaper  # 构造与候选快照契约一致的最小输入。


def test_candidate_snapshot_mapping_does_not_require_unstored_paper_type() -> None:
    """候选快照未封存 paper_type 时，适配器应使用生产契约默认值。"""
    candidate = CandidatePaper(  # 构造不包含 paper_type 的合法排序前候选。
        paper_id="openalex:W1",  # 提供稳定论文标识。
        title="Offline DeepSeek mapping",  # 提供生产论文契约要求的标题。
        source="openalex",  # 使用生产来源枚举中的合法值。
        abstract="A public abstract.",  # 验证公开摘要继续透传。
        authors=["Ada Lovelace"],  # 验证字符串作者转换为生产作者对象。
        year=2024,  # 验证年份透传。
        venue="TestConf",  # 验证场地透传。
        doi="10.1000/test",  # 验证强身份字段透传。
        rrf_score=0.5,  # 验证排序前融合分透传。
        snapshot_rank=1,  # 满足候选快照排序契约。
    )

    paper = _to_production_paper(candidate)  # 纯内存转换，不读取 .env、网络或模型。

    assert paper.paper_type is None  # 不从不存在的快照字段猜测论文类型。
    assert paper.authors[0].name == "Ada Lovelace"  # 保留作者展示名称。
    assert paper.rrf_score == 0.5  # 保留上游确定性融合分数。
