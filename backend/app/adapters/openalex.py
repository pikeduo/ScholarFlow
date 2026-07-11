"""将 OpenAlex Work JSON 响应转换为 ScholarFlow 的统一论文模型。"""

from collections.abc import Mapping  # 安全识别嵌套 JSON 对象。

from backend.app.models.paper import Paper, PaperAuthor  # 复用统一的论文领域模型。


class OpenAlexMappingError(ValueError):
    """表示 OpenAlex 响应缺少生成统一论文所必需的数据。"""


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将 JSON 值安全转换为字符串键的映射对象。

    参数：
        value：待检查的 JSON 字段值。
    返回：
        Mapping[str, object] | None：可用映射或空值。
    """
    return value if isinstance(value, Mapping) else None  # 拒绝列表、字符串和空值等非对象字段。


def _optional_text(value: object) -> str | None:
    """提取去除首尾空白后的非空文本。

    参数：
        value：待转换的 JSON 字段值。
    返回：
        str | None：有效文本或空值。
    """
    if not isinstance(value, str):  # 非字符串不能作为统一文本字段。
        return None  # 以空值表示字段缺失或类型异常。
    normalized_text = value.strip()  # 去除数据源可能带入的展示空白。
    return normalized_text or None  # 将空字符串统一视为缺失。


def _required_text(work: Mapping[str, object], field_name: str) -> str:
    """读取 OpenAlex Work 的必要文本字段。

    参数：
        work：OpenAlex Work JSON 对象。
        field_name：必须存在的字段名。
    返回：
        str：已校验的字段文本。
    异常：
        OpenAlexMappingError：字段缺失、类型错误或为空时抛出。
    """
    text_value = _optional_text(work.get(field_name))  # 读取并规范化必要字段。
    if text_value is None:  # 必要字段无法用于构造统一模型时立即失败。
        raise OpenAlexMappingError(f"OpenAlex Work 缺少有效字段：{field_name}")  # 返回可定位的映射错误。
    return text_value  # 返回已通过校验的文本。


def _restore_abstract(work: Mapping[str, object]) -> str:
    """根据 OpenAlex 的摘要倒排索引还原阅读顺序文本。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        str：还原后的摘要；字段缺失或结构异常时为空字符串。
    """
    inverted_index = _as_mapping(work.get("abstract_inverted_index"))  # 读取 OpenAlex 的词语位置映射。
    if inverted_index is None:  # OpenAlex 可以合法地不提供摘要。
        return ""  # 以空摘要保持论文记录可用。
    words_by_position: dict[int, str] = {}  # 根据词语位置重建原始顺序。
    for word, positions in inverted_index.items():  # 遍历每个词语与其出现位置。
        if not isinstance(word, str) or not isinstance(positions, list):  # 跳过不符合 API 结构的条目。
            continue  # 避免单个异常字段阻断整篇论文映射。
        for position in positions:  # 处理词语在摘要中的每次出现。
            if isinstance(position, int) and position >= 0:  # 仅接受合法的非负位置。
                words_by_position.setdefault(position, word)  # 保留首次出现的词语以处理异常重复位置。
    return " ".join(words_by_position[position] for position in sorted(words_by_position))  # 按位置排序生成摘要文本。


def _extract_authors(work: Mapping[str, object]) -> list[PaperAuthor]:
    """从 OpenAlex authorships 字段提取规范化作者列表。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        list[PaperAuthor]：保留可识别作者与首个机构信息的列表。
    """
    authorships = work.get("authorships")  # 读取作者署名数组。
    if not isinstance(authorships, list):  # 缺失作者信息不应阻断论文检索。
        return []  # 以空作者列表表示来源未提供数据。
    authors: list[PaperAuthor] = []  # 累积可构造的作者模型。
    for authorship in authorships:  # 按 OpenAlex 返回顺序处理作者。
        authorship_data = _as_mapping(authorship)  # 将当前署名条目转换为可读取对象。
        if authorship_data is None:  # 忽略结构异常的署名条目。
            continue  # 继续处理其他合法作者。
        author_data = _as_mapping(authorship_data.get("author"))  # 读取嵌套作者对象。
        if author_data is None:  # 缺少作者对象时无法建立作者记录。
            continue  # 忽略该异常署名。
        author_name = _optional_text(author_data.get("display_name"))  # 提取作者显示名称。
        if author_name is None:  # 姓名是作者模型的必要字段。
            continue  # 跳过无法识别的作者。
        institution_name: str | None = None  # 默认不绑定机构信息。
        institutions = authorship_data.get("institutions")  # 读取作者机构数组。
        if isinstance(institutions, list) and institutions:  # 仅在至少存在一个机构时继续解析。
            first_institution = _as_mapping(institutions[0])  # 取首个机构作为简洁的当前归属。
            if first_institution is not None:  # 确认机构对象结构有效。
                institution_name = _optional_text(first_institution.get("display_name"))  # 提取机构显示名称。
        authors.append(  # 写入规范化作者记录。
            PaperAuthor(  # 构造统一作者模型。
                name=author_name,  # 使用必填的作者显示名称。
                orcid=_optional_text(author_data.get("orcid")),  # 保留可选 ORCID。
                institution=institution_name,  # 保留可选首个机构名称。
            )
        )
    return authors  # 返回保持原始作者顺序的列表。


def _extract_venue(work: Mapping[str, object]) -> str | None:
    """从 OpenAlex primary_location 提取期刊或会议名称。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        str | None：来源显示名称或空值。
    """
    primary_location = _as_mapping(work.get("primary_location"))  # 读取主发布位置对象。
    if primary_location is None:  # 预印本或不完整元数据可能没有发布位置。
        return None  # 保持期刊字段为空。
    source_data = _as_mapping(primary_location.get("source"))  # 读取主发布位置中的来源对象。
    if source_data is None:  # 缺少来源对象时无法判断期刊或会议。
        return None  # 保持期刊字段为空。
    return _optional_text(source_data.get("display_name"))  # 返回期刊或会议显示名称。


def _extract_references(work: Mapping[str, object]) -> list[str]:
    """从 OpenAlex referenced_works 提取有效的被引论文标识。

    参数：
        work：OpenAlex Work JSON 对象。
    返回：
        list[str]：保持数据源顺序的非空 OpenAlex Work ID 列表。
    """
    references = work.get("referenced_works")  # 读取引文关系数组。
    if not isinstance(references, list):  # 缺失引用列表是合法的部分响应。
        return []  # 使用空列表表示无可用引用关系。
    return [reference for reference in references if _optional_text(reference) is not None]  # 过滤空白或非文本标识。


def map_openalex_work_to_paper(work: Mapping[str, object]) -> Paper:
    """将一条 OpenAlex Work 响应转换为统一论文模型。

    参数：
        work：已经由 HTTP 客户端解码的 OpenAlex Work JSON 对象。
    返回：
        Paper：可被去重、排序和存储模块复用的规范化论文。
    异常：
        OpenAlexMappingError：Work 缺少有效 id 或 title 时抛出。
    """
    identifiers = _as_mapping(work.get("ids"))  # 读取可能包含 DOI 的外部标识对象。
    cited_by_count = work.get("cited_by_count")  # 读取 OpenAlex 提供的引用计数。
    publication_year = work.get("publication_year")  # 读取可选发表年份。
    return Paper(  # 构造统一论文领域模型并交由 Pydantic 二次校验。
        paper_id=_required_text(work, "id"),  # 使用 OpenAlex Work ID 作为来源内稳定标识。
        title=_required_text(work, "title"),  # 使用 API 规定的论文标题字段。
        abstract=_restore_abstract(work),  # 还原倒排索引格式的摘要。
        authors=_extract_authors(work),  # 规范化作者和机构信息。
        year=publication_year if isinstance(publication_year, int) and not isinstance(publication_year, bool) else None,  # 忽略无效年份类型。
        venue=_extract_venue(work),  # 提取主发布位置的期刊或会议名称。
        doi=_optional_text(work.get("doi")) or (_optional_text(identifiers.get("doi")) if identifiers else None),  # 兼容顶层与 ids 中的 DOI。
        citation_count=max(cited_by_count, 0) if isinstance(cited_by_count, int) and not isinstance(cited_by_count, bool) else 0,  # 防御异常负数或类型。
        references=_extract_references(work),  # 保留 OpenAlex 提供的被引 Work ID。
        source="openalex",  # 标记统一模型的当前数据源。
    )
