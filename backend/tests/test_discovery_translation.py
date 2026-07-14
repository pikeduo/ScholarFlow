"""验证补充网页发现按字段翻译、缓存和快照边界。"""

from collections.abc import Iterator  # 标注 pytest 夹具的生成器返回类型。
from types import SimpleNamespace  # 构造只满足路由读取边界的轻量结果替身。

import pytest  # 提供夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证翻译路由。

from backend.app.api.routes.discoveries import get_discovery_translation_client  # 覆盖真实模型依赖避免测试访问网络。
from backend.app.api.routes.papers import get_paper_translation_store  # 复用论文翻译缓存替身验证同一缓存边界。
from backend.app.api.routes.search import get_search_run_state_store  # 覆盖 SQLite 搜索结果读取依赖。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.discovery import SupplementalDiscoveryItem  # 构造独立网页发现，不伪装成论文。
from backend.app.models.paper_translation import PaperTranslationResponse  # 构造复用的字段级翻译响应。


class FakeDiscoveryStore:
    """为网页发现翻译路由提供不访问 SQLite 的结果快照替身。"""

    def __init__(self, item: SupplementalDiscoveryItem | None) -> None:
        """保存可命中的独立网页发现或空值。"""
        self._item = item  # 保存由测试控制的同次发现快照。

    def get_result(self, _: str) -> SimpleNamespace | None:
        """返回只包含独立发现列表的完成结果替身。"""
        return SimpleNamespace(discoveries=[self._item]) if self._item is not None else None  # 路由只依赖 discoveries 字段。


class CountingTranslationClient:
    """记录共用模型调用次数，验证标题和摘要片段独立缓存。"""

    def __init__(self) -> None:
        """初始化调用计数。"""
        self.calls = 0  # 从零开始记录真实模型边界调用次数。

    async def translate_text(self, resource_id: str, field: str, _: str) -> PaperTranslationResponse:
        """返回固定译文并记录一次当前字段翻译。"""
        self.calls += 1  # 仅在当前字段缓存未命中时增加。
        return PaperTranslationResponse(paper_id=resource_id, field=field, text_zh="网页发现中文译文", model_name="deepseek-v4-flash")  # 复用论文翻译响应和缓存契约。


class FakeTranslationStore:
    """以资源、字段和原文模拟共用 SQLite 字段译文缓存。"""

    def __init__(self) -> None:
        """初始化空缓存字典。"""
        self._items: dict[tuple[str, str, str], PaperTranslationResponse] = {}  # 用完整原文模拟字段级原文版本键。

    def get(self, resource_id: str, field: str, source_text: str) -> PaperTranslationResponse | None:
        """读取与当前字段原文完全匹配的缓存。"""
        return self._items.get((resource_id, field, source_text))  # 标题和摘要片段必须使用独立键。

    def save(self, translation: PaperTranslationResponse, source_text: str) -> PaperTranslationResponse:
        """保存当前字段译文，模拟成功提交。"""
        self._items[(translation.paper_id, translation.field, source_text)] = translation  # 只覆盖同一网页发现缓存键、字段与原文版本。
        return translation  # 返回与生产缓存服务一致的稳定模型。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供网页发现翻译路由的本地 ASGI 客户端并清理依赖覆盖。"""
    client = TestClient(app)  # 创建不会发起外部 HTTP 的本地客户端。
    yield client  # 交给用例调用独立网页发现翻译资源。
    client.close()  # 释放测试客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 清理搜索结果替身避免污染其他测试。
    app.dependency_overrides.pop(get_discovery_translation_client, None)  # 清理模型替身避免污染其他测试。
    app.dependency_overrides.pop(get_paper_translation_store, None)  # 清理共用缓存替身避免污染其他测试。


def _discovery() -> SupplementalDiscoveryItem:
    """构造包含英文标题和摘要片段的最小已保存网页发现。"""
    return SupplementalDiscoveryItem(source="tavily", title="Evidence grounded discovery", url="https://example.com/discovery", snippet="This page describes grounded literature discovery.", raw_rank=1)  # 提供标题和摘要片段两种独立字段。


def test_discovery_translation_reuses_cache_by_field_and_never_accepts_body(api_client: TestClient) -> None:
    """重复标题应命中缓存，摘要片段仍单独翻译且请求只包含运行和 URL。"""
    item = _discovery()  # 构造当前已保存搜索运行中的网页发现。
    translation_client = CountingTranslationClient()  # 注入可观测共用模型替身。
    translation_store = FakeTranslationStore()  # 注入字段和原文独立的共用缓存替身。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeDiscoveryStore(item)  # 仅允许路由从结果快照读取网页文本。
    app.dependency_overrides[get_discovery_translation_client] = lambda: translation_client  # 记录模型调用次数。
    app.dependency_overrides[get_paper_translation_store] = lambda: translation_store  # 避免写入真实 SQLite。

    first_title = api_client.post("/api/v1/discoveries/translation/title?run_id=run-1&url=https%3A%2F%2Fexample.com%2Fdiscovery")  # 首次标题请求应调用模型并写缓存。
    second_title = api_client.post("/api/v1/discoveries/translation/title?run_id=run-1&url=https%3A%2F%2Fexample.com%2Fdiscovery")  # 同一标题请求应直接命中缓存。
    snippet = api_client.post("/api/v1/discoveries/translation/snippet?run_id=run-1&url=https%3A%2F%2Fexample.com%2Fdiscovery")  # 摘要片段必须作为独立字段单独翻译。

    assert first_title.status_code == second_title.status_code == snippet.status_code == 200  # 三次请求均应获得稳定响应。
    assert translation_client.calls == 2  # 标题只调用一次，摘要片段独立调用一次。
    assert second_title.json()["field"] == "title" and snippet.json()["field"] == "snippet"  # 验证缓存和响应均不会混淆两个字段。


def test_discovery_translation_rejects_unknown_saved_url(api_client: TestClient) -> None:
    """不在同次快照中的网页 URL 不能被前端用作任意模型文本输入。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakeDiscoveryStore(None)  # 模拟运行没有可翻译的已保存网页发现。
    app.dependency_overrides[get_discovery_translation_client] = lambda: CountingTranslationClient()  # 即使配置模型替身也不得被调用。
    app.dependency_overrides[get_paper_translation_store] = lambda: FakeTranslationStore()  # 未知发现不应读取或写入缓存。

    response = api_client.post("/api/v1/discoveries/translation/title?run_id=run-1&url=https%3A%2F%2Fexample.com%2Fmissing")  # 请求不在保存快照中的网页地址。

    assert response.status_code == 404  # 验证未知网页发现不会进入模型调用边界。
