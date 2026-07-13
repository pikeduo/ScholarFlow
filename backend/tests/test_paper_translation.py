"""验证论文标题与摘要按需中文翻译的适配器和 API 边界。"""

import asyncio  # 在同步 pytest 中执行异步适配器和接口调用。
import json  # 检查发送给 DeepSeek 的结构化公开元数据。
from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import httpx  # 使用 MockTransport 禁止真实 DeepSeek 网络调用。
import pytest  # 提供夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证翻译路由。

from backend.app.adapters.deepseek_translation import DeepSeekPaperTranslationClient  # 导入待测 DeepSeek 翻译适配器。
from backend.app.api.routes.papers import get_library_paper_repository, get_paper_translation_client, get_paper_translation_store  # 覆盖真实翻译、文献库和缓存依赖避免接口测试访问网络。
from backend.app.api.routes.search import get_search_run_state_store  # 覆盖 SQLite 论文读取依赖。
from backend.app.core.config import Settings  # 构造隔离且含测试密钥的配置。
from backend.app.main import app  # 导入已装配版本化路由的 FastAPI 应用。
from backend.app.models.paper import PaperRecord  # 构造已保存的规范化论文记录。
from backend.app.models.paper_translation import PaperTranslationResponse  # 构造稳定翻译结果。


class FakePaperStore:
    """为翻译路由提供不访问 SQLite 的已保存论文替身。"""

    def __init__(self, paper: PaperRecord | None) -> None:
        """保存可命中的论文或空值。"""
        self._paper = paper  # 保存稳定的已保存论文读取结果。

    def get_paper(self, _: str) -> PaperRecord | None:
        """返回固定论文，避免路由触发真实持久化读取。"""
        return self._paper  # 路由只依赖此最小读取接口。


class FakeLibraryPaperRepository:
    """为翻译接口提供不访问真实 SQLite 的文献库快照替身。"""

    def __init__(self, paper: PaperRecord | None = None) -> None:
        """保存可选的已收藏论文快照。"""
        self._paper = paper  # 保存命中或空值。

    def find_paper(self, paper_id: str) -> PaperRecord | None:
        """仅对相同论文标识返回收藏快照。"""
        return self._paper if self._paper is not None and self._paper.paper_id == paper_id else None  # 模拟生产精确匹配规则。


class FakeTranslationClient:
    """为路由测试提供不访问 DeepSeek 的翻译替身。"""

    async def translate(self, paper: PaperRecord, field: str) -> PaperTranslationResponse:
        """返回与输入论文绑定的固定中文译文。"""
        return PaperTranslationResponse(paper_id=paper.paper_id, field=field, text_zh="证据驱动检索", model_name="deepseek-v4-flash")  # 模拟已验证的模型响应。


class CountingTranslationClient:
    """记录模型调用次数，验证缓存命中时不会重复翻译。"""

    def __init__(self) -> None:
        """初始化可观测的调用计数。"""
        self.calls = 0  # 从零开始记录实际模型边界调用。

    async def translate(self, paper: PaperRecord, field: str) -> PaperTranslationResponse:
        """返回固定译文并记录一次模型调用。"""
        self.calls += 1  # 只应在 SQLite 缓存未命中时增加。
        return PaperTranslationResponse(paper_id=paper.paper_id, field=field, text_zh="证据驱动检索", model_name="deepseek-v4-flash")  # 返回与字段绑定的稳定结果。


class FakeTranslationStore:
    """为路由测试提供以论文、字段和原文模拟的持久译文缓存。"""

    def __init__(self) -> None:
        """初始化空缓存字典。"""
        self._items: dict[tuple[str, str, str], PaperTranslationResponse] = {}  # 用完整原文模拟 SQLite 的原文哈希版本键。

    def get(self, paper_id: str, field: str, source_text: str) -> PaperTranslationResponse | None:
        """读取与当前字段原文完全匹配的缓存。"""
        return self._items.get((paper_id, field, source_text))  # 标题和摘要必须使用独立键。

    def save(self, translation: PaperTranslationResponse, source_text: str) -> PaperTranslationResponse:
        """保存当前字段译文，模拟成功提交的 SQLite 缓存。"""
        self._items[(translation.paper_id, translation.field, source_text)] = translation  # 仅覆盖同一论文、字段和原文版本。
        return translation  # 返回与生产缓存服务一致的稳定模型。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供翻译路由的本地 ASGI 客户端并清理依赖覆盖。"""
    client = TestClient(app)  # 创建不会发起外部 HTTP 的本地客户端。
    yield client  # 交给用例调用翻译资源。
    client.close()  # 释放测试客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 清理 SQLite 替身避免污染其他测试。
    app.dependency_overrides.pop(get_paper_translation_client, None)  # 清理翻译替身避免污染其他测试。
    app.dependency_overrides.pop(get_paper_translation_store, None)  # 清理译文缓存替身避免污染其他测试。
    app.dependency_overrides.pop(get_library_paper_repository, None)  # 清理文献库回退替身避免读取真实数据库。


def _paper() -> PaperRecord:
    """构造包含英文标题和摘要的最小已保存论文。"""
    return PaperRecord(paper_id="paper-translation-1", title="Evidence Grounded Retrieval", abstract="This paper studies retrieval with grounded evidence.", source="openalex")  # 提供模型翻译所需公开文本。


