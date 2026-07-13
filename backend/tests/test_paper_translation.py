"""验证论文标题与摘要按需中文翻译的适配器和 API 边界。"""

import asyncio  # 在同步 pytest 中执行异步适配器和接口调用。
import json  # 检查发送给 DeepSeek 的结构化公开元数据。
from collections.abc import Iterator  # 标注测试夹具的生成器返回类型。

import httpx  # 使用 MockTransport 禁止真实 DeepSeek 网络调用。
import pytest  # 提供夹具声明能力。
from fastapi.testclient import TestClient  # 通过本地 ASGI 客户端验证翻译路由。

from backend.app.adapters.deepseek_translation import DeepSeekPaperTranslationClient  # 导入待测 DeepSeek 翻译适配器。
from backend.app.api.routes.papers import get_paper_translation_client  # 覆盖真实翻译依赖避免接口测试访问网络。
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


class FakeTranslationClient:
    """为路由测试提供不访问 DeepSeek 的翻译替身。"""

    async def translate(self, paper: PaperRecord) -> PaperTranslationResponse:
        """返回与输入论文绑定的固定中文译文。"""
        return PaperTranslationResponse(paper_id=paper.paper_id, title_zh="证据驱动检索", abstract_zh="这是一段中文摘要。", model_name="deepseek-v4-flash")  # 模拟已验证的模型响应。


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    """提供翻译路由的本地 ASGI 客户端并清理依赖覆盖。"""
    client = TestClient(app)  # 创建不会发起外部 HTTP 的本地客户端。
    yield client  # 交给用例调用翻译资源。
    client.close()  # 释放测试客户端资源。
    app.dependency_overrides.pop(get_search_run_state_store, None)  # 清理 SQLite 替身避免污染其他测试。
    app.dependency_overrides.pop(get_paper_translation_client, None)  # 清理翻译替身避免污染其他测试。


def _paper() -> PaperRecord:
    """构造包含英文标题和摘要的最小已保存论文。"""
    return PaperRecord(paper_id="paper-translation-1", title="Evidence Grounded Retrieval", abstract="This paper studies retrieval with grounded evidence.", source="openalex")  # 提供模型翻译所需公开文本。


def test_deepseek_translation_client_sends_saved_title_and_abstract_only() -> None:
    """适配器应调用 JSON Output，并返回严格校验后的中文译文。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """检查请求不含无关用户数据并返回固定 DeepSeek 响应。"""
        body = json.loads(request.content.decode("utf-8"))  # 使用显式 UTF-8 解析请求 JSON。
        user_payload = json.loads(body["messages"][1]["content"])  # 读取仅含论文公开文本的用户消息。
        assert user_payload == {"title": "Evidence Grounded Retrieval", "abstract": "This paper studies retrieval with grounded evidence."}  # 验证不发送额外用户查询或密钥。
        assert body["response_format"] == {"type": "json_object"}  # 验证使用可校验 JSON Output。
        response_body = {"model": "deepseek-v4-flash", "choices": [{"message": {"content": json.dumps({"title_zh": "证据驱动检索", "abstract_zh": "本文研究具有证据依据的检索。"}, ensure_ascii=False)}}]}  # 构造模型固定翻译响应。
        return httpx.Response(200, json=response_body, request=request)  # 返回不访问网络的成功响应。

    client = DeepSeekPaperTranslationClient(config=Settings(_env_file=None, deepseek_api_key="test-key"), transport=httpx.MockTransport(handler))  # 注入测试密钥与本地传输层。
    translated = asyncio.run(client.translate(_paper()))  # 执行完整适配器请求与响应校验。

    assert translated.title_zh == "证据驱动检索"  # 验证标题译文正确映射。
    assert translated.abstract_zh == "本文研究具有证据依据的检索。"  # 验证摘要译文正确映射。


def test_paper_translation_endpoint_translates_only_saved_paper(api_client: TestClient) -> None:
    """翻译接口应只接受已保存论文标识并返回中文标题与摘要。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(_paper())  # 注入已保存论文读取替身。
    app.dependency_overrides[get_paper_translation_client] = lambda: FakeTranslationClient()  # 注入无需网络的翻译替身。

    response = api_client.post("/api/v1/papers/paper-translation-1/translation")  # 通过稳定论文资源触发按需翻译。

    assert response.status_code == 200  # 验证用户主动翻译成功返回。
    assert response.json()["title_zh"] == "证据驱动检索"  # 验证响应包含可供卡片显示的标题译文。


def test_paper_translation_endpoint_rejects_unknown_paper(api_client: TestClient) -> None:
    """未知论文不能被前端用来触发任意 DeepSeek 文本翻译。"""
    app.dependency_overrides[get_search_run_state_store] = lambda: FakePaperStore(None)  # 注入未命中论文的只读替身。
    app.dependency_overrides[get_paper_translation_client] = lambda: FakeTranslationClient()  # 即使配置翻译替身也不得被调用。

    response = api_client.post("/api/v1/papers/missing-paper/translation")  # 请求不在保存快照中的论文标识。

    assert response.status_code == 404  # 验证未知论文不会进入模型调用边界。
