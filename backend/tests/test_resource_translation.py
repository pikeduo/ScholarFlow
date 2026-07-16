"""验证受控资源译文执行服务的缓存命中、降级和模型失败边界。"""

import asyncio  # 在同步测试中执行服务的异步翻译方法。

import pytest  # 验证模型异常不会被服务改写。

from backend.app.adapters.deepseek_translation import PaperTranslationError  # 模拟安全的模型调用失败。
from backend.app.models.paper_translation import PaperTranslationResponse  # 构造稳定字段级译文结果。
from backend.app.services.paper_translation_store import PaperTranslationStoreError  # 模拟缓存读取与写入故障。
from backend.app.services.resource_translation import ResourceTranslationService  # 覆盖统一缓存与模型执行流程。


class FakeTranslationStore:
    """提供可控缓存命中和读写故障的离线替身。"""

    def __init__(self, cached: PaperTranslationResponse | None = None, fail_get: bool = False, fail_save: bool = False) -> None:
        """保存可选缓存结果及读写失败开关。"""
        self.cached = cached  # 保存当前原文版本的可复用译文。
        self.fail_get = fail_get  # 控制缓存读取是否失败。
        self.fail_save = fail_save  # 控制缓存写入是否失败。
        self.get_calls = 0  # 记录缓存读取次数。
        self.save_calls = 0  # 记录缓存写入次数。

    def get(self, _: str, __: str, ___: str) -> PaperTranslationResponse | None:
        """返回预置缓存或模拟稳定缓存读取错误。"""
        self.get_calls += 1  # 记录本次原文版本的缓存查询。
        if self.fail_get:  # 模拟 SQLite 不可用。
            raise PaperTranslationStoreError("模拟缓存读取失败")  # 保持生产异常类型。
        return self.cached  # 返回命中或未命中结果。

    def save(self, translation: PaperTranslationResponse, _: str) -> PaperTranslationResponse:
        """返回模型译文或模拟稳定缓存写入错误。"""
        self.save_calls += 1  # 记录模型成功后的缓存写入尝试。
        if self.fail_save:  # 模拟 SQLite 写入不可用。
            raise PaperTranslationStoreError("模拟缓存写入失败")  # 保持生产异常类型。
        return translation  # 模拟成功持久化并返回稳定结果。


def _translation(text_zh: str = "中文译文") -> PaperTranslationResponse:
    """构造绑定当前资源、字段与模型名的固定译文。"""
    return PaperTranslationResponse(paper_id="resource-1", field="title", text_zh=text_zh, model_name="deepseek-v4-flash")  # 保持离线测试不依赖模型或数据库。


def test_resource_translation_uses_cache_without_calling_model() -> None:
    """原文版本命中缓存时不得重复调用模型或写缓存。"""
    store = FakeTranslationStore(cached=_translation("缓存译文"))  # 构造已命中当前原文版本的缓存替身。
    model_calls = 0  # 记录不应发生的模型调用。

    async def translate_call() -> PaperTranslationResponse:
        """若被调用则说明缓存命中语义被破坏。"""
        nonlocal model_calls  # 更新闭包内调用计数。
        model_calls += 1  # 记录错误的模型调用。
        return _translation()  # 返回无关结果以完成类型契约。

    result = asyncio.run(ResourceTranslationService(store).translate("resource-1", "title", "source-v1", translate_call))  # 执行当前原文版本的翻译。
    assert result.text_zh == "缓存译文"  # 锁定直接复用缓存结果。
    assert model_calls == 0 and store.save_calls == 0  # 锁定命中时不调用模型也不重复写缓存。


def test_resource_translation_degrades_on_cache_failures_and_calls_model_once() -> None:
    """缓存读写失败时仍应仅调用一次模型并返回本次译文。"""
    store = FakeTranslationStore(fail_get=True, fail_save=True)  # 同时模拟读取和写入缓存不可用。
    model_calls = 0  # 记录本次请求的实际模型调用次数。

    async def translate_call() -> PaperTranslationResponse:
        """模拟一次成功且可展示的受控模型翻译。"""
        nonlocal model_calls  # 更新闭包内调用计数。
        model_calls += 1  # 缓存不可用时仍只允许调用一次。
        return _translation("本次译文")  # 返回未缓存但可展示的模型结果。

    result = asyncio.run(ResourceTranslationService(store).translate("resource-1", "title", "source-v2", translate_call))  # 执行缓存降级翻译。
    assert result.text_zh == "本次译文"  # 锁定写缓存失败不会丢弃模型成功结果。
    assert model_calls == 1 and store.get_calls == 1 and store.save_calls == 1  # 锁定一次请求中的缓存和模型调用次数。


def test_resource_translation_does_not_save_after_model_failure() -> None:
    """模型失败时不得写入空值或错误译文缓存。"""
    store = FakeTranslationStore()  # 构造正常可写但不应被调用的缓存替身。

    async def translate_call() -> PaperTranslationResponse:
        """模拟受控模型适配器抛出的稳定异常。"""
        raise PaperTranslationError("翻译服务暂时不可用")  # 保持路由已有的 503 映射异常类型。

    with pytest.raises(PaperTranslationError):  # 锁定服务不吞没模型错误。
        asyncio.run(ResourceTranslationService(store).translate("resource-1", "title", "source-v3", translate_call))  # 触发失败模型调用。
    assert store.save_calls == 0  # 锁定模型失败前不会尝试写入错误缓存。
