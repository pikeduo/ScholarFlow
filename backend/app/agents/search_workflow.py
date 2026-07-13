"""使用 LangGraph 编排现有多轮搜索服务的实际运行节点。"""

from typing import NotRequired, Protocol, TypedDict  # 声明工作流状态和可替换的执行服务边界。

from langgraph.graph import END, START, StateGraph  # 使用 LangGraph 定义可审计的异步节点与固定边。

from backend.app.models.multi_round_search import MultiRoundSearchResult  # 回传与 REST、SSE 共用的最终搜索结果契约。
from backend.app.models.query_intent import QueryIntent  # 接收已规划或用户编辑的完整检索意图。
from backend.app.services.search_events import SearchRunEventPublisher  # 沿用既有安全进度事件发布边界。


class MultiRoundSearchExecutor(Protocol):
    """定义 LangGraph 执行节点依赖的多轮搜索服务最小接口。"""

    async def run_direct(self, query: QueryIntent, *, budget_exhausted: bool = False, event_publisher: SearchRunEventPublisher | None = None) -> MultiRoundSearchResult:
        """执行不再嵌套工作流的实际多轮搜索服务。"""
        ...  # 工作流只编排服务边界，不依赖来源适配器、HTTP 或模型细节。


class SearchWorkflowState(TypedDict):
    """保存单次 LangGraph 搜索运行所需的最小、可审计状态。"""

    query: QueryIntent  # 保存本次实际执行的结构化意图。
    budget_exhausted: bool  # 保存调用前已知的预算触顶状态。
    event_publisher: SearchRunEventPublisher | None  # 将 SSE 或未来 Redis 发布器透传给执行服务。
    workflow_status: str  # 保存节点边界可观察的工作流状态。
    result: NotRequired[MultiRoundSearchResult]  # 在执行节点完成后保存唯一最终结果。


class SearchWorkflowError(RuntimeError):
    """表示 LangGraph 未能形成可返回的多轮搜索结果。"""


class MultiRoundSearchWorkflow:
    """以 LangGraph 节点编排初始化、实际搜索和结果整理三个固定步骤。"""

    def __init__(self, executor: MultiRoundSearchExecutor) -> None:
        """保存可替换执行服务并编译不含动态边的稳定工作流。

        参数：
            executor：承载多源召回、排序、覆盖与停止条件的应用服务。
        """
        self._executor = executor  # 工作流只依赖服务协议，避免绑定具体控制器实现。
        self._graph = self._build_graph()  # 在装配时编译固定三节点图，避免每次请求重复定义边。

    async def run(self, query: QueryIntent, *, budget_exhausted: bool = False, event_publisher: SearchRunEventPublisher | None = None) -> MultiRoundSearchResult:
        """运行 LangGraph 并返回同一搜索契约，保留已有 REST 与 SSE 调用方式。

        参数：
            query：已完成 Query Agent 规划或由用户编辑的检索意图。
            budget_exhausted：调用前已确定的成本或时间预算状态。
            event_publisher：可选的安全进度事件发布器。
        返回：
            MultiRoundSearchResult：实际多轮服务形成的最终稳定结果。
        异常：
            SearchWorkflowError：节点没有形成最终结果时抛出。
        """
        final_state = await self._graph.ainvoke({"query": query, "budget_exhausted": budget_exhausted, "event_publisher": event_publisher, "workflow_status": "queued"})  # 从统一初始状态执行固定节点链。
        result = final_state.get("result")  # 仅从整理完成后的图状态读取最终结果。
        if not isinstance(result, MultiRoundSearchResult):  # 防止未来节点误返回空值或不兼容对象。
            raise SearchWorkflowError("搜索工作流未生成最终结果")  # 向 API 边界提供不含内部图状态的稳定错误。
        return result  # 保持现有调用方的返回对象和字段不变。

    def _build_graph(self):
        """创建初始化、执行和结果整理节点组成的确定性 LangGraph。"""
        graph = StateGraph(SearchWorkflowState)  # 使用 TypedDict 明确节点间可传递的最小状态字段。
        graph.add_node("initialize_run", self._initialize_run)  # 首节点明确标记一次工作流正式开始。
        graph.add_node("execute_multi_round", self._execute_multi_round)  # 中央节点委托既有应用服务执行实际检索。
        graph.add_node("compose_result", self._compose_result)  # 末节点校验并整理供 API 返回的结果。
        graph.add_edge(START, "initialize_run")  # 固定从初始化进入工作流。
        graph.add_edge("initialize_run", "execute_multi_round")  # 初始化完成后才允许来源检索。
        graph.add_edge("execute_multi_round", "compose_result")  # 执行节点返回后统一整理终态。
        graph.add_edge("compose_result", END)  # 结果整理后关闭当前图运行。
        return graph.compile()  # 编译为可通过 ainvoke 执行的异步工作流。

    async def _initialize_run(self, _: SearchWorkflowState) -> dict[str, str]:
        """标记工作流已开始，不在此节点调用外部来源或模型。"""
        return {"workflow_status": "initialized"}  # 保持初始化可观察且不重复创建 SearchRunState。

    async def _execute_multi_round(self, state: SearchWorkflowState) -> dict[str, object]:
        """调用已有多轮服务，保留其来源路由、排序和停止条件职责。"""
        result = await self._executor.run_direct(state["query"], budget_exhausted=state["budget_exhausted"], event_publisher=state["event_publisher"])  # 服务层继续管理真实检索和安全事件发布。
        return {"result": result, "workflow_status": "searched"}  # 仅将稳定最终结果写回图状态。

    async def _compose_result(self, state: SearchWorkflowState) -> dict[str, object]:
        """确认执行节点返回结果并标记工作流完成。"""
        result = state.get("result")  # 读取上游节点写入的唯一结果。
        if not isinstance(result, MultiRoundSearchResult):  # 防止图配置调整后遗漏结果传播。
            raise SearchWorkflowError("搜索执行节点未返回有效结果")  # 返回安全、可处理的编排层错误。
        return {"result": result, "workflow_status": "completed"}  # 为未来状态恢复保留明确的终态标记。
