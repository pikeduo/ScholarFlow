"""提供只从已保存搜索结果读取的版本化论文详情接口。"""

from typing import Annotated, Literal  # 为 FastAPI 依赖注入声明清晰类型与翻译字段范围。

from fastapi import APIRouter, Depends, HTTPException, Query, status  # 声明论文读取与按需翻译路由。
from sqlalchemy.orm import Session  # 标注文献库论文快照读取依赖。

from backend.app.adapters.deepseek_translation import DeepSeekPaperTranslationClient, PaperTranslationClient, PaperTranslationError  # 隔离 DeepSeek 翻译调用与可替换测试边界。
from backend.app.api.routes.search import get_search_run_state_store  # 复用 SQLite 搜索结果存储装配，避免新增基础设施。
from backend.app.api.routes.library import get_database_session  # 复用请求级 SQLite 会话，避免文献库详情读取创建独立连接。
from backend.app.core.logging import logger  # 记录存储边界异常的完整堆栈。
from backend.app.models.paper import PaperRecord  # 返回统一的规范化论文领域契约。
from backend.app.models.paper_translation import PaperTranslationResponse  # 返回稳定的中文翻译响应。
from backend.app.services.saved_paper_resolver import SavedPaperResolver  # 统一搜索快照优先、文献库回退的只读解析规则。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离 SQLite 访问并映射公共错误。
from backend.app.repositories.library import LibraryRepository  # 从用户明确收藏的本地论文快照补充详情和翻译读取边界。
from backend.app.services.paper_translation_store import PaperTranslationStore, SqlitePaperTranslationStore  # 通过 SQLite 缓存跨浏览器复用字段级译文。
from backend.app.services.resource_translation import ResourceTranslationService  # 统一执行缓存命中、模型调用和缓存降级流程。


router = APIRouter(prefix="/papers")  # 将论文资源归入固定版本化路径。
paper_translation_client: PaperTranslationClient = DeepSeekPaperTranslationClient()  # 复用无状态翻译适配器且允许测试覆盖依赖。
paper_translation_store: PaperTranslationStore = SqlitePaperTranslationStore()  # 复用字段级 SQLite 缓存服务且允许测试覆盖依赖。


def get_paper_translation_client() -> PaperTranslationClient:
    """返回当前用于按需论文翻译的 DeepSeek 适配器。"""
    return paper_translation_client  # 通过依赖注入隔离真实网络调用。


def get_paper_translation_store() -> PaperTranslationStore:
    """返回当前用于跨浏览器复用译文的 SQLite 缓存服务。"""
    return paper_translation_store  # 通过依赖注入隔离持久化实现并支持离线测试替换。


def get_library_paper_repository(session: Annotated[Session, Depends(get_database_session)]) -> LibraryRepository:
    """提供只读文献库论文快照仓储，供详情和翻译安全回退。"""
    return LibraryRepository(session)  # 复用文献库既有身份与 JSON 快照边界。


@router.get("/detail", response_model=PaperRecord, status_code=status.HTTP_200_OK, summary="读取已保存论文详情")
def get_paper_detail(
    paper_id: Annotated[str, Query(min_length=1)],
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
    library_repository: Annotated[LibraryRepository, Depends(get_library_paper_repository)],
) -> PaperRecord:
    """按内部论文标识读取 SQLite 中最新保存的规范化详情。

    参数：
        paper_id：搜索最终结果提供的稳定论文标识。
        state_store：可替换的搜索结果快照读取适配层。
    返回：
        PaperRecord：可安全展示的论文事实、标识符、来源和核验证据。
    异常：
        HTTPException：论文不存在时返回 404，存储故障时返回 503。
    """
    normalized_paper_id = paper_id.strip()  # 拒绝仅由空白组成的无效资源标识。
    if not normalized_paper_id:  # 防止空路径参数进入 SQLite 扫描。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="论文详情不存在或尚未保存")  # 保持未知标识的稳定公共语义。
    try:  # 将 SQLite 与 JSON 解析异常隔离在服务边界后处理。
        paper = SavedPaperResolver(state_store, library_repository).get_paper(normalized_paper_id)  # 统一读取搜索优先、文献库回退的本地快照，绝不触发外部学术来源。
    except SearchRunStoreError:  # 不将数据库路径、SQL 或快照正文泄露给客户端。
        logger.exception("论文详情读取接口失败：论文=%s", normalized_paper_id)  # 只记录安全内部标识和堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="论文详情暂时不可用，请稍后重试") from None  # 返回可重试的公共提示。
    if paper is None:  # 未被任何已完成搜索保存的论文不能由前端伪造读取。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="论文详情不存在或尚未保存")  # 保持不存在与尚未保存的同一安全语义。
    return paper  # 返回统一 PaperRecord，不额外查询供应商 API。


