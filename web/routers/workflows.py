"""Workflow, approval, capability, search, and webhook APIs."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from web.dependencies import get_services
from web.state import WebServices

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workflows"])
SERVICES_DEPENDENCY = Depends(get_services)


def _require_agent(services: WebServices) -> Any:
    """Get system agent, raising 503 if LLM is not configured."""
    try:
        return services.system_agent()
    except Exception as exc:
        msg = str(exc)
        if "api_key" in msg.lower() or "OPENAI_API_KEY" in msg:
            raise HTTPException(
                status_code=503,
                detail=(
                    "LLM not configured. Set the OPENAI_API_KEY environment variable "
                    "or run `pybot-onboard` to configure your API key."
                ),
            ) from exc
        logger.exception("Failed to create system agent")
        raise HTTPException(status_code=500, detail=msg) from exc


class TriggerWorkflowRequest(BaseModel):
    name: str
    input_vars: dict[str, Any] = Field(default_factory=dict)


class WorkflowSaveRequest(BaseModel):
    name: str
    definition: dict[str, Any]


class WorkflowSpecSaveRequest(BaseModel):
    name: str
    spec_content: str

    @model_validator(mode="after")
    def validate_content(self) -> WorkflowSpecSaveRequest:
        if not self.spec_content.strip():
            raise ValueError("spec_content is required")
        return self


class ApprovalResolveRequest(BaseModel):
    approved: bool
    note: str = ""
    approver: str = ""
    labels: list[str] = Field(default_factory=list)
    resume_token: str = ""


@router.post("/api/webhook/{channel_name}")
async def api_channel_webhook(
    channel_name: str,
    payload: dict[str, Any],
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    result = _require_agent(services).channel_manager.handle_incoming(channel_name, payload)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/api/workflows/trigger")
async def api_trigger_workflow(
    req: TriggerWorkflowRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        workflow = engine.load_workflow(req.name)
        if not workflow:
            return {
                "success": False,
                "error": f"工作流 '{req.name}' 不存在",
                "available": [item["name"] for item in engine.list_workflow_files()],
            }
        workflow.variables.update(req.input_vars)
        workflow.variables["input"] = req.input_vars
        return {"success": True, "result": engine.run_workflow(workflow)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/workflows")
async def api_list_workflows(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        return {"saved": engine.list_workflow_files(), "active": engine.list_active_workflows()}
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/workflows/node-types")
async def api_list_node_types() -> dict[str, object]:
    """List all supported workflow node types (Dify-style discovery)."""
    from core.workflow_models import BRANCH_NODE_TYPES, NodeType

    categories = {
        "control": ["start", "end", "condition", "router", "parallel", "merge", "delay"],
        "action": ["exec", "tool", "llm", "code", "http_request", "agent"],
        "data": ["transform", "variable_assigner", "list_operator", "parameter_extractor", "question_classifier"],
        "iteration": ["foreach", "iteration"],
        "collaboration": ["debate", "consensus", "supervisor"],
        "flow": ["approve", "subflow"],
    }
    node_types = []
    for nt in NodeType:
        category = "other"
        for cat, members in categories.items():
            if nt.value in members:
                category = cat
                break
        node_types.append(
            {
                "type": nt.value,
                "category": category,
                "is_branch": nt in BRANCH_NODE_TYPES,
            }
        )
    return {"node_types": node_types}


class SingleNodeRunRequest(BaseModel):
    node_config: dict[str, Any]
    variables: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/workflows/nodes/run")
async def api_run_single_node(
    req: SingleNodeRunRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Run a single workflow node in isolation (Dify-style node testing)."""
    try:
        engine = _require_agent(services).pyflow_engine
        from core.workflow_models import FlowNode, NodeType, WorkflowDef

        node_type_str = req.node_config.get("type", "llm")
        try:
            node_type = NodeType(node_type_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown node type: {node_type_str}") from exc

        node = FlowNode(
            id=req.node_config.get("id", "test_node"),
            type=node_type,
            label=req.node_config.get("label", "Test Node"),
            config={k: v for k, v in req.node_config.items() if k not in ("id", "type", "label")},
        )
        workflow = WorkflowDef(id="__single_node_test__", name="Single Node Test")
        workflow.variables.update(req.variables)

        import time

        start = time.time()
        result = engine.node_runtime.dispatch_node(node, workflow)
        elapsed = round(time.time() - start, 3)

        return {
            "success": True,
            "node_id": node.id,
            "node_type": node_type_str,
            "output": result,
            "elapsed_time": elapsed,
            "variables": {k: v for k, v in workflow.variables.items() if not callable(v)},
        }
    except HTTPException:
        raise
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/workflows/runs")
async def api_list_workflow_runs(
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """List workflow run history (Dify-style run tracking)."""
    try:
        engine = _require_agent(services).pyflow_engine
        runs = engine.execution_runtime.run_history
        return {
            "runs": [run.to_dict() for run in reversed(runs[-50:])],
            "total": len(runs),
        }
    except Exception as exc:
        return {"runs": [], "total": 0, "error": str(exc)}


@router.get("/api/workflows/runs/{run_id}")
async def api_get_workflow_run(
    run_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    """Get details of a specific workflow run."""
    try:
        engine = _require_agent(services).pyflow_engine
        run = engine.execution_runtime.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/workflows/{workflow_id}/graph")
async def api_workflow_graph(
    workflow_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        graph = engine.get_workflow_graph(workflow_id)
        if graph:
            return graph

        try:
            definition = engine.get_workflow_definition(workflow_id)
        except FileNotFoundError:
            return {"error": "工作流不存在"}

        raw_nodes = definition.get("nodes", [])
        if isinstance(raw_nodes, dict):
            raw_nodes = list(raw_nodes.values())

        nodes = [
            {
                "id": node.get("id", ""),
                "type": node.get("type", "exec"),
                "label": node.get("label", node.get("id", "")),
                "status": "pending",
            }
            for node in raw_nodes
        ]

        edges = [
            {
                "from": edge.get("from", edge.get("source", "")),
                "to": edge.get("to", edge.get("target", "")),
            }
            for edge in definition.get("edges", [])
        ]
        if not edges and len(nodes) >= 2:
            edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
        return {"nodes": nodes, "edges": edges, "status": "saved"}
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/capabilities")
async def api_capabilities(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    try:
        return _require_agent(services).capability_bus.get_stats()
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/capabilities/graph")
async def api_capability_graph(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    try:
        return _require_agent(services).capability_bus.get_layer_graph()
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/capabilities/events")
async def api_capability_events(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    try:
        return {"events": _require_agent(services).capability_bus.get_recent_events()}
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/workflows")
async def api_create_workflow(
    req: WorkflowSaveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        name = _require_agent(services).pyflow_engine.create_workflow_definition(req.name, req.definition)
        return {"success": True, "name": name}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.put("/api/workflows/{workflow_id}")
async def api_update_workflow(
    workflow_id: str,
    req: WorkflowSaveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        _require_agent(services).pyflow_engine.update_workflow_definition(workflow_id, req.definition)
        return {"success": True, "name": workflow_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.delete("/api/workflows/{workflow_id}")
async def api_delete_workflow(
    workflow_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        _require_agent(services).pyflow_engine.delete_workflow_definition(workflow_id)
        return {"success": True, "deleted": workflow_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/workflows/{workflow_id}/definition")
async def api_get_workflow_definition(
    workflow_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    from core.workflow_spec import export_workflow_spec, strip_workflow_runtime

    try:
        definition = _require_agent(services).pyflow_engine.get_workflow_definition(workflow_id)
        spec_content = export_workflow_spec(definition)
        return {
            "name": workflow_id,
            "definition": strip_workflow_runtime(definition),
            "spec_content": spec_content,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/workflows/from-spec")
async def api_create_workflow_from_spec(
    req: WorkflowSpecSaveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    from core.workflow_spec import parse_workflow_spec

    try:
        definition = parse_workflow_spec(req.spec_content)
        if not definition.get("name"):
            definition["name"] = req.name
        name = _require_agent(services).pyflow_engine.create_workflow_definition(req.name, definition)
        return {"success": True, "name": name}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.put("/api/workflows/{workflow_id}/from-spec")
async def api_update_workflow_from_spec(
    workflow_id: str,
    req: WorkflowSpecSaveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    from core.workflow_spec import parse_workflow_spec

    try:
        definition = parse_workflow_spec(req.spec_content)
        _require_agent(services).pyflow_engine.update_workflow_definition(workflow_id, definition)
        return {"success": True, "name": workflow_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/approvals")
async def api_get_approvals(services: WebServices = SERVICES_DEPENDENCY) -> dict[str, object]:
    try:
        pending = services.approval_queue.list_pending()
        snapshot = services.approval_queue.get_snapshot()
        return {
            "approvals": pending,
            "pending": pending,
            "recent": services.approval_queue.list_history(limit=25),
            "counts": {
                "pending": snapshot["pending"],
                "approved": snapshot["approved"],
                "rejected": snapshot["rejected"],
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/approvals/{approval_id}/resolve")
async def api_resolve_approval(
    approval_id: str,
    req: ApprovalResolveRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        request = services.approval_queue.get_request(approval_id)
        metadata = request.metadata if request is not None else {}
        result = services.approvals.resolve(
            approval_id,
            approved=req.approved,
            note=req.note,
            approver=req.approver,
            resolution_labels=req.labels,
        )
        if result.get("success"):
            return result
        if req.resume_token:
            workflow_id = str(metadata.get("workflow_id", approval_id)).strip() or approval_id
            return _require_agent(services).pyflow_engine.resume_workflow(
                workflow_id,
                req.resume_token,
                req.approved,
                approval_id=approval_id,
                note=req.note,
                resolved_by=req.approver,
            )
        return result
    except Exception as exc:
        return {"error": str(exc)}


# --- Workflow Version Control ---


class WorkflowPublishRequest(BaseModel):
    commit_id: str | None = None


class WorkflowRollbackRequest(BaseModel):
    commit_id: str


@router.get("/api/workflows/{workflow_id}/versions")
async def api_workflow_versions(
    workflow_id: str,
    limit: int = 20,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        history = engine.get_workflow_history(workflow_id, limit=limit)
        meta = engine.get_workflow_meta(workflow_id)
        return {
            "workflow_id": workflow_id,
            "draft_commit_id": meta.get("draft_commit_id"),
            "published_commit_id": meta.get("published_commit_id"),
            "commits": history,
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/workflows/{workflow_id}/versions/{commit_id}")
async def api_workflow_version_detail(
    workflow_id: str,
    commit_id: str,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        version = engine.get_workflow_version(workflow_id, commit_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")
        return version
    except HTTPException:
        raise
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/workflows/{workflow_id}/publish")
async def api_publish_workflow(
    workflow_id: str,
    req: WorkflowPublishRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        result = engine.publish_workflow(workflow_id, req.commit_id)
        return {"success": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/api/workflows/{workflow_id}/rollback")
async def api_rollback_workflow(
    workflow_id: str,
    req: WorkflowRollbackRequest,
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    try:
        engine = _require_agent(services).pyflow_engine
        result = engine.rollback_workflow(workflow_id, req.commit_id)
        return {"success": True, "draft_commit_id": result.get("draft_commit_id")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/search")
async def api_global_search(
    q: str = "",
    services: WebServices = SERVICES_DEPENDENCY,
) -> dict[str, object]:
    results = []
    query = q.strip().lower()
    if not query:
        return {"results": results}

    try:
        agent = _require_agent(services)
        for name, description in agent.list_tools().items():
            if query in name.lower() or query in description.lower():
                results.append({"type": "tool", "name": name, "description": description})
        for name, description in agent.list_agents().items():
            if query in name.lower() or query in description.lower():
                results.append({"type": "agent", "name": name, "description": description})
        for workflow in agent.pyflow_engine.list_workflow_files():
            workflow_name = workflow.get("name", "") if isinstance(workflow, dict) else str(workflow)
            if query in workflow_name.lower():
                results.append({"type": "workflow", "name": workflow_name, "description": ""})
        for app in services.app_manager.list_apps():
            app_name = app.get("name", "")
            app_description = app.get("description", "")
            if query in app_name.lower() or query in app_description.lower():
                results.append({"type": "app", "name": app_name, "description": app_description})
    except Exception:
        pass
    return {"results": results[:30]}
