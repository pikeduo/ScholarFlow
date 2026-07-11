"""提供跨学术来源的论文身份解析、字段融合、版本族标识与 RRF 计算。"""

import hashlib  # 为版本族生成不暴露论文标题的稳定短标识。
import math  # 校验可配置的 RRF 参数是否为有限数值。
import re  # 清理 DOI、arXiv 与 PMID 的展示形式。
import unicodedata  # 统一标题和作者名称的 Unicode 表示。
from collections import defaultdict  # 按身份根节点收集论文记录。
from collections.abc import Iterable, Mapping  # 声明可替换的输入集合和来源权重映射。
from datetime import datetime  # 合并来源记录的最近拉取时间。

from backend.app.core.logging import logger  # 记录不包含完整查询和论文文本的融合统计。
from backend.app.models.paper import PaperAuthor, PaperRecord, PaperSourceRecord  # 使用统一论文、作者和来源溯源模型。
from backend.app.models.paper_fusion import PaperFusionResult  # 返回融合后的论文与数量统计。


DEFAULT_RRF_K = 60  # 使用业界常用的平滑常数，调用方可通过构造函数覆盖。


class PaperFusionService:
    """按稳定身份合并 PaperRecord，并保留来源溯源、版本族和 RRF 分数。

    参数：
        rrf_k：RRF 分母中的平滑常数，必须为正整数。
        source_weights：可选的来源权重映射；未配置的来源默认权重为一。
    """

    def __init__(self, rrf_k: int = DEFAULT_RRF_K, source_weights: Mapping[str, float] | None = None) -> None:
        """校验并保存融合策略，避免业务规则散落在调用方。"""
        if rrf_k <= 0:  # RRF 分母必须保持为正以避免无意义的排序分数。
            raise ValueError("rrf_k 必须大于零")  # 在服务构造阶段尽早暴露无效配置。
        self._rrf_k = rrf_k  # 保存可替换的 RRF 平滑常数。
        self._source_weights = self._validate_source_weights(source_weights or {})  # 保存已校验的来源权重副本。

    def fuse(self, papers: Iterable[PaperRecord]) -> PaperFusionResult:
        """对一次多源召回的论文执行身份分组、字段融合和 RRF 计算。

        参数：
            papers：适配器已转换且尚未跨来源融合的论文记录。
        返回：
            PaperFusionResult：包含融合论文、合并数量和版本族统计。
        """
        input_papers = list(papers)  # 固化输入顺序，确保同一输入得到稳定的输出顺序。
        groups = self._build_identity_groups(input_papers)  # 按 DOI、arXiv、PMID、来源 ID 与标题回退形成身份组。
        fused_papers = [self._fuse_group(group) for group in groups]  # 分别合并每个身份组而不丢弃来源溯源。
        fused_papers = self._assign_version_families(fused_papers)  # 对未融合的预印本与正式版本执行保守版本族关联。
        work_family_count = len({paper.work_family_id for paper in fused_papers if paper.work_family_id})  # 统计输出中可识别的版本族。
        result = PaperFusionResult(  # 构造供协调器下一层和 API 层复用的稳定结果。
            papers=fused_papers,  # 保留按首次出现身份组排序的融合记录。
            input_count=len(input_papers),  # 记录融合前的原始记录数量。
            fused_count=len(fused_papers),  # 记录身份组数量。
            merged_count=len(input_papers) - len(fused_papers),  # 记录被合并到其他身份组的重复记录数量。
            work_family_count=work_family_count,  # 记录可展示的版本族数量。
        )
        logger.info(  # 仅记录计数，避免日志输出论文标题、摘要或用户查询。
            "论文融合完成：输入=%d，融合后=%d，合并=%d，版本族=%d",
            result.input_count,
            result.fused_count,
            result.merged_count,
            result.work_family_count,
        )
        return result  # 返回可交给后续规则过滤与排序阶段的融合结果。

    @staticmethod
    def _assign_version_families(papers: list[PaperRecord]) -> list[PaperRecord]:
        """为标题和首作者一致的预印本、正式版本建立共享版本族，而不合并两条论文记录。"""
        candidate_groups: dict[str, list[int]] = defaultdict(list)  # 按保守版本候选键收集已完成身份融合的论文。
        for index, paper in enumerate(papers):  # 遍历每条融合后的规范论文。
            candidate_key = _build_version_candidate_key(paper)  # 提取标题和首作者完全一致的版本候选键。
            if candidate_key is not None:  # 缺少标题、首作者或可识别版本类型时不进行推断。
                candidate_groups[candidate_key].append(index)  # 收集可能属于同一作品的不同版本。
        family_updates: dict[int, str] = {}  # 保存仅通过严格条件确认的论文索引与版本族 ID。
        for candidate_key, indexes in candidate_groups.items():  # 检查每个候选组是否同时包含预印本和正式版本。
            candidate_papers = [papers[index] for index in indexes]  # 读取候选组对应的融合论文。
            paper_types = {paper.paper_type for paper in candidate_papers}  # 收集来源明确提供的论文类型。
            known_years = [paper.year for paper in candidate_papers if paper.year is not None]  # 收集可用于限制版本跨度的已知年份。
            has_preprint = "preprint" in paper_types  # 仅预印本与正式版组合才建立跨身份版本族。
            has_formal_version = bool({"article", "conference"} & paper_types)  # 识别文章或会议等正式发布版本。
            years_are_compatible = len(known_years) == len(candidate_papers) and max(known_years) - min(known_years) <= 2  # 要求年份完整且跨度不超过两年以降低误关联。
            if not has_preprint or not has_formal_version or not years_are_compatible:  # 任一安全条件不满足时不覆盖原有身份族标识。
                continue  # 保持各身份组独立版本族，交由后续人工或更强证据处理。
            family_id = _stable_work_id(f"version:{candidate_key}")  # 使用标题和作者候选键生成不暴露原文的共享版本族 ID。
            for index in indexes:  # 为候选组中的预印本和正式版写入相同版本族。
                family_updates[index] = family_id  # 延迟更新以保持输入列表对象不可变。
        return [paper.model_copy(update={"work_family_id": family_updates.get(index, paper.work_family_id)}) for index, paper in enumerate(papers)]  # 返回仅更新确认版本族后的新记录列表。

    @staticmethod
    def _validate_source_weights(source_weights: Mapping[str, float]) -> dict[str, float]:
        """复制并校验来源权重，防止调用方在融合过程中修改策略。"""
        validated_weights: dict[str, float] = {}  # 保存通过全部边界校验的来源权重。
        for source, weight in source_weights.items():  # 逐个检查外部提供的来源配置。
            normalized_source = source.strip().casefold()  # 统一来源键以避免大小写导致权重失效。
            if not normalized_source:  # 空白来源不能被可靠地应用到任何来源记录。
                raise ValueError("source_weights 不能包含空白来源")  # 拒绝不可解释的配置。
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight < 0:  # 仅允许非负有限数值。
                raise ValueError("source_weights 必须使用非负有限数值")  # 在调用前阻止异常 RRF 分数。
            validated_weights[normalized_source] = float(weight)  # 复制为统一浮点值避免外部映射变动。
        return validated_weights  # 返回独立且已规范化的权重映射。

    def _build_identity_groups(self, papers: list[PaperRecord]) -> list[list[PaperRecord]]:
        """按稳定身份键建立并查集分组，并仅在缺少跨源 ID 时使用标题回退。"""
        parent = list(range(len(papers)))  # 初始化每条输入记录为独立的身份组。
        key_to_index: dict[str, int] = {}  # 保存每个身份键最早出现的记录位置。

        def find(index: int) -> int:
            """查找并压缩当前记录所属身份组的根节点。"""
            while parent[index] != index:  # 沿父节点向上查找组根。
                parent[index] = parent[parent[index]]  # 执行路径压缩以降低后续查找成本。
                index = parent[index]  # 继续移动到压缩后的父节点。
            return index  # 返回当前身份组根节点。

        def union(left: int, right: int) -> None:
            """稳定合并两个身份组，并保留首次出现记录作为根节点。"""
            left_root = find(left)  # 查找左侧记录的当前组根。
            right_root = find(right)  # 查找右侧记录的当前组根。
            if left_root == right_root:  # 相同身份组无需重复合并。
                return  # 保持现有根节点不变。
            if left_root < right_root:  # 使用较小索引作为根以保持输入顺序稳定。
                parent[right_root] = left_root  # 将后出现组并入先出现组。
            else:  # 右侧组更早出现时交换合并方向。
                parent[left_root] = right_root  # 将后出现组并入先出现组。

        for index, paper in enumerate(papers):  # 按输入顺序处理每条来源记录。
            for identity_key in _identity_keys(paper):  # 提取可用于确认同一论文的稳定键。
                existing_index = key_to_index.get(identity_key)  # 查询该身份键是否已在此前记录出现。
                if existing_index is None:  # 首次出现的身份键建立索引。
                    key_to_index[identity_key] = index  # 保存最早记录以维持稳定合并方向。
                else:  # 该键已出现表示两个记录可安全合并。
                    union(index, existing_index)  # 合并当前记录和此前记录所属的身份组。

        grouped_records: dict[int, list[PaperRecord]] = defaultdict(list)  # 按压缩后的根节点收集每组记录。
        for index, paper in enumerate(papers):  # 再次按输入顺序保证组内来源顺序稳定。
            grouped_records[find(index)].append(paper)  # 将记录放入所属身份组。
        return [grouped_records[root] for root in sorted(grouped_records)]  # 按首次出现根节点返回稳定身份组顺序。

    def _fuse_group(self, records: list[PaperRecord]) -> PaperRecord:
        """选择规范记录并合并身份组内的互补元数据与溯源信息。"""
        canonical = max(enumerate(records), key=lambda item: (_record_quality(item[1]), -item[0]))[1]  # 选择元数据更完整且同分时更早出现的记录。
        source_records = _merge_source_records(records)  # 合并并去重来源排名、命中子查询和拉取时间。
        authors = _merge_authors(records)  # 合并作者来源 ID 与可补全的机构信息。
        references = _merge_distinct_text(record.references for record in records)  # 合并真实引用标识并保持出现顺序。
        keywords = _merge_distinct_text(record.keywords for record in records)  # 合并关键词以提高后续语义排序文本完整度。
        work_family_id = _build_work_family_id(records, canonical)  # 仅根据来源明确提供的稳定标识生成版本族。
        return canonical.model_copy(  # 以规范记录为基线，避免删除兼容的已有公开字段。
            update={
                "abstract": _choose_longest_text(record.abstract for record in records),  # 优先保留信息量更高的公开摘要。
                "authors": authors,  # 使用融合后的作者与来源作者 ID。
                "year": _choose_year(canonical, records),  # 优先保留规范记录年份，缺失时再补全。
                "venue": _choose_preferred_text(canonical.venue, (record.venue for record in records)),  # 补全期刊或会议名称。
                "doi": _choose_preferred_text(canonical.doi, (record.doi for record in records)),  # 保留可追溯的 DOI 展示值。
                "arxiv_id": _choose_preferred_text(canonical.arxiv_id, (record.arxiv_id for record in records)),  # 保留预印本标识。
                "pmid": _choose_preferred_text(canonical.pmid, (record.pmid for record in records)),  # 保留医学论文标识。
                "citation_count": max(record.citation_count for record in records),  # 使用各来源中最大的非负引用计数。
                "references": references,  # 保留去重后的真实引用关系。
                "keywords": keywords,  # 保留跨来源互补关键词。
                "paper_type": _choose_paper_type(canonical, records),  # 优先保存更正式且更完整的论文类型。
                "openalex_id": _choose_preferred_text(canonical.openalex_id, (record.openalex_id for record in records)),  # 补全 OpenAlex 来源标识。
                "semantic_scholar_id": _choose_preferred_text(canonical.semantic_scholar_id, (record.semantic_scholar_id for record in records)),  # 补全 Semantic Scholar 来源标识。
                "dblp_key": _choose_preferred_text(canonical.dblp_key, (record.dblp_key for record in records)),  # 补全 DBLP 来源标识。
                "is_open_access": _merge_open_access(record.is_open_access for record in records),  # 任一来源确认开放时保留肯定结论。
                "open_access_url": _choose_preferred_text(canonical.open_access_url, (record.open_access_url for record in records)),  # 补全合法开放访问链接。
                "source_records": source_records,  # 保存全部来源溯源而非保留首次记录。
                "work_family_id": work_family_id,  # 写入稳定版本族标识供后续图谱使用。
                "rrf_score": self._calculate_rrf(source_records),  # 根据每个来源的最佳原始排名计算融合分数。
                "updated_at": _latest_datetime(record.updated_at for record in records),  # 保留融合记录中最新的规范化时间。
            }
        )

    def _calculate_rrf(self, source_records: list[PaperSourceRecord]) -> float:
        """按每个来源的最佳原始排名计算可配置权重的 Reciprocal Rank Fusion 分数。"""
        best_ranks: dict[str, int] = {}  # 保存每个来源在当前身份组中的最佳原始排名。
        for source_record in source_records:  # 遍历融合后所有来源溯源记录。
            if source_record.raw_rank is None:  # 未提供原始排名时不能伪造 RRF 贡献。
                continue  # 保留溯源但跳过分数计算。
            source_name = source_record.source.casefold()  # 统一来源名称以匹配可配置权重。
            previous_rank = best_ranks.get(source_name)  # 查询该来源此前的最佳排名。
            if previous_rank is None or source_record.raw_rank < previous_rank:  # 仅保留数值更小的最佳名次。
                best_ranks[source_name] = source_record.raw_rank  # 更新该来源对融合论文的最佳贡献。
        return sum(  # 汇总各来源的独立排名贡献。
            self._source_weights.get(source_name, 1.0) / (self._rrf_k + raw_rank)  # 使用配置权重和标准 RRF 分母。
            for source_name, raw_rank in best_ranks.items()  # 每个来源最多贡献一次，避免同源重复命中放大分数。
        )


