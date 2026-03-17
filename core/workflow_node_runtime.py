"""Generic workflow node execution runtime extracted from the engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from core.approval_queue import ApprovalQueue
from core.project_paths import ProjectPaths
from core.workflow_models import FlowNode, NodeStatus, NodeType, WorkflowDef, WorkflowStatus
from core.workflow_nodes_extended import (
    run_http_request,
    run_iteration,
    run_list_operator,
    run_parameter_extractor,
    run_question_classifier,
    run_variable_assigner,
)
from core.workflow_pause_state import extract_waiting_approval_ids, primary_waiting_approval_id


class WorkflowApprovalPause(Exception):
    """Raised when a workflow needs human approval before continuing."""

    def __init__(self, workflow_id: str, node_id: str, resume_token: str, prompt: str, approval_id: str):
        self.workflow_id = workflow_id
        self.node_id = node_id
        self.resume_token = resume_token
        self.prompt = prompt
        self.approval_id = approval_id
        super().__init__(f"Workflow paused for approval: {prompt}")


class WorkflowNodeRuntime:
    """Execute built-in workflow nodes while leaving orchestration to the engine."""

    def __init__(
        self,
        *,
        workspace_dir: str,
        approval_queue: ApprovalQueue,
        save_workflow: Any,
        load_workflow: Any,
        resume_workflow: Any,
        run_workflow: Any,
        resolve_var: Any,
        resolve_config: Any,
        evaluate_condition: Any,
        get_predecessors: Any,
        workflow_approval_fingerprint: Any,
        log_event: Any,
        extra_dispatch: Any,
        tool_callback: Any = None,
        agent_callback: Any = None,
    ):
        self.workspace_dir = workspace_dir
        self.approval_queue = approval_queue
        self.save_workflow = save_workflow
        self.load_workflow = load_workflow
        self.resume_workflow = resume_workflow
        self.run_workflow = run_workflow
        self.resolve_var = resolve_var
        self.resolve_config = resolve_config
        self.evaluate_condition = evaluate_condition
        self.get_predecessors = get_predecessors
        self.workflow_approval_fingerprint = workflow_approval_fingerprint
        self.log_event = log_event
        self.extra_dispatch = extra_dispatch
        self.tool_callback = tool_callback
        self.agent_callback = agent_callback

    def exec_node(self, node: FlowNode, workflow: WorkflowDef) -> Any:
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()
        self.log_event(workflow, node.id, "start", f"type={node.type.value}")

        try:
            result = self.dispatch_node(node, workflow)
            self._raise_delegated_pause_if_needed(node=node, workflow=workflow, result=result)
            node.status = NodeStatus.COMPLETED
            node.output = result
            node.completed_at = time.time()
            workflow.variables[f"{node.id}.output"] = result
            workflow.variables[f"{node.id}.status"] = "completed"
            self.log_event(workflow, node.id, "completed", str(result)[:200] if result else "")
            return result
        except WorkflowApprovalPause:
            raise
        except Exception as exc:
            max_retries = int(node.config.get("retries", 0))
            if node.retry_count < max_retries:
                node.retry_count += 1
                delay = node.config.get("retry_delay", 2) * (2 ** (node.retry_count - 1))
                self.log_event(workflow, node.id, "retry", f"{node.retry_count}/{max_retries}, delay={delay}s")
                time.sleep(delay)
                node.status = NodeStatus.PENDING
                return self.exec_node(node, workflow)

            node.status = NodeStatus.FAILED
            node.error = str(exc)
            node.completed_at = time.time()
            workflow.variables[f"{node.id}.status"] = "failed"
            workflow.variables[f"{node.id}.error"] = str(exc)
            self.log_event(workflow, node.id, "failed", str(exc))
            raise

    def dispatch_node(self, node: FlowNode, workflow: WorkflowDef) -> Any:
        config = self.resolve_config(node.config, workflow)

        if node.type == NodeType.START:
            return config.get("input", workflow.variables.get("input", {}))
        if node.type == NodeType.END:
            output_key = config.get("output")
            if isinstance(output_key, str) and output_key:
                return self.resolve_var(output_key, workflow)
            return {
                key: value
                for key, value in workflow.variables.items()
                if not key.startswith("_") and not callable(value)
            }
        if node.type == NodeType.EXEC:
            return self._run_exec(config)
        if node.type == NodeType.TOOL:
            return self._run_tool(config)
        if node.type == NodeType.LLM:
            return self._run_llm(config)
        if node.type == NodeType.CODE:
            return self._run_code(config)
        if node.type == NodeType.APPROVE:
            return self._run_approve(node, config, workflow)
        if node.type == NodeType.CONDITION:
            return self._run_condition(config, workflow)
        if node.type == NodeType.ROUTER:
            return self._run_router(config, workflow)
        if node.type == NodeType.PARALLEL:
            return self._run_parallel(config, workflow)
        if node.type == NodeType.FOREACH:
            return self._run_foreach(config, workflow)
        if node.type == NodeType.SUBFLOW:
            return self._run_subflow(config, workflow)
        if node.type == NodeType.TRANSFORM:
            return self._run_transform(config, workflow)
        if node.type == NodeType.MERGE:
            return self._run_merge(node, config, workflow)
        if node.type == NodeType.DELAY:
            seconds = config.get("seconds", 1)
            time.sleep(min(seconds, 300))
            return {"delayed": seconds}
        if node.type == NodeType.HTTP_REQUEST:
            return run_http_request(config)
        if node.type == NodeType.QUESTION_CLASSIFIER:
            return run_question_classifier(config, self.agent_callback)
        if node.type == NodeType.VARIABLE_ASSIGNER:
            return run_variable_assigner(config, workflow.variables, lambda v: self.resolve_var(v, workflow))
        if node.type == NodeType.LIST_OPERATOR:
            return run_list_operator(
                config,
                lambda v: self.resolve_var(v, workflow),
                lambda c, w=workflow: self.evaluate_condition(c, w),
            )
        if node.type == NodeType.PARAMETER_EXTRACTOR:
            return run_parameter_extractor(config, self.agent_callback)
        if node.type == NodeType.ITERATION:
            return run_iteration(
                config,
                workflow.variables,
                lambda v: self.resolve_var(v, workflow),
                lambda body: self.resolve_config(body, workflow),
                lambda body, item, w=workflow: self._run_foreach_body(body, item, w),
            )
        return self.extra_dispatch(node, config, workflow)

    def _run_exec(self, config: dict[str, Any]) -> dict[str, Any]:
        command = config.get("command", "")
        timeout = min(config.get("timeout", 30), 120)
        cwd = config.get("cwd", self.workspace_dir)
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"命令超时 ({timeout}s): {command}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"命令失败 (exit {result.returncode}): {(result.stderr or result.stdout)[:500]}")
        return {
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-1000:],
            "returncode": result.returncode,
            "success": True,
        }

    def _run_tool(self, config: dict[str, Any]) -> Any:
        if not self.tool_callback:
            raise RuntimeError("未设置工具回调，无法执行 tool 节点")
        return self.tool_callback(config.get("tool"), config.get("args", {}))

    def _run_llm(self, config: dict[str, Any]) -> str:
        if not self.agent_callback:
            raise RuntimeError("未设置 Agent 回调，无法执行 llm 节点")
        return self.agent_callback(config.get("prompt", ""))

    def _run_code(self, config: dict[str, Any]) -> dict[str, Any]:
        code = config.get("code", "")
        language = config.get("language", "python")
        timeout = min(config.get("timeout", 15), 30)
        tmp_dir = os.path.join(str(ProjectPaths.from_root().tools_workspace_dir), "pyflow_code")
        os.makedirs(tmp_dir, exist_ok=True)

        if language in ("python", "py"):
            script = os.path.join(tmp_dir, f"pf_{uuid.uuid4().hex[:8]}.py")
            runner = [sys.executable, script]
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        elif language in ("javascript", "js", "node"):
            script = os.path.join(tmp_dir, f"pf_{uuid.uuid4().hex[:8]}.js")
            runner = ["node", script]
            env = None
        else:
            raise ValueError(f"不支持的语言: {language}")

        with open(script, "w", encoding="utf-8") as file:
            file.write(code)
        try:
            result = subprocess.run(
                runner,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workspace_dir,
                env=env,
            )
        finally:
            try:
                os.remove(script)
            except OSError:
                pass

        if result.returncode != 0:
            raise RuntimeError(f"代码执行失败: {result.stderr[:500]}")
        return {"stdout": result.stdout[-3000:], "success": True}

    def _run_approve(self, node: FlowNode, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        node.status = NodeStatus.WAITING
        workflow.resume_token = uuid.uuid4().hex
        workflow.status = WorkflowStatus.PAUSED
        self.save_workflow(workflow)
        prompt = config.get("prompt", "是否继续执行?")
        approval = self.approval_queue.create_request(
            kind="workflow_node",
            scope=f"workflow:{workflow.id}",
            summary=f"工作流审批: {workflow.name} / {node.label or node.id}",
            prompt=prompt,
            metadata={
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "node_id": node.id,
                "node_label": node.label,
                "resume_token": workflow.resume_token,
            },
            fingerprint=self.workflow_approval_fingerprint(
                workflow_id=workflow.id,
                node_id=node.id,
                resume_token=workflow.resume_token,
            ),
            callback=lambda approved, note: self.resume_workflow(
                workflow.id,
                workflow.resume_token or "",
                approved,
                note=note,
            ),
            labels=["workflow-node", f"workflow:{workflow.name}", f"node:{node.type.value}"],
            policy_tags=["workflow-approval", f"node-type:{node.type.value}"],
        )
        raise WorkflowApprovalPause(
            workflow_id=workflow.id,
            node_id=node.id,
            resume_token=workflow.resume_token,
            prompt=prompt,
            approval_id=approval.approval_id,
        )

    def _raise_delegated_pause_if_needed(
        self,
        *,
        node: FlowNode,
        workflow: WorkflowDef,
        result: Any,
    ) -> None:
        if not isinstance(result, dict):
            return
        if str(result.get("workflow_pause_kind", "")).strip() != "delegated_subagent":
            return

        approval_ids = extract_waiting_approval_ids(result)
        approval_id = primary_waiting_approval_id(result)
        if not approval_id:
            raise RuntimeError(f"工作流节点 '{node.id}' 等待委派审批，但缺少 approval_id")

        workflow.resume_token = uuid.uuid4().hex
        workflow.status = WorkflowStatus.PAUSED
        node.status = NodeStatus.WAITING
        node.output = {**result, "resume_token": workflow.resume_token}
        workflow.variables[f"{node.id}.status"] = "waiting_approval"
        workflow.variables[f"{node.id}.approval_id"] = approval_id
        workflow.variables[f"{node.id}.output"] = node.output

        prompt = str(result.get("response", "")).strip() or "子智能体委派已暂停，等待审批。"
        for pending_approval_id in approval_ids or [approval_id]:
            request = self.approval_queue.get_request(pending_approval_id)
            if request is None:
                continue
            self.approval_queue.update_request_metadata(
                pending_approval_id,
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                workflow_node_id=node.id,
                workflow_node_label=node.label,
                workflow_resume_token=workflow.resume_token,
                workflow_pause_kind="delegated_subagent",
                workflow_pause_mode=str(result.get("workflow_pause_mode", "")).strip(),
            )
            if pending_approval_id == approval_id:
                prompt = request.prompt or prompt
        self.save_workflow(workflow)
        raise WorkflowApprovalPause(
            workflow_id=workflow.id,
            node_id=node.id,
            resume_token=workflow.resume_token,
            prompt=prompt,
            approval_id=approval_id,
        )

    def _run_condition(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        expr = config.get("expression", config.get("condition", "true"))
        result = self.evaluate_condition(expr, workflow)
        return {"result": result, "_branch": config.get("true_branch") if result else config.get("false_branch")}

    def _run_router(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        selected = config.get("default")
        for route in config.get("routes", []):
            if self.evaluate_condition(route.get("condition", ""), workflow):
                selected = route.get("target", "")
                break
        return {"selected_route": selected, "_route_target": selected}

    def _run_parallel(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        threads: list[threading.Thread] = []

        def run_branch(branch_cfg: dict[str, Any], branch_id: str) -> None:
            try:
                results[branch_id] = self._run_branch_action(branch_cfg, workflow)
            except Exception as exc:  # noqa: PERF203
                errors[branch_id] = str(exc)

        for index, branch in enumerate(config.get("branches", [])):
            branch_id = branch.get("id", f"branch_{index}")
            thread = threading.Thread(target=run_branch, args=(branch, branch_id))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        timed_out = [thread.name for thread in threads if thread.is_alive()]
        if timed_out:
            errors["_timeout"] = f"分支超时未完成: {timed_out}"
        if errors and not config.get("ignore_errors", False):
            raise RuntimeError(f"并行分支执行失败: {errors}")
        return {"branches": results, "errors": errors}

    def _run_foreach(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        items_ref = config.get("items", "[]")
        items = self.resolve_var(items_ref, workflow) if isinstance(items_ref, str) else items_ref
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                items = [items]
        if not isinstance(items, list):
            items = [items]

        body = config.get("body", {})
        max_items = config.get("max_items", 100)
        results = []
        for index, item in enumerate(items[:max_items]):
            workflow.variables["_foreach_item"] = item
            workflow.variables["_foreach_index"] = index
            try:
                resolved_body = self.resolve_config(body, workflow)
                results.append(
                    {
                        "index": index,
                        "item": item,
                        "result": self._run_foreach_body(resolved_body, item, workflow),
                    }
                )
            except Exception as exc:  # noqa: PERF203
                results.append({"index": index, "item": item, "error": str(exc)})
                if not config.get("ignore_errors", False):
                    break

        workflow.variables.pop("_foreach_item", None)
        workflow.variables.pop("_foreach_index", None)
        return {"items_count": len(items[:max_items]), "results": results}

    def _run_subflow(self, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        subflow_name = config.get("workflow", "")
        sub_workflow = self.load_workflow(subflow_name)
        if not sub_workflow:
            raise ValueError(f"子工作流 '{subflow_name}' 不存在")
        sub_workflow.id = uuid.uuid4().hex[:12]
        for key, value in config.get("input", {}).items():
            sub_workflow.variables[key] = self.resolve_var(value, workflow) if isinstance(value, str) else value
        return {"subflow": subflow_name, "result": self.run_workflow(sub_workflow)}

    def _run_transform(self, config: dict[str, Any], workflow: WorkflowDef) -> Any:
        operation = config.get("operation", "passthrough")
        data = config.get("data")
        if isinstance(data, str):
            data = self.resolve_var(data, workflow)
        if operation == "json_parse":
            return json.loads(data) if isinstance(data, str) else data
        if operation == "json_stringify":
            return json.dumps(data, ensure_ascii=False, indent=2)
        if operation == "extract":
            return data.get(config.get("key", ""), None) if isinstance(data, dict) else None
        if operation == "merge":
            merged = {}
            for source in config.get("sources", []):
                value = self.resolve_var(source, workflow) if isinstance(source, str) else source
                if isinstance(value, dict):
                    merged.update(value)
            return merged
        if operation == "filter" and isinstance(data, list):
            filtered = []
            for item in data:
                workflow.variables["_item"] = item
                if self.evaluate_condition(config.get("condition", "true"), workflow):
                    filtered.append(item)
            workflow.variables.pop("_item", None)
            return filtered
        if operation == "map" and isinstance(data, list):
            mapped = []
            for item in data:
                workflow.variables["_item"] = item
                mapped.append(self.resolve_var(config.get("expression", "_item"), workflow))
            workflow.variables.pop("_item", None)
            return mapped
        if operation == "template":
            return self.resolve_var(config.get("template", ""), workflow)
        return data

    def _run_merge(self, node: FlowNode, config: dict[str, Any], workflow: WorkflowDef) -> dict[str, Any]:
        merged = {}
        for predecessor in self.get_predecessors(workflow, node.id):
            pred_node = workflow.nodes.get(predecessor)
            if pred_node and pred_node.output is not None:
                merged[predecessor] = pred_node.output
        if config.get("strategy", "collect") == "flatten" and all(isinstance(value, dict) for value in merged.values()):
            flattened = {}
            for value in merged.values():
                flattened.update(value)
            return flattened
        return merged

    def _run_branch_action(self, branch_cfg: dict[str, Any], workflow: WorkflowDef) -> Any:
        if "command" in branch_cfg:
            return self._run_exec(branch_cfg)
        if "tool" in branch_cfg:
            return self._run_tool(branch_cfg)
        if "prompt" in branch_cfg:
            return self._run_llm(branch_cfg)
        if "code" in branch_cfg:
            return self._run_code(branch_cfg)
        return self.extra_dispatch(
            FlowNode(id=branch_cfg.get("id", "branch"), type=NodeType(branch_cfg.get("type", "agent"))),
            branch_cfg,
            workflow,
        )

    def _run_foreach_body(self, body: dict[str, Any], item: Any, workflow: WorkflowDef) -> Any:
        if "command" in body:
            command = body["command"]
            if isinstance(command, str):
                command = command.replace("${_foreach_item}", json.dumps(item) if not isinstance(item, str) else item)
            return self._run_exec({**body, "command": command})
        if "tool" in body:
            return self._run_tool(body)
        if "prompt" in body:
            prompt = body["prompt"]
            if isinstance(prompt, str):
                prompt = prompt.replace("${_foreach_item}", json.dumps(item) if not isinstance(item, str) else item)
            return self._run_llm({**body, "prompt": prompt})
        if "code" in body:
            return self._run_code(body)
        return self.extra_dispatch(
            FlowNode(id="foreach_body", type=NodeType(body.get("type", "agent"))), body, workflow
        )
