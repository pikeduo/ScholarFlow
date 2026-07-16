"""提供只翻译已保存补充网页发现的安全接口。"""

from hashlib import sha256  # 为来源 URL 生成稳定且不暴露正文的缓存标识。
from typing import Annotated, Literal  # 声明 FastAPI 参数与翻译字段范围。

from fastapi import APIRouter, Depends, HTTPException, Query, status  # 声明独立网页发现翻译路由。

from backend.app.adapters.deepseek_translation import DeepSeekPaperTranslationClient, PaperTranslationError, TextTranslationClient  # 复用受控 DeepSeek 文本翻译适配器。
from backend.app.api.routes.search import get_search_run_state_store  # 复用 SQLite 搜索结果存储装配。
from backend.app.api.routes.papers import get_paper_translation_store  # 复用论文翻译的 SQLite 缓存服务，避免创建第二套缓存基础设施。
from backend.app.core.logging import logger  # 记录不含网页正文的完整受控堆栈。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 保持网页发现与论文领域对象严格隔离。
from backend.app.services.paper_translation_store import PaperTranslationStore  # 复用统一字段级译文缓存服务。
from backend.app.services.resource_translation import ResourceTranslationService  # 统一执行缓存命中、模型调用和缓存降级流程。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离搜索快照读取边界。


router = APIRouter(prefix="/discoveries")  # 将网页发现资源归入独立版本化路径。
discovery_translation_client: TextTranslationClient = DeepSeekPaperTranslationClient()  # 复用论文翻译的同一个 DeepSeek 文本客户端。


def get_discovery_translation_client() -> TextTranslationClient:
    """返回当前用于按需网页发现翻译的 DeepSeek 适配器。"""
    return discovery_translation_client  # 通过依赖注入隔离真实网络调用并支持离线测试替换。


def _read_saved_discovery(run_id: str, url: str, state_store: SearchRunStateStore) -> SupplementalDiscoveryItem | None:
    """从同次已保存搜索结果中精确读取网页发现，绝不接收前端正文。"""
    result = state_store.get_result(run_id)  # 仅读取 SQLite 最终结果快照，不重新调用 Tavily 或其他外部来源。
    if result is None:  # 运行不存在、未完成或结果已被清理时不允许调用模型。
        return None  # 保持资源不存在与尚未保存的统一语义。
    return next((item for item in result.discoveries if item.url == url), None)  # 仅允许精确匹配同次快照内的 URL。


def _discovery_cache_id(item: SupplementalDiscoveryItem) -> str:
    """返回由来源和 URL 派生的稳定匿名缓存标识。"""
    material = f"{item.source}\n{item.url}"  # URL 不携带正文，可在相同发现跨运行时复用缓存。
    return f"discovery:{sha256(material.encode('utf-8')).hexdigest()}"  # 显式 UTF-8 保证 Windows 与服务端一致。


@router.post("/translation/{field}", status_code=status.HTTP_200_OK, summary="翻译已保存补充网页发现标题或摘要片段")
async def translate_discovery(
    field: Literal["title", "snippet"],
    run_id: Annotated[str, Query(min_length=1)],
    url: Annotated[str, Query(min_length=1)],
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
    translation_client: Annotated[TextTranslationClient, Depends(get_discovery_translation_client)],
    translation_store: Annotated[PaperTranslationStore, Depends(get_paper_translation_store)],
) -> dict[str, str]:
    """按用户操作翻译同次已保存网页发现的指定字段并缓存结果。"""
    normalized_run_id = run_id.strip()  # 拒绝空白运行标识进入 SQLite 扫描。
    normalized_url = url.strip()  # 拒绝空白 URL 进入查找和缓存键生成。
    if not normalized_run_id or not normalized_url:  # 防止无效参数触发模型调用。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="补充网页发现不存在或尚未保存")  # 保持稳定公共资源语义。
    try:  # 先读取同次 SQLite 快照，禁止前端伪造或提交网页正文。
        discovery = _read_saved_discovery(normalized_run_id, normalized_url, state_store)  # 只读取最终结果中独立保存的网页发现。
    except SearchRunStoreError:  # 不向客户端暴露数据库路径、SQL 或快照正文。
        logger.exception("网页发现翻译读取接口失败：运行=%s", normalized_run_id)  # 仅记录运行标识和受控堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="网页发现翻译暂时不可用，请稍后重试") from None  # 返回可重试的公共提示。
    if discovery is None:  # 未保存的 URL 不能被前端作为任意模型输入使用。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="补充网页发现不存在或尚未保存")  # 保持资源读取与翻译授权边界一致。
    source_text = discovery.title if field == "title" else discovery.snippet  # 只读取当前用户请求字段，标题翻译不依赖摘要片段存在。
    if not source_text.strip():  # 缺失当前字段时不得消耗模型调用或返回错误缓存。
        field_label = "标题" if field == "title" else "摘要片段"  # 为用户构造与请求字段一致的公共提示。
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"网页发现{field_label}暂缺，无法翻译")  # 明确字段缺失而不误报其他字段。
    discovery_id = _discovery_cache_id(discovery)  # 生成网页发现专用前缀的稳定缓存键，不参与论文身份处理。
    cache_field = "title" if field == "title" else "abstract"  # 将网页摘要片段映射到既有摘要缓存槽位，保持两个字段独立。
    try:  # 将真实模型异常转换为稳定 HTTP 语义。
        translated = await ResourceTranslationService(translation_store).translate(discovery_id, cache_field, source_text, lambda: translation_client.translate_text(discovery_id, cache_field, source_text))  # 统一缓存命中、模型调用和写入降级，仍只翻译当前字段。
    except PaperTranslationError as exc:  # 配置、网络和模型输出错误均已在适配器净化。
        logger.exception("网页发现翻译调用失败：发现=%s，字段=%s", discovery_id, field)  # 记录完整受控堆栈但不记录网页正文。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None  # 返回可重试且不泄露内部信息的公共提示。
    return {"field": field, "text_zh": translated.text_zh, "model_name": translated.model_name}  # 对前端保持网页发现字段名、轻量响应和摘要片段映射。