@router.get("/{paper_id}", response_model=PaperRecord, status_code=status.HTTP_200_OK, include_in_schema=False)
def get_legacy_paper_detail(
    paper_id: str,
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
    library_repository: Annotated[LibraryRepository, Depends(get_library_paper_repository)],
) -> PaperRecord:
    """兼容旧版无斜杠论文标识的详情路径，新的前端必须使用查询参数入口。

    参数：
        paper_id：不含路径分隔符的历史论文标识。
        state_store：可替换的搜索结果快照读取适配层。
    返回：
        PaperRecord：已保存的规范化论文事实。
    """
    return get_paper_detail(paper_id=paper_id, state_store=state_store, library_repository=library_repository)  # 复用相同读取边界，避免旧路径出现行为分叉。


@router.post("/translation/{field}", response_model=PaperTranslationResponse, status_code=status.HTTP_200_OK, summary="翻译已保存论文标题或摘要")
async def translate_paper(
    field: Literal["title", "abstract"],
    paper_id: Annotated[str, Query(min_length=1)],
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
    library_repository: Annotated[LibraryRepository, Depends(get_library_paper_repository)],
    translation_client: Annotated[PaperTranslationClient, Depends(get_paper_translation_client)],
    translation_store: Annotated[PaperTranslationStore, Depends(get_paper_translation_store)],
) -> PaperTranslationResponse:
    """按用户操作调用 DeepSeek，将已保存论文的指定字段翻译为中文。

    参数：
        paper_id：搜索最终结果提供的稳定内部论文标识。
        state_store：只读 SQLite 搜索结果快照适配层。
        translation_client：可替换的 DeepSeek 翻译适配器。
    返回：
        PaperTranslationResponse：包含当前标题或摘要字段的简体中文译文。
    异常：
        HTTPException：论文不存在、当前字段缺失或翻译服务不可用时返回安全公共错误。
    """
    normalized_paper_id = paper_id.strip()  # 拒绝空白论文标识进入 SQLite 扫描和模型调用。
    if not normalized_paper_id:  # 空标识不可能对应已保存论文。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="论文详情不存在或尚未保存")  # 复用论文资源的稳定不存在语义。
    try:  # 先读取已保存论文，禁止前端提交任意文本给模型。
        paper = SavedPaperResolver(state_store, library_repository).get_paper(normalized_paper_id)  # 只从统一解析的搜索或已收藏 SQLite 快照读取公开元数据。
    except SearchRunStoreError:  # 不向客户端暴露数据库路径、SQL 或快照正文。
        logger.exception("论文翻译读取接口失败：论文=%s", normalized_paper_id)  # 仅记录稳定内部标识和受控堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="论文翻译暂时不可用，请稍后重试") from None  # 返回可重试公共错误。
    if paper is None:  # 未保存论文不能触发模型调用。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="论文详情不存在或尚未保存")  # 保持资源读取与翻译的授权边界一致。
    source_text = paper.title if field == "title" else paper.abstract  # 只读取当前用户请求字段，标题翻译不依赖摘要存在。
    if not source_text.strip():  # 缺失当前字段时不得消耗模型调用或返回错误缓存。
        field_label = "标题" if field == "title" else "摘要"  # 为用户构造与请求字段一致的公共提示。
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"论文{field_label}暂缺，无法翻译")  # 明确当前字段缺失而不误报摘要。
    try:  # 将真实模型异常转换为稳定 HTTP 语义。
        return await ResourceTranslationService(translation_store).translate(paper.paper_id, field, source_text, lambda: translation_client.translate(paper, field))  # 统一缓存命中、模型调用和写入降级，且只翻译当前字段。
    except PaperTranslationError as exc:  # 配置、网络和模型输出错误均已在适配器净化。
        logger.exception("论文翻译调用失败：论文=%s", normalized_paper_id)  # 记录完整受控堆栈但不记录标题或摘要。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None  # 返回可重试且不泄露内部信息的公共提示。
