"""提供只从已保存搜索结果读取的版本化论文详情接口。"""

from typing import Annotated  # 为 FastAPI 依赖注入声明清晰类型。

from fastapi import APIRouter, Depends, HTTPException, status  # 声明只读详情路由和稳定错误响应。

from backend.app.api.routes.search import get_search_run_state_store  # 复用 SQLite 搜索结果存储装配，避免新增基础设施。
from backend.app.core.logging import logger  # 记录存储边界异常的完整堆栈。
from backend.app.models.paper import PaperRecord  # 返回统一的规范化论文领域契约。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离 SQLite 访问并映射公共错误。


router = APIRouter(prefix="/papers")  # 将论文资源归入固定版本化路径。


@router.get("/{paper_id}", response_model=PaperRecord, status_code=status.HTTP_200_OK, summary="读取已保存论文详情")
def get_paper_detail(
    paper_id: str,
    state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)],
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
        paper = state_store.get_paper(normalized_paper_id)  # 仅读取最终结果快照，绝不触发外部学术来源。
    except SearchRunStoreError:  # 不将数据库路径、SQL 或快照正文泄露给客户端。
        logger.exception("论文详情读取接口失败：论文=%s", normalized_paper_id)  # 只记录安全内部标识和堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="论文详情暂时不可用，请稍后重试") from None  # 返回可重试的公共提示。
    if paper is None:  # 未被任何已完成搜索保存的论文不能由前端伪造读取。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="论文详情不存在或尚未保存")  # 保持不存在与尚未保存的同一安全语义。
    return paper  # 返回统一 PaperRecord，不额外查询供应商 API。