def test_deepseek_translation_client_sends_saved_title_and_abstract_only() -> None:
    """适配器应调用 JSON Output，并返回严格校验后的中文译文。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """检查请求不含无关用户数据并返回固定 DeepSeek 响应。"""
        body = json.loads(request.content.decode("utf-8"))  # 使用显式 UTF-8 解析请求 JSON。
        user_payload = json.loads(body["messages"][1]["content"])  # 读取仅含论文公开文本的用户消息。
        assert user_payload == {"field": "title", "text": "Evidence Grounded Retrieval"}  # 验证只发送用户请求字段而不发送额外文本或密钥。
        assert body["response_format"] == {"type": "json_object"}  # 验证使用可校验 JSON Output。
        response_body = {"model": "deepseek-v4-flash", "choices": [{"message": {"content": json.dumps({"text_zh": "证据驱动检索"}, ensure_ascii=False)}}]}  # 构造模型固定翻译响应。
        return httpx.Response(200, json=response_body, request=request)  # 返回不访问网络的成功响应。

    client = DeepSeekPaperTranslationClient(config=Settings(_env_file=None, deepseek_api_key="test-key"), transport=httpx.MockTransport(handler))  # 注入测试密钥与本地传输层。
    translated = asyncio.run(client.translate(_paper(), "title"))  # 执行单字段适配器请求与响应校验。

    assert translated.field == "title" and translated.text_zh == "证据驱动检索"  # 验证标题字段译文正确映射。


def test_paper_translation_endpoint_translates_only_saved_paper(api_client: TestClient) -> None:
    """翻译接口应只接受已保存论文标识并返回中文标题与摘要。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(_paper())  # 注入已保存论文读取替身。
    app.dependency_overrides[get_paper_translation_client] = lambda: FakeTranslationClient()  # 注入无需网络的翻译替身。
    app.dependency_overrides[get_paper_translation_store] = lambda: FakeTranslationStore()  # 注入不写用户 SQLite 的缓存替身。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持搜索快照命中测试不访问真实文献库。

    response = api_client.post("/api/v1/papers/translation/title?paper_id=paper-translation-1")  # 通过查询参数传递稳定论文标识并触发标题翻译。

    assert response.status_code == 200  # 验证用户主动翻译成功返回。
    assert response.json()["text_zh"] == "证据驱动检索"  # 验证响应包含可供卡片显示的标题译文。


def test_paper_translation_endpoint_reuses_sqlite_cache_by_field_and_source_text(api_client: TestClient) -> None:
    """同一论文标题重复请求应命中缓存，摘要仍保留独立翻译边界。"""
    translation_client = CountingTranslationClient()  # 注入可观测模型替身。
    translation_store = FakeTranslationStore()  # 注入字段和原文独立的缓存替身。
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(_paper())  # 提供可翻译的已保存论文。
    app.dependency_overrides[get_paper_translation_client] = lambda: translation_client  # 记录模型调用次数。
    app.dependency_overrides[get_paper_translation_store] = lambda: translation_store  # 避免写入真实 SQLite。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持缓存测试不访问真实文献库。

    first_title = api_client.post("/api/v1/papers/translation/title?paper_id=paper-translation-1")  # 首次标题请求应调用模型并写缓存。
    second_title = api_client.post("/api/v1/papers/translation/title?paper_id=paper-translation-1")  # 同一标题请求应直接命中缓存。
    abstract = api_client.post("/api/v1/papers/translation/abstract?paper_id=paper-translation-1")  # 摘要必须作为独立字段单独翻译。

    assert first_title.status_code == second_title.status_code == abstract.status_code == 200  # 三次请求均应获得稳定响应。
    assert translation_client.calls == 2  # 标题只调用一次，摘要独立调用一次。
    assert second_title.json()["field"] == "title" and abstract.json()["field"] == "abstract"  # 验证缓存和响应均不会混淆两个字段。


def test_paper_translation_endpoint_rejects_unknown_paper(api_client: TestClient) -> None:
    """未知论文不能被前端用来触发任意 DeepSeek 文本翻译。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(None)  # 注入未命中论文的只读替身。
    app.dependency_overrides[get_paper_translation_client] = lambda: FakeTranslationClient()  # 即使配置翻译替身也不得被调用。
    app.dependency_overrides[get_paper_translation_store] = lambda: FakeTranslationStore()  # 未知论文不应读取或写入缓存。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository()  # 保持未知论文测试不访问真实文献库。

    response = api_client.post("/api/v1/papers/translation/abstract?paper_id=missing-paper")  # 请求不在保存快照中的论文标识。

    assert response.status_code == 404  # 验证未知论文不会进入模型调用边界。


def test_paper_translation_endpoint_falls_back_to_saved_library_paper(api_client: TestClient) -> None:
    """搜索快照清理后，用户收藏的论文仍可按字段独立复用翻译入口。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(None)  # 模拟搜索结果快照不再保存该论文。
    app.dependency_overrides[get_library_paper_repository] = lambda: FakeLibraryPaperRepository(_paper())  # 注入用户已收藏的论文快照。
    app.dependency_overrides[get_paper_translation_client] = lambda: FakeTranslationClient()  # 注入无需外网的翻译替身。
    app.dependency_overrides[get_paper_translation_store] = lambda: FakeTranslationStore()  # 注入隔离译文缓存。

    response = api_client.post("/api/v1/papers/translation/title?paper_id=paper-translation-1")  # 请求收藏论文标题的独立翻译。

    assert response.status_code == 200 and response.json()["field"] == "title"  # 验证文献库快照也可安全进入字段级翻译边界。
