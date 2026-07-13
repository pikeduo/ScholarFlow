"""提供从 SQLite 搜索运行快照读取用量统计的接口。"""
from typing import Annotated  # 声明 FastAPI 依赖类型。
from fastapi import APIRouter, Depends, HTTPException, status  # 声明路由与安全错误。
from backend.app.api.routes.search import get_search_run_state_store  # 复用运行状态存储装配。
from backend.app.core.logging import logger  # 记录受控异常。
from backend.app.models.usage import SearchRunUsage  # 声明公共响应。
from backend.app.services.search_run_store import SearchRunStateStore, SearchRunStoreError  # 隔离 SQLite 边界。
router = APIRouter(prefix="/usage")  # 组织版本化用量资源。
@router.get("/{run_id}", response_model=SearchRunUsage, status_code=status.HTTP_200_OK, summary="读取搜索运行用量")
def get_search_usage(run_id: str, state_store: Annotated[SearchRunStateStore, Depends(get_search_run_state_store)]) -> SearchRunUsage:
    """读取已保存运行统计，不重新执行搜索或估算缺失数据。"""
    try: state = state_store.get(run_id)  # 仅读取 SQLite 轻量快照。
    except SearchRunStoreError:
        logger.exception("搜索用量读取接口失败：运行=%s", run_id)  # 记录运行标识与堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="搜索用量暂时不可用，请稍后重试") from None
    if state is None: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="搜索运行不存在")
    return SearchRunUsage(run_id=state.run_id, api_call_count=state.api_call_count, token_usage=state.token_usage, cost_usd=state.cost_usd, latency_ms=state.latency_ms, cache_hits=state.cache_hits, current_round=state.current_round, max_rounds=state.max_rounds, selected_sources=state.selected_sources, stop_reason=state.stop_reason)
