"""Collaboration-node runtime for workflow agents, debates, and supervisors."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from typing import Any

from .delegation_payload import normalize_delegation_payload
from .workflow_models import FlowNode, NodeType, WorkflowDef
from .workflow_pause_state import apply_waiting_approvals, normalize_pending_approvals


class WorkflowCollaborationRuntime:
    """Execute workflow nodes that rely on agent orchestration."""

    def __init__(
        self,
        *,
        log_event: Callable[[WorkflowDef, str, str, str], None],
        agent_callback: Callable[[str], str] | None = None,
        delegate_callback: Callable[[str, str, str], Any] | None = None,
    ):
        self._log_event = log_event
        self.agent_callback = agent_callback
        self.delegate_callback = delegate_callback

    def set_agent_callback(self, callback: Callable[[str], str] | None) -> None:
        self.agent_callback = callback

    def set_delegate_callback(self, callback: Callable[[str, str, str], Any] | None) -> None:
        self.delegate_callback = callback

    def dispatch_node(self, node: FlowNode, config: dict[str, Any], workflow: WorkflowDef) -> Any:
        if node.type == NodeType.AGENT:
            return self.run_agent(node, config, workflow)
        if node.type == NodeType.DEBATE:
            return self.run_debate(node, config, workflow)
        if node.type == NodeType.CONSENSUS:
            return self.run_consensus(node, config, workflow)
        if node.type == NodeType.SUPERVISOR:
            return self.run_supervisor(node, config, workflow)
        raise ValueError(f"未知协作节点类型: {node.type}")

    def resume_delegated_node(
        self,
        *,
        node: FlowNode,
        workflow: WorkflowDef,
        waiting_payload: dict[str, Any],
        resolved_payload: dict[str, Any],
        resolved_approval_id: str = "",
    ) -> dict[str, Any] | None:
        mode = str(waiting_payload.get("workflow_pause_mode", "")).strip()
        pause_state = waiting_payload.get("workflow_pause_state", {})
        if not isinstance(pause_state, dict):
            pause_state = {}

        if mode == "debate":
            return self.run_debate(
                node,
                node.config,
                workflow,
                pause_state=pause_state,
                resolved_payload=resolved_payload,
            )
        if mode == "consensus":
            return self.run_consensus(
                node,
                node.config,
                workflow,
                pause_state=pause_state,
                resolved_payload=resolved_payload,
                resolved_approval_id=resolved_approval_id,
            )
        return None

    def run_debate(
        self,
        node: FlowNode,
        config: dict[str, Any],
        workflow: WorkflowDef,
        *,
        pause_state: dict[str, Any] | None = None,
        resolved_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        topic = config.get("topic", "")
        agent_a = str(config.get("agent_a", "")).strip()
        agent_b = str(config.get("agent_b", "")).strip()
        judge = str(config.get("judge", "")).strip()
        rounds = int(config.get("rounds", 2) or 2)

        if not all([topic, agent_a, agent_b]):
            raise ValueError("debate 节点必须提供 topic, agent_a, agent_b")
        if not self.delegate_callback:
            raise RuntimeError("未设置 delegate_callback，无法执行 debate")

        state = self._build_debate_state(topic=topic, pause_state=pause_state)
        step_index = int(state.get("step_index", 0))
        history = str(state.get("history", f"辩论主题: {topic}\n\n"))
        transcript = self._copy_list(state.get("transcript"))

        steps = self._build_debate_steps(agent_a=agent_a, agent_b=agent_b, judge=judge, rounds=rounds)
        resumed_once = False

        while step_index < len(steps):
            step = steps[step_index]
            speaker = str(step["speaker"])
            role = str(step["role"])
            round_index = int(step.get("round_index", 0))

            if role == "speaker_a":
                self._log_event(workflow, node.id, "debate_round", f"第 {round_index + 1} 轮辩论开始")

            task = self._build_debate_task(role=role, speaker=speaker, history=history)
            payload = self._consume_or_delegate(
                agent_name=speaker,
                task=task,
                context="",
                resolved_payload=resolved_payload if not resumed_once else None,
            )
            resumed_once = True

            if payload["status"] == "waiting_approval":
                return self._waiting_delegate_result(
                    mode="debate",
                    payload=payload,
                    topic=topic,
                    transcript=transcript,
                    workflow_pause_state={
                        "step_index": step_index,
                        "history": history,
                        "transcript": transcript,
                    },
                )

            if role == "judge":
                self._log_event(workflow, node.id, "debate_judge", "裁判开始总结")
                return {
                    "topic": topic,
                    "transcript": transcript,
                    "conclusion": payload["response"],
                    "judge_result": payload,
                    "response": payload["response"],
                }

            history += f"【{speaker}】:\n{payload['response']}\n\n"
            transcript.append(self._transcript_entry(agent=speaker, payload=payload))
            step_index += 1

        self._log_event(workflow, node.id, "debate_judge", "裁判开始总结")
        judge_task = f"请作为裁判，阅读以下两方智能体的辩论记录，并给出一个客观、综合的最终结论：\n{history}"
        if self.agent_callback:
            final_conclusion = self.agent_callback(judge_task)
        else:
            final_conclusion = "无法进行裁判总结（未配置 judge 且无主 Agent）"

        return {
            "topic": topic,
            "transcript": transcript,
            "conclusion": final_conclusion,
            "response": final_conclusion,
        }

    def run_consensus(
        self,
        node: FlowNode,
        config: dict[str, Any],
        workflow: WorkflowDef,
        *,
        pause_state: dict[str, Any] | None = None,
        resolved_payload: dict[str, Any] | None = None,
        resolved_approval_id: str = "",
    ) -> dict[str, Any]:
        question = str(config.get("question", "")).strip()
        agents = config.get("agents", [])
        aggregator = str(config.get("aggregator", "")).strip()

        if not question or not agents or not isinstance(agents, list):
            raise ValueError("consensus 节点必须提供 question 和 agents 列表")
        if not self.delegate_callback:
            raise RuntimeError("未设置 delegate_callback，无法执行 consensus")

        state = pause_state or {}
        phase = str(state.get("phase", "experts")).strip() or "experts"
        next_agent_index = int(state.get("next_agent_index", 0))
        responses = self._copy_dict(state.get("responses"))
        delegation_results = self._copy_dict(state.get("delegation_results"))
        pending_approvals = normalize_pending_approvals(state.get("pending_approvals"))

        if phase == "experts" and resolved_payload is not None:
            pending_approvals = self._apply_consensus_resolution(
                pending_approvals=pending_approvals,
                responses=responses,
                delegation_results=delegation_results,
                resolved_payload=resolved_payload,
                resolved_approval_id=resolved_approval_id,
                question=question,
            )
            if not pending_approvals and next_agent_index < len(agents):
                resumed_agent_name = str(agents[next_agent_index]).strip()
                if resumed_agent_name and resumed_agent_name not in responses:
                    if resolved_payload["status"] == "waiting_approval" and resolved_payload.get("approval_id"):
                        pending_approvals.append(
                            self._build_pending_consensus_approval(
                                agent_name=resumed_agent_name,
                                task=question,
                                payload=resolved_payload,
                            )
                        )
                    else:
                        responses[resumed_agent_name] = resolved_payload["response"]
                        delegation_results[resumed_agent_name] = normalize_delegation_payload(
                            resolved_payload,
                            agent_name=resumed_agent_name,
                            task=question,
                        )
                    next_agent_index += 1

        if phase == "experts" and next_agent_index == 0 and not responses and not pending_approvals:
            self._log_event(workflow, node.id, "consensus_start", f"向 {len(agents)} 个智能体分发问题")

        while phase == "experts" and next_agent_index < len(agents):
            agent_name = str(agents[next_agent_index]).strip()
            next_agent_index += 1
            if not agent_name:
                continue
            if agent_name in responses:
                continue
            if any(item.get("agent_name") == agent_name for item in pending_approvals):
                continue

            payload = self._delegate(agent_name, question, "")
            if payload["status"] == "waiting_approval":
                pending_approvals.append(
                    self._build_pending_consensus_approval(
                        agent_name=agent_name,
                        task=question,
                        payload=payload,
                    )
                )
                continue

            responses[agent_name] = payload["response"]
            delegation_results[agent_name] = payload

        if phase == "experts" and pending_approvals:
            return self._waiting_delegate_result(
                mode="consensus",
                payload=pending_approvals[0].get("payload", {}),
                question=question,
                expert_responses=responses,
                delegation_results=delegation_results,
                workflow_pause_state={
                    "phase": "experts",
                    "next_agent_index": next_agent_index,
                    "responses": responses,
                    "delegation_results": delegation_results,
                    "pending_approvals": pending_approvals,
                },
                pending_approvals=pending_approvals,
            )

        self._log_event(workflow, node.id, "consensus_aggregate", "开始汇总共识")
        agg_context = f"原始问题: {question}\n\n各专家意见：\n"
        for agent_name in agents:
            response = responses.get(str(agent_name), "")
            agg_context += f"--- 专家 {agent_name} ---\n{response}\n\n"

        agg_task = f"请综合以上专家的意见，提取共识，消除分歧，给出一个最完善的最终回答。\n\n{agg_context}"

        if aggregator and self.delegate_callback:
            payload = self._consume_or_delegate(
                agent_name=aggregator,
                task=agg_task,
                context="",
                resolved_payload=resolved_payload if phase == "aggregate" else None,
            )
            if payload["status"] == "waiting_approval":
                return self._waiting_delegate_result(
                    mode="consensus",
                    payload=payload,
                    question=question,
                    expert_responses=responses,
                    delegation_results=delegation_results,
                    workflow_pause_state={
                        "phase": "aggregate",
                        "responses": responses,
                        "delegation_results": delegation_results,
                    },
                )
            final_answer = payload["response"]
            aggregator_result: dict[str, Any] | None = payload
        elif self.agent_callback:
            final_answer = self.agent_callback(agg_task)
            aggregator_result = None
        else:
            final_answer = "无法进行汇总（未配置 aggregator 且无主 Agent）"
            aggregator_result = None

        return {
            "question": question,
            "expert_responses": responses,
            "delegation_results": delegation_results,
            "aggregator_result": aggregator_result,
            "consensus": final_answer,
            "response": final_answer,
        }

    def run_supervisor(self, node: FlowNode, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        task = config.get("task", "")
        workers = config.get("workers", [])

        if not task or not workers or not isinstance(workers, list):
            raise ValueError("supervisor 节点必须提供 task 和 workers 列表")
        if not self.agent_callback or not self.delegate_callback:
            raise RuntimeError("执行 supervisor 需要同时配置 agent_callback 和 delegate_callback")

        decision_prompt = (
            f"你是一个任务路由主管。你需要将以下任务分配给最合适的专家。\n"
            f"任务: {task}\n"
            f"可用专家列表: {', '.join(workers)}\n"
            f"请仔细思考，然后只输出你选择的专家名称（必须是列表中的一个，不要输出任何其他字符）。"
        )

        self._log_event(workflow, node.id, "supervisor_routing", "主管正在分析并路由任务")
        chosen_worker = self.agent_callback(decision_prompt).strip()
        chosen_worker = self._normalize_chosen_worker(chosen_worker, workers)

        if chosen_worker not in workers:
            self._log_event(
                workflow,
                node.id,
                "supervisor_fallback",
                f"主管选择了未知专家 '{chosen_worker}'，回退至第一个专家 '{workers[0]}'",
            )
            chosen_worker = workers[0]

        self._log_event(workflow, node.id, "supervisor_delegate", f"任务已路由给专家: {chosen_worker}")
        payload = self._delegate(chosen_worker, task, "")
        if payload["status"] == "waiting_approval":
            return self._waiting_delegate_result(
                mode="supervisor",
                payload=payload,
                task=task,
                chosen_worker=chosen_worker,
            )
        return {
            "task": task,
            "chosen_worker": chosen_worker,
            "response": payload["response"],
            "delegation": payload,
            "status": payload["status"],
            "success": payload["success"],
            "approval_id": payload.get("approval_id"),
            "state_update": payload.get("state_update", {}),
            "state_keys": payload.get("state_keys", []),
            "thread_id": payload.get("thread_id"),
        }

    def run_agent(self, node: FlowNode, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        agent_name = config.get("agent_name", "")
        task = config.get("task", "")
        context = config.get("context", "")
        timeout = min(config.get("timeout", 300), 600)
        retry_on_fail = config.get("retry_on_fail", False)

        if not agent_name:
            raise ValueError("agent 节点必须指定 agent_name")
        if not task:
            raise ValueError("agent 节点必须指定 task")

        if not self.delegate_callback:
            if retry_on_fail and self.agent_callback:
                self._log_event(workflow, node.id, "agent_fallback", f"无委派回调，回退到主 Agent 处理: {agent_name}")
                result = self.agent_callback(f"[代替 {agent_name}] {task}\n\n上下文：{context}")
                return {"agent_name": agent_name, "response": result, "fallback": True, "success": True}
            raise RuntimeError(
                "未设置子智能体委派回调（delegate_callback），无法执行 agent 节点。"
                "请在 PyFlowEngine 上调用 set_delegate_callback()。"
            )

        self._log_event(workflow, node.id, "agent_delegate", f"委派给 {agent_name}: {task[:100]}")
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._delegate, agent_name, task, context)
                payload = future.result(timeout=timeout)
            if payload["status"] == "waiting_approval":
                return self._waiting_delegate_result(
                    mode="agent",
                    payload=payload,
                    agent_name=agent_name,
                    task=task,
                    context=context,
                )
            return {
                "agent_name": agent_name,
                "response": payload["response"],
                "delegation": payload,
                "status": payload["status"],
                "success": payload["success"],
                "approval_id": payload.get("approval_id"),
                "thread_id": payload.get("thread_id"),
                "state_update": payload.get("state_update", {}),
                "state_keys": payload.get("state_keys", []),
            }
        except concurrent.futures.TimeoutError as exc:
            err = f"子智能体 '{agent_name}' 执行超时 ({timeout}s)"
            self._log_event(workflow, node.id, "agent_timeout", err)
            if retry_on_fail and self.agent_callback:
                result = self.agent_callback(f"[代替超时的 {agent_name}] {task}\n\n上下文：{context}")
                return {"agent_name": agent_name, "response": result, "fallback": True, "success": True, "warning": err}
            raise RuntimeError(err) from exc
        except Exception as exc:
            self._log_event(workflow, node.id, "agent_error", str(exc))
            if retry_on_fail and self.agent_callback:
                result = self.agent_callback(f"[代替失败的 {agent_name}] {task}\n\n上下文：{context}")
                return {
                    "agent_name": agent_name,
                    "response": result,
                    "fallback": True,
                    "success": True,
                    "warning": f"子智能体失败: {exc}",
                }
            raise

    @staticmethod
    def _build_debate_state(*, topic: str, pause_state: dict[str, Any] | None) -> dict[str, Any]:
        state = pause_state or {}
        return {
            "step_index": int(state.get("step_index", 0)),
            "history": str(state.get("history", f"辩论主题: {topic}\n\n")),
            "transcript": WorkflowCollaborationRuntime._copy_list(state.get("transcript")),
        }

    @staticmethod
    def _build_debate_steps(*, agent_a: str, agent_b: str, judge: str, rounds: int) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for round_index in range(rounds):
            steps.append({"role": "speaker_a", "speaker": agent_a, "round_index": round_index})
            steps.append({"role": "speaker_b", "speaker": agent_b, "round_index": round_index})
        if judge:
            steps.append({"role": "judge", "speaker": judge, "round_index": rounds})
        return steps

    @staticmethod
    def _build_debate_task(*, role: str, speaker: str, history: str) -> str:
        if role == "judge":
            return f"请作为裁判，阅读以下两方智能体的辩论记录，并给出一个客观、综合的最终结论：\n{history}"
        return f"请针对以下主题和历史记录，提出你的观点或反驳对方：\n{history}"

    def _consume_or_delegate(
        self,
        *,
        agent_name: str,
        task: str,
        context: str,
        resolved_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if resolved_payload is not None:
            return normalize_delegation_payload(resolved_payload, agent_name=agent_name, task=task)
        return self._delegate(agent_name, task, context)

    @staticmethod
    def _normalize_chosen_worker(chosen_worker: str, workers: list[str]) -> str:
        for worker in workers:
            if worker.lower() in chosen_worker.lower():
                return worker
        return chosen_worker

    def _delegate(self, agent_name: str, task: str, context: str) -> dict[str, Any]:
        if not self.delegate_callback:
            raise RuntimeError("未设置 delegate_callback，无法执行委派")
        raw_result = self.delegate_callback(agent_name, task, context)
        return normalize_delegation_payload(raw_result, agent_name=agent_name, task=task)

    @staticmethod
    def _transcript_entry(*, agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "agent": agent,
            "content": payload.get("response", ""),
            "status": payload.get("status", "completed"),
            "success": payload.get("success", True),
            "approval_id": payload.get("approval_id"),
            "thread_id": payload.get("thread_id"),
            "state_keys": payload.get("state_keys", []),
            "state_update": payload.get("state_update", {}),
        }

    @staticmethod
    def _waiting_delegate_result(
        mode: str,
        payload: dict[str, Any],
        *,
        pending_approvals: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        workflow_pause_state = extra.pop("workflow_pause_state", None)
        result = {
            **extra,
            "status": "waiting_approval",
            "success": False,
            "response": payload.get("response", ""),
            "approval_id": payload.get("approval_id"),
            "thread_id": payload.get("thread_id"),
            "workflow_pause_kind": "delegated_subagent",
            "workflow_pause_mode": mode,
            "delegation": payload,
            "state_update": payload.get("state_update", {}),
            "state_keys": payload.get("state_keys", []),
        }
        if isinstance(workflow_pause_state, dict) and workflow_pause_state:
            result["workflow_pause_state"] = workflow_pause_state
        return apply_waiting_approvals(result, pending_approvals)

    def _apply_consensus_resolution(
        self,
        *,
        pending_approvals: list[dict[str, Any]],
        responses: dict[str, Any],
        delegation_results: dict[str, Any],
        resolved_payload: dict[str, Any],
        resolved_approval_id: str,
        question: str,
    ) -> list[dict[str, Any]]:
        if not pending_approvals:
            return []

        pending = list(pending_approvals)
        match_index = self._find_pending_approval_index(
            pending_approvals=pending,
            resolved_approval_id=resolved_approval_id,
            resolved_payload=resolved_payload,
        )
        if match_index is None:
            return pending

        matched = pending.pop(match_index)
        agent_name = str(matched.get("agent_name", "")).strip()
        if not agent_name:
            return pending

        if resolved_payload["status"] == "waiting_approval" and resolved_payload.get("approval_id"):
            pending.append(
                self._build_pending_consensus_approval(
                    agent_name=agent_name,
                    task=str(matched.get("task", question)),
                    payload=resolved_payload,
                    context=str(matched.get("context", "")),
                )
            )
            return pending

        responses[agent_name] = resolved_payload["response"]
        delegation_results[agent_name] = normalize_delegation_payload(
            resolved_payload,
            agent_name=agent_name,
            task=str(matched.get("task", question)),
        )
        return pending

    @staticmethod
    def _find_pending_approval_index(
        *,
        pending_approvals: list[dict[str, Any]],
        resolved_approval_id: str,
        resolved_payload: dict[str, Any],
    ) -> int | None:
        target_approval_id = str(resolved_approval_id).strip()
        if target_approval_id:
            for index, pending in enumerate(pending_approvals):
                if str(pending.get("approval_id", "")).strip() == target_approval_id:
                    return index

        target_agent = str(resolved_payload.get("agent_name", "")).strip()
        if target_agent:
            for index, pending in enumerate(pending_approvals):
                if str(pending.get("agent_name", "")).strip() == target_agent:
                    return index

        return 0 if pending_approvals else None

    @staticmethod
    def _build_pending_consensus_approval(
        *,
        agent_name: str,
        task: str,
        payload: dict[str, Any],
        context: str = "",
    ) -> dict[str, Any]:
        return {
            "agent_name": agent_name,
            "task": task,
            "context": context,
            "approval_id": payload.get("approval_id"),
            "thread_id": payload.get("thread_id"),
            "response": payload.get("response", ""),
            "payload": payload,
        }

    @staticmethod
    def _copy_dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _copy_list(value: Any) -> list[Any]:
        return list(value) if isinstance(value, list) else []