def _identity_keys(paper: PaperRecord) -> set[str]:
    """返回论文的稳定身份键；标题键只用于缺少 DOI、arXiv 与 PMID 的记录。"""
    keys = {f"source:{paper.source}:{_normalize_text(paper.paper_id)}"}  # 始终保留同源平台主标识以识别来源重复响应。
    cross_source_keys: set[str] = set()  # 保存可跨来源确认同一论文的稳定身份键。
    if paper.doi and (normalized_doi := _normalize_doi(paper.doi)):  # DOI 是最高优先级的跨来源稳定标识。
        cross_source_keys.add(f"doi:{normalized_doi}")  # 加入规范化 DOI 身份键。
    if paper.arxiv_id and (normalized_arxiv_id := _normalize_arxiv_id(paper.arxiv_id)):  # arXiv 标识可确认同一预印本。
        cross_source_keys.add(f"arxiv:{normalized_arxiv_id}")  # 加入忽略版本号的预印本身份键。
    if paper.pmid and (normalized_pmid := _normalize_pmid(paper.pmid)):  # PMID 可确认同一生物医学论文。
        cross_source_keys.add(f"pmid:{normalized_pmid}")  # 加入规范化 PMID 身份键。
    for source_record in paper.source_records:  # 额外检查适配器已经保留的来源专有稳定标识。
        external_id = _normalize_text(source_record.external_id)  # 统一来源 ID 的空白与大小写表示。
        if external_id:  # 空白外部标识不应形成错误身份组。
            keys.add(f"source:{source_record.source}:{external_id}")  # 同一来源相同平台 ID 必须合并。
    for source_name, source_id in (
        ("openalex", paper.openalex_id),  # 显式检查 OpenAlex Work 标识。
        ("semantic_scholar", paper.semantic_scholar_id),  # 显式检查 Semantic Scholar 论文标识。
        ("dblp", paper.dblp_key),  # 显式检查 DBLP 出版物键。
    ):
        if source_id and (normalized_source_id := _normalize_text(source_id)):  # 仅为非空显式来源 ID 建立键。
            keys.add(f"source:{source_name}:{normalized_source_id}")  # 兼容旧记录尚未写入 source_records 的情况。
    if not cross_source_keys:  # 缺少 DOI、arXiv 与 PMID 时才允许标题回退参与跨来源身份判断。
        title_key = _build_title_key(paper)  # 使用标题、年份和首作者组成保守回退键。
        if title_key is not None:  # 标题或年份或作者不足时不应凭空合并。
            keys.add(f"title:{title_key}")  # 将保守标题键加入当前身份集合。
    return keys | cross_source_keys  # 同时返回来源内和跨来源的稳定身份键。


