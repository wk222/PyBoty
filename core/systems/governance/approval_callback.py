from typing import Any, Dict, List, Callable, Optional
from langchain_core.callbacks import BaseCallbackHandler

class ApprovalRequiredException(Exception):
    """自定义异常，用于中断 AgentExecutor 并请求人工审批"""
    def __init__(self, tool_name: str, tool_input: str):
        self.tool_name = tool_name
        self.tool_input = tool_input
        super().__init__(f"Tool '{tool_name}' requires human approval.")

class GovernanceApprovalCallback(BaseCallbackHandler):
    """
    支持同步交互式确认的审批拦截器。
    将高危操作的拦截逻辑从复杂的 Middleware 剥离到 CallbackHandler 中。
    """
    def __init__(
        self, 
        high_risk_tools: List[str], 
        approval_queue: Any = None, 
        thread_id: str = "default",
        ask_user_fn: Optional[Callable[[str, str], bool]] = None
    ):
        self.high_risk_tools = high_risk_tools
        self.approval_queue = approval_queue
        self.thread_id = thread_id
        self.ask_user_fn = ask_user_fn
        self.raise_error = True

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """在工具真正执行前触发"""
        tool_name = serialized.get("name")
        
        if tool_name in self.high_risk_tools:
            print(f"\n[治理中心] ⚠️ 拦截到高危工具调用: {tool_name}")
            
            # 如果提供了同步的交互式询问函数（如 CLI 的 input()），则阻塞等待确认
            if self.ask_user_fn:
                print(f"[治理中心] 参数: {input_str}")
                approved = self.ask_user_fn(tool_name, input_str)
                if not approved:
                    # 用户拒绝，抛出异常中断执行
                    raise ApprovalRequiredException(tool_name=tool_name, tool_input=input_str)
                else:
                    print(f"[治理中心] ✅ 用户已批准执行 {tool_name}")
                    return # 批准执行，直接返回，不抛出异常

            # 如果没有同步交互函数，但配置了异步审批队列（App Matrix / Web 模式）
            elif self.approval_queue:
                # 为了简化，这里我们直接抛出异常中断执行，后续可以在外层捕获并返回给前端
                # 前端可以展示“等待审批”的 UI
                raise ApprovalRequiredException(tool_name=tool_name, tool_input=input_str)
            else:
                # 如果既没有同步交互，也没有异步队列，默认记录日志并放行（或者按需抛出异常）
                print(f"[治理中心] ⚠️ 警告：执行高危工具 {tool_name}，但未配置审批机制，默认放行。")
