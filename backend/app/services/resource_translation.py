"""统一执行已授权资源的字段级译文缓存与模型调用流程。"""

from collections.abc import Awaitable, Callable  # 标注由各资源路由提供的受控异步翻译回调。

from backend.app.core.logging import logger  # 记录缓存降级堆栈但不写入原文或译文。
from backend.app.models.paper_translation import PaperTranslationResponse  # 复用稳定的字段级译文结果契约。
from backend.app.services.paper_translation_store import PaperTranslationStore, PaperTranslationStoreError, TranslationField  # 复用原文哈希缓存边界与字段枚举。


TranslationCall = Callable[[], Awaitable[PaperTranslationResponse]]  # 调用方必须只传入已完成资源授权后的模型调用。


class ResourceTranslationService:
    """执行字段级译文缓存、模型调用和缓存写入降级。

    参数：
        translation_store：可替换的 SQLite 字段级译文缓存边界。
    """

    def __init__(self, translation_store: PaperTranslationStore) -> None:
        """保存缓存依赖，不负责读取论文、网页发现或 HTTP 参数。"""
        self._translation_store = translation_store  # 保持资源授权与缓存执行职责分离。

    async def translate(self, resource_id: str, field: TranslationField, source_text: str, translate_call: TranslationCall) -> PaperTranslationResponse:
        """优先复用当前原文版本的缓存，未命中时调用模型并尽力保存。

        参数：
            resource_id：已授权资源的稳定缓存标识。
            field：缓存使用的标题或摘要字段。
            source_text：已由路由确认可翻译的受控公开文本。
            translate_call：只调用一次的受控异步模型翻译回调。
        返回：
            PaperTranslationResponse：缓存命中或本次模型生成的稳定译文。
        异常：
            PaperTranslationError：由调用方模型适配器原样抛出，供路由映射既有 503。
        """
        try:  # 缓存读取失败不应阻止用户主动请求当前字段译文。
            cached_translation = self._translation_store.get(resource_id, field, source_text)  # 缓存键仍由资源、字段与原文 SHA-256 组成。
        except PaperTranslationStoreError:  # SQLite 暂不可用时安全降级到单次模型调用。
            logger.exception("字段译文缓存读取失败，将直接调用翻译服务：资源=%s，字段=%s", resource_id, field)  # 不记录原文、译文或外部响应。
            cached_translation = None  # 明确缓存未命中以继续受控模型调用。
        if cached_translation is not None:  # 当前原文版本命中时绝不重复调用模型。
            return cached_translation  # 保持缓存中的模型名和文本不变。
        translated = await translate_call()  # 只在缓存未命中或读取降级时调用一次模型。
        try:  # 模型成功后尽力持久化当前原文版本的译文。
            return self._translation_store.save(translated, source_text)  # 复用既有哈希失效和字段级存储语义。
        except PaperTranslationStoreError:  # 缓存写入失败不应丢弃已完成的本次模型结果。
            logger.exception("字段译文缓存写入失败，将返回本次翻译结果：资源=%s，字段=%s", resource_id, field)  # 不记录原文、译文或模型响应。
            return translated  # 下次请求会再次尝试缓存和模型调用。