def _normalize_doi(doi: str) -> str:
    """规范化 DOI 的 URL、前缀、大小写和无语义尾部标点。"""
    normalized = doi.strip().casefold()  # 移除展示空白并消除 DOI 大小写差异。
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)  # 移除常见 DOI URL 前缀。
    normalized = normalized.removeprefix("doi:").strip()  # 移除常见 DOI 标签并清理其后空白。
    return normalized.rstrip("/.,;:)]}")  # 移除 URL 斜杠和引用文本常见尾部标点。


def _normalize_arxiv_id(arxiv_id: str) -> str:
    """规范化 arXiv 标识并忽略同一预印本的版本后缀。"""
    normalized = arxiv_id.strip().casefold()  # 移除空白并统一大小写。
    normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized)  # 兼容 arXiv 摘要和 PDF URL。
    normalized = normalized.removeprefix("arxiv:")  # 移除展示性 arXiv 前缀。
    normalized = normalized.removesuffix(".pdf")  # 兼容 PDF URL 末尾扩展名。
    return re.sub(r"v\d+$", "", normalized)  # 将同一预印本不同版本归为同一稳定标识。


def _normalize_pmid(pmid: str) -> str:
    """规范化 PMID 的 URL、前缀、空白与尾部斜杠。"""
    normalized = pmid.strip().casefold()  # 移除空白并消除大小写差异。
    normalized = re.sub(r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/", "", normalized)  # 移除 PubMed URL 前缀。
    normalized = normalized.removeprefix("pmid:").strip()  # 移除常见 PMID 标签。
    return normalized.rstrip("/.,;:)]}")  # 忽略展示文本中无语义的尾部符号。


def _normalize_text(value: str) -> str:
    """使用 Unicode NFKC、大小写折叠和空白压缩生成可比较文本。"""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())  # 统一兼容字符、大小写和连续空白。


