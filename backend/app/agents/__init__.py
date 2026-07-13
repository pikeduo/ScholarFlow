"""提供 LangGraph 工作流及其可替换节点装配边界。"""

from backend.app.agents.search_workflow import MultiRoundSearchWorkflow, SearchWorkflowError  # 对外导出多轮搜索实际工作流与稳定编排异常。

__all__ = ["MultiRoundSearchWorkflow", "SearchWorkflowError"]  # 限制 Agent 包的稳定公共接口。
