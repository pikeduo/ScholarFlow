"""提供个人文献库收藏、筛选、更新和删除的版本化 API。"""

from collections.abc import Iterator  # 标注请求级数据库会话依赖。
from typing import Annotated  # 声明 FastAPI 依赖和查询参数类型。

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status  # 构建稳定 REST 接口和错误边界。
from sqlalchemy.exc import SQLAlchemyError  # 捕获持久化层可预期故障。
from sqlalchemy.orm import Session  # 标注请求级 SQLAlchemy 会话。

from backend.app.core.logging import logger  # 记录数据库故障完整堆栈。
from backend.app.models.library import LibraryItem, LibraryItemList, LibrarySaveResult, ReadingStatus, SaveLibraryItemRequest, UpdateLibraryItemRequest  # 声明公共请求与响应契约。
from backend.app.repositories.database import SessionLocal  # 为每个请求创建独立数据库会话。
from backend.app.repositories.faiss_index import FaissIndexManager  # 管理默认文献库 FAISS 索引文件。
from backend.app.repositories.library import LibraryRepository  # 装配 SQLite 文献库仓储。
from backend.app.repositories.vector_metadata import VectorMetadataRepository  # 装配 SQLite 向量映射仓储。
from backend.app.services.library import LibraryItemNotFoundError, LibraryService  # 编排文献库业务并映射不存在错误。
from backend.app.services.library_vector_index import DEFAULT_LIBRARY_INDEX_PATH, LIBRARY_INDEX_NAME, LibraryPaperIndexer, LibraryVectorIndexer  # 装配可覆盖的收藏后向量索引依赖。


router = APIRouter(prefix="/library/items")  # 将个人文献库端点组织到稳定资源路径。


def get_database_session() -> Iterator[Session]:
    """提供请求级数据库会话并确保异常路径释放连接。"""
    session = SessionLocal()  # 为当前请求创建独立事务会话。
    try:  # 将会话生命周期绑定到请求。
        yield session  # 交给服务依赖完成业务操作。
    finally:  # 成功或异常都关闭连接资源。
        session.close()  # 防止连接长期占用。


_library_paper_indexer = LibraryVectorIndexer(index_manager=FaissIndexManager(LIBRARY_INDEX_NAME, DEFAULT_LIBRARY_INDEX_PATH))  # 创建进程级懒加载模型和索引管理器，构造阶段不触发 I/O。


def get_library_paper_indexer() -> LibraryPaperIndexer:
    """提供可由测试覆盖的进程级文献库向量索引器。"""
    return _library_paper_indexer  # 复用模型和索引实例，避免每次收藏重复加载权重。


def get_library_service(session: Annotated[Session, Depends(get_database_session)], paper_indexer: Annotated[LibraryPaperIndexer, Depends(get_library_paper_indexer)]) -> LibraryService:
    """使用请求级会话构造文献库服务。"""
    return LibraryService(LibraryRepository(session), VectorMetadataRepository(session), paper_indexer)  # 集中装配文献库、向量状态和懒加载索引器。


@router.post("", response_model=LibrarySaveResult, status_code=status.HTTP_200_OK, summary="收藏论文")
def save_library_item(request: SaveLibraryItemRequest, service: Annotated[LibraryService, Depends(get_library_service)]) -> LibrarySaveResult:
    """去重保存一篇论文；重复收藏返回已有记录并合并标签。"""
    try:  # 将数据库故障隔离为稳定公共错误。
        return service.save(request)  # 执行身份去重和持久化。
    except SQLAlchemyError:  # 不暴露 SQL、数据库路径或内部表结构。
        logger.exception("文献库保存失败")  # 在受控日志中保留完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="文献库服务暂时不可用，请稍后重试") from None  # 返回安全错误。


@router.get("", response_model=LibraryItemList, status_code=status.HTTP_200_OK, summary="查询文献库")
def list_library_items(service: Annotated[LibraryService, Depends(get_library_service)], tag: Annotated[str | None, Query(min_length=1, max_length=100)] = None, reading_status: ReadingStatus | None = None) -> LibraryItemList:
    """按可选标签和阅读状态筛选个人文献库。"""
    try:  # 将数据库故障隔离为稳定公共错误。
        return service.list(tag=tag, reading_status=reading_status)  # 返回确定性筛选结果。
    except SQLAlchemyError:  # 不向前端暴露查询实现细节。
        logger.exception("文献库查询失败")  # 记录完整堆栈供排查。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="文献库服务暂时不可用，请稍后重试") from None  # 返回安全错误。


@router.get("/{item_id}", response_model=LibraryItem, status_code=status.HTTP_200_OK, summary="读取收藏")
def get_library_item(item_id: str, service: Annotated[LibraryService, Depends(get_library_service)]) -> LibraryItem:
    """按内部标识读取单条收藏。"""
    try:  # 区分不存在和数据库故障。
        return service.get(item_id)  # 读取完整收藏。
    except LibraryItemNotFoundError:  # 不存在属于稳定资源边界。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文献库记录不存在") from None  # 返回明确 404。
    except SQLAlchemyError:  # 隐藏持久化实现细节。
        logger.exception("文献库记录读取失败")  # 记录完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="文献库服务暂时不可用，请稍后重试") from None  # 返回安全错误。


@router.patch("/{item_id}", response_model=LibraryItem, status_code=status.HTTP_200_OK, summary="更新收藏")
def update_library_item(item_id: str, request: UpdateLibraryItemRequest, service: Annotated[LibraryService, Depends(get_library_service)]) -> LibraryItem:
    """更新收藏标签、备注或阅读状态。"""
    try:  # 区分不存在和数据库故障。
        return service.update(item_id, request)  # 只更新请求明确提交的字段。
    except LibraryItemNotFoundError:  # 不允许 PATCH 创建不存在资源。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文献库记录不存在") from None  # 返回明确 404。
    except SQLAlchemyError:  # 隐藏 SQL 和数据库位置。
        logger.exception("文献库记录更新失败")  # 记录完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="文献库服务暂时不可用，请稍后重试") from None  # 返回安全错误。


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除收藏")
def delete_library_item(item_id: str, service: Annotated[LibraryService, Depends(get_library_service)]) -> Response:
    """删除指定收藏并返回无正文成功响应。"""
    try:  # 区分不存在和数据库故障。
        service.delete(item_id)  # 执行原子删除。
        return Response(status_code=status.HTTP_204_NO_CONTENT)  # 成功删除不返回冗余 JSON。
    except LibraryItemNotFoundError:  # 删除不存在资源返回稳定 404。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文献库记录不存在") from None  # 返回明确错误。
    except SQLAlchemyError:  # 隐藏持久化实现细节。
        logger.exception("文献库记录删除失败")  # 记录完整堆栈。
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="文献库服务暂时不可用，请稍后重试") from None  # 返回安全错误。