def _build_title_key(paper: PaperRecord) -> str | None:
    """为缺少跨源稳定 ID 的论文建立标题、年份和首作者回退键。"""
    normalized_title = _normalize_text(paper.title)  # 规范化标题以消除 Unicode、大小写和空白差异。
    first_author = _normalize_text(paper.authors[0].name) if paper.authors else ""  # 仅使用首作者降低同题不同论文的误合并风险。
    if not normalized_title or paper.year is None or not first_author:  # 缺少任一关键维度时不能安全使用标题回退。
        return None  # 交由后续来源或人工核验，而不是激进合并。
    return f"{normalized_title}|{paper.year}|{first_author}"  # 返回可稳定比较的保守回退键。


def _build_version_candidate_key(paper: PaperRecord) -> str | None:
    """为预印本与正式版本关联生成严格的标题和首作者候选键。"""
    if paper.paper_type not in {"article", "conference", "preprint"}:  # 仅对可明确区分版本角色的论文类型建立候选。
        return None  # 综述或未知类型不在本阶段推断版本关系。
    normalized_title = _normalize_text(paper.title)  # 规范化标题以避免 Unicode、大小写和空白差异。
    first_author = _normalize_text(paper.authors[0].name) if paper.authors else ""  # 使用首作者降低同标题不同论文误关联风险。
    if not normalized_title or not first_author:  # 缺少任一关键证据时不建立版本候选。
        return None  # 保持保守策略，不从不完整元数据推断版本关系。
    return f"{normalized_title}|{first_author}"  # 让不同年份的同一作品版本进入同一候选组。


