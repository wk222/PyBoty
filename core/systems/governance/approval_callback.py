import sys
from typing import Any, Dict, List, Callable, Optional
from langchain_core.callbacks import BaseCallbackHandler


class ApprovalRequiredException(Exception):
    """自定义异常，用于中断 AgentExecutor 并请求人工审批"""
    def __init__(self, tool_name: str, tool_input: str, approval_id: str | None = None):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.approval_id = approval_id
        super().__init__(f"Tool '{tool_name}' requires human approval.")


class GovernanceApprovalCallback(BaseCallbackHandler):
    """
    支持同步交互式确认和异步审批队列的审批拦截器。

    路由逻辑（按优先级）：
    1. 若提供了 ask_user_fn（CLI 交互模式）：阻塞等待用户 Y/N。
       - 批准 → 继续执行；拒绝 → 抛出 ApprovalRequiredException。
    2. 若配置了 approval_queue（Web / 服务模式）：向队列创建审批请求，
       随后抛出 ApprovalRequiredException（携带 approval_id）让上层中断执行。
    3. 两者均未配置：记录警告日志，默认放行。
    """

    def __init__(
        self,
        high_risk_tools: List[str],
        approval_queue: Any = None,
        thread_id: str = "default",
        ask_user_fn: Optional[Callable[[str, str], bool]] = None,
    ):
        self.high_risk_tools = high_risk_tools
        self.approval_queue = approval_queue
        self.thread_id = thread_id
        self.ask_user_fn = ask_user_fn
        self.raise_error = True

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """在工具真正执行前触发"""
        tool_name = serialized.get("name")

        if tool_name not in self.high_risk_tools:
            return

        print(f"\n[治理中心] ⚠️ 拦截到高危工具调用: {tool_name}")

        if self.ask_user_fn:
            print(f"[治理中心] 参数: {input_str}")
            approved = self.ask_user_fn(tool_name, input_str)
            if not approved:
                raise ApprovalRequiredException(tool_name=tool_name, tool_input=input_str)
            print(f"[治理中心] ✅ 用户已批准执行 {tool_name}")
            return

        if self.approval_queue:
            approval_id: str | None = None
            try:
                from core.systems.governance.approval_queue import InterruptKind
                request = self.approval_queue.create_request(
                    kind=InterruptKind.TOOL_APPROVAL,
                    scope=self.thread_id,
                    summary=f"高危工具 '{tool_name}' 请求执行授权",
                    prompt=f"工具 `{tool_name}` 即将执行，参数如下：\n{input_str}\n\n是否批准？",
                    metadata={"tool_name": tool_name, "tool_input": input_str},
                    fingerprint=f"{self.thread_id}:{tool_name}",
                )
                approval_id = request.approval_id
                print(f"[治理中心] 已创建审批请求 {approval_id}，等待前端确认。")
            except Exception as exc:
                print(f"[治理中心] ⚠️ 无法创建审批请求: {exc}")
            raise ApprovalRequiredException(
                tool_name=tool_name,
                tool_input=input_str,
                approval_id=approval_id,
            )

        print(f"[治理中心] ⚠️ 警告：执行高危工具 {tool_name}，但未配置审批机制，默认放行。")