def _record_quality(record: PaperRecord) -> tuple[int, int, int, int, int, int]:
    """计算规范记录选择所需的元数据完整度，不将引用量当作相关性分数。"""
    return (  # 返回按字段可用性优先的字典序质量元组。
        int(bool(record.doi)),  # DOI 可提高跨来源身份与正式出版版本的可追溯性。
        int(record.paper_type in {"article", "conference"}),  # 正式文章或会议版优先于预印本作为默认展示记录。
        int(bool(record.abstract.strip())),  # 有摘要可支持后续语义排序与证据核验。
        int(bool(record.venue and record.venue.strip())),  # 有 venue 可支持展示和规则过滤。
        int(bool(record.authors)),  # 有作者可支持标题回退与展示。
        int(bool(record.open_access_url)),  # 有合法开放链接可提升结果可用性。
    )


def _merge_source_records(records: list[PaperRecord]) -> list[PaperSourceRecord]:
    """按来源和外部 ID 合并来源溯源信息，并补齐缺失的主来源记录。"""
    merged: dict[tuple[str, str], PaperSourceRecord] = {}  # 使用来源与外部 ID 作为溯源记录唯一键。
    for record in records:  # 按身份组输入顺序处理每条来源记录。
        candidate_records = list(record.source_records)  # 复制来源记录以避免修改原始 Pydantic 列表。
        if not any(item.source == record.source and item.external_id == record.paper_id for item in candidate_records):  # 兼容旧记录遗漏主来源溯源的情况。
            candidate_records.append(PaperSourceRecord(source=record.source, external_id=record.paper_id))  # 用不含伪造排名的最小记录补齐主来源。
        for source_record in candidate_records:  # 逐条合并来源命中信息。
            key = (source_record.source, source_record.external_id)  # 以来源和原始稳定 ID 判断同一溯源记录。
            existing = merged.get(key)  # 查询当前来源 ID 是否已出现。
            if existing is None:  # 首次出现时保留副本避免修改输入对象。
                merged[key] = source_record.model_copy(deep=True)  # 复制嵌套列表和时间字段以保持输入不可变。
                continue  # 无需执行后续字段合并。
            merged[key] = existing.model_copy(  # 保留来源和外部 ID，同时更新可互补字段。
                update={
                    "raw_rank": _minimum_rank(existing.raw_rank, source_record.raw_rank),  # 使用同一来源中更靠前的原始排名。
                    "matched_subqueries": _merge_distinct_text([existing.matched_subqueries, source_record.matched_subqueries]),  # 合并命中子查询并保持顺序。
                    "fetched_at": _latest_datetime([existing.fetched_at, source_record.fetched_at]),  # 写入最新成功拉取时间。
                }
            )
    return list(merged.values())  # 字典插入顺序保留首次出现来源顺序。


def _merge_authors(records: list[PaperRecord]) -> list[PaperAuthor]:
    """按 ORCID 或规范化姓名合并作者，并保留各来源作者标识。"""
    merged: dict[str, PaperAuthor] = {}  # 保存作者身份键到融合作者的映射。
    for record in records:  # 遍历每条融合候选记录的作者列表。
        for author in record.authors:  # 按原始作者顺序处理以保证展示稳定。
            author_key = f"orcid:{author.orcid.casefold()}" if author.orcid else f"name:{_normalize_text(author.name)}"  # ORCID 优先，缺失时保守使用规范化姓名。
            existing = merged.get(author_key)  # 查询该作者是否已在此前来源出现。
            if existing is None:  # 首次出现作者直接深复制保留原始展示名。
                merged[author_key] = author.model_copy(deep=True)  # 避免修改输入模型的来源 ID 映射。
                continue  # 无需合并字段。
            merged[author_key] = existing.model_copy(  # 保留首次展示名并填充互补身份信息。
                update={
                    "orcid": existing.orcid or author.orcid,  # 缺失时从后续来源补充 ORCID。
                    "institution": existing.institution or author.institution,  # 缺失时从后续来源补充机构。
                    "source_author_ids": {**existing.source_author_ids, **author.source_author_ids},  # 保存两个来源提供的作者稳定标识。
                }
            )
    return list(merged.values())  # 保持首次出现作者顺序供前端稳定展示。


def _merge_distinct_text(text_groups: Iterable[Iterable[str]]) -> list[str]:
    """合并多个文本列表，按规范化文本去重并保留首次出现的展示值。"""
    merged_texts: list[str] = []  # 保存去重后仍保留原始展示形式的文本。
    seen_texts: set[str] = set()  # 保存不区分大小写和空白的比较键。
    for text_group in text_groups:  # 遍历每个来源的文本列表。
        for text in text_group:  # 遍历列表内每条文本。
            normalized_text = _normalize_text(text)  # 生成用于去重的稳定比较键。
            if not normalized_text or normalized_text in seen_texts:  # 忽略空白文本和重复文本。
                continue  # 保留最早出现的展示值。
            seen_texts.add(normalized_text)  # 标记该规范化文本已被保留。
            merged_texts.append(text.strip())  # 保留去除首尾空白后的原始展示文本。
    return merged_texts  # 返回顺序稳定的去重文本列表。


def _choose_longest_text(values: Iterable[str]) -> str:
    """从多个候选文本中选择最长的非空值，以优先保留摘要信息量。"""
    candidates = [value.strip() for value in values if value.strip()]  # 丢弃空白文本并清理展示空白。
    return max(candidates, key=len) if candidates else ""  # 缺失全部文本时保持模型约定的空摘要。


def _choose_preferred_text(preferred: str | None, alternatives: Iterable[str | None]) -> str | None:
    """优先保留规范记录字段，缺失时按输入顺序选择首个非空补充值。"""
    if preferred and preferred.strip():  # 规范记录已有值时不静默覆盖其展示形式。
        return preferred.strip()  # 返回清理过首尾空白的规范记录值。
    for alternative in alternatives:  # 依次检查其余来源提供的可选字段。
        if alternative and alternative.strip():  # 仅使用实际存在的非空字段。
            return alternative.strip()  # 返回首个可用补充值以保持稳定性。
    return None  # 全部来源缺失时明确保留未知状态。


def _choose_year(canonical: PaperRecord, records: list[PaperRecord]) -> int | None:
    """优先保留规范记录年份，缺失时选择最早的已知年份作为首次公开年份近似值。"""
    if canonical.year is not None:  # 规范记录提供年份时不在融合阶段猜测更优版本。
        return canonical.year  # 保持规范记录年份。
    known_years = [record.year for record in records if record.year is not None]  # 收集所有有效年份供缺失补全。
    return min(known_years) if known_years else None  # 缺失全部年份时保持未知。


def _choose_paper_type(canonical: PaperRecord, records: list[PaperRecord]) -> str | None:
    """优先选择正式版本类型，避免默认展示预印本而忽略已知正式版本。"""
    type_priority = {"article": 3, "conference": 2, "preprint": 1, "review": 1}  # 定义不涉及相关性的版本展示优先级。
    candidates = [record.paper_type for record in records if record.paper_type is not None]  # 收集来源明确提供的基础论文类型。
    if canonical.paper_type is not None and canonical.paper_type in type_priority:  # 规范记录类型可识别时也参与优先级比较。
        candidates.append(canonical.paper_type)  # 重复加入不会改变最大值，只保证规范值被考虑。
    return max(candidates, key=lambda value: type_priority[value]) if candidates else None  # 返回正式程度更高的已知类型。


def _merge_open_access(values: Iterable[bool | None]) -> bool | None:
    """合并三态开放获取信息；任一来源确认开放时优先保留真值。"""
    known_values = [value for value in values if value is not None]  # 忽略来源未确认的空值。
    if not known_values:  # 没有任何来源给出开放状态时不能推断。
        return None  # 保留三态未知。
    return any(known_values)  # 任一来源确认开放即提供用户可用的开放结论。


def _minimum_rank(left: int | None, right: int | None) -> int | None:
    """返回两个可选来源排名中数值更小的有效排名。"""
    ranks = [rank for rank in (left, right) if rank is not None]  # 过滤未提供原始排名的来源记录。
    return min(ranks) if ranks else None  # 两者均缺失时不虚构排名。


def _latest_datetime(values: Iterable[datetime | None]) -> datetime | None:
    """返回多个可选时间中的最新值，全部缺失时保持空值。"""
    candidates = [value for value in values if value is not None]  # 过滤来源未提供的时间。
    return max(candidates) if candidates else None  # 使用时间对象自然顺序选择最新时间。


def _build_work_family_id(records: list[PaperRecord], canonical: PaperRecord) -> str:
    """根据来源明确提供的稳定标识生成保守、稳定且不泄露标题的版本族 ID。"""
    family_keys: list[str] = []  # 按稳定性优先级收集可解释的版本族依据。
    for record in records:  # 遍历同一身份组内的所有来源记录。
        if record.doi and (doi := _normalize_doi(record.doi)):  # DOI 是正式版本最稳定的版本族依据。
            family_keys.append(f"doi:{doi}")  # 记录规范化 DOI。
        if record.arxiv_id and (arxiv_id := _normalize_arxiv_id(record.arxiv_id)):  # arXiv 标识可关联同一预印本版本。
            family_keys.append(f"arxiv:{arxiv_id}")  # 记录忽略版本后缀的预印本 ID。
        if record.pmid and (pmid := _normalize_pmid(record.pmid)):  # PMID 可关联生物医学版本元数据。
            family_keys.append(f"pmid:{pmid}")  # 记录规范化 PMID。
    if not family_keys:  # 没有跨源稳定标识时退回当前规范记录的来源内稳定 ID。
        family_keys.append(f"source:{canonical.source}:{_normalize_text(canonical.paper_id)}")  # 仍为同源重复提供稳定族标识。
    return _stable_work_id("|".join(sorted(set(family_keys))))  # 使用排序去重后的稳定键构造确定性版本族标识。


def _stable_work_id(seed: str) -> str:
    """将版本族依据转换为不直接暴露 DOI、标题或作者的短哈希标识。"""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]  # 使用 UTF-8 固化跨平台一致的哈希输入与短摘要。
    return f"work-{digest}"  # 返回供 API、图谱和持久化层使用的稳定版本族 ID。
