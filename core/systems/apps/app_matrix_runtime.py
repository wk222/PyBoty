"""Service layer for the APP Brain mode.

This runtime keeps the orchestration registry aligned with real apps and
provides higher-level helpers for app-to-app coordination plans.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .app_manager import AppDefinition, AppManager
from .app_orchestration import AppOrchestrationRegistry, NodeStatus, NodeType, OrchestrationNode

logger = logging.getLogger(__name__)


class AppMatrixRuntime:
    """Coordinate app topology for the APP Brain root mode."""

    def __init__(
        self,
        *,
        app_manager: AppManager,
        orchestration_registry: AppOrchestrationRegistry,
        capability_registry: Any | None = None,
        pyflow_engine: Any | None = None,
    ) -> None:
        self.app_manager = app_manager
        self.registry = orchestration_registry
        self.capability_registry = capability_registry
        self.pyflow_engine = pyflow_engine

    def sync_apps(self, *, clear_missing: bool = False) -> dict[str, Any]:
        """Sync generated apps into the orchestration registry."""
        synced_nodes: list[dict[str, Any]] = []
        current_app_names = {app.name for app in self.app_manager.apps.values()}

        for app in self.app_manager.apps.values():
            node = self.registry.upsert_node(
                app.name,
                NodeType.APP,
                description=app.description,
                domain=self._resolve_domain(app),
                owner=app.author,
                metadata=self._build_app_metadata(app),
            )
            self.registry.update_node_status(
                node.node_id,
                NodeStatus.ACTIVE if app.enabled else NodeStatus.INACTIVE,
            )
            synced_nodes.append(node.to_dict())

        removed_nodes: list[str] = []
        if clear_missing:
            for node in self.registry.list_nodes(node_type=NodeType.APP):
                if node.metadata.get("source") != "app_manager":
                    continue
                if node.name in current_app_names:
                    continue
                if self.registry.unregister_node(node.node_id):
                    removed_nodes.append(node.name)

        return {
            "synced": synced_nodes,
            "removed": removed_nodes,
            "issues": self.registry.validate_graph(),
            "topology_stats": self.registry.get_topology()["stats"],
        }

    def sync_workflows(self, *, clear_missing: bool = False) -> dict[str, Any]:
        """Sync registered PyFlow workflows into the orchestration registry as WORKFLOW nodes."""
        if self.pyflow_engine is None:
            return {"synced": [], "removed": [], "skipped": "pyflow_engine_unavailable"}

        synced: list[dict[str, Any]] = []
        current_names: set[str] = set()
        try:
            workflow_files = self.pyflow_engine.list_workflow_files()
        except Exception as exc:
            logger.debug("[AppMatrix] list_workflow_files failed: %s", exc)
            workflow_files = []

        for entry in workflow_files:
            wf_name = str(entry.get("name") or entry.get("id") or "")
            if not wf_name:
                continue
            current_names.add(wf_name)
            node = self.registry.upsert_node(
                wf_name,
                NodeType.WORKFLOW,
                description=str(entry.get("description", "")),
                domain=str(entry.get("category", "workflow")),
                owner=str(entry.get("author", "")),
                metadata={
                    "source": "pyflow_engine",
                    "workflow_id": entry.get("id", wf_name),
                    "version": entry.get("version", ""),
                    "schedule": entry.get("schedule", ""),
                    "tags": list(entry.get("tags", [])),
                    "node_count": entry.get("node_count", 0),
                },
            )
            self.registry.update_node_status(node.node_id, NodeStatus.ACTIVE)
            synced.append(node.to_dict())

        removed: list[str] = []
        if clear_missing:
            for node in self.registry.list_nodes(node_type=NodeType.WORKFLOW):
                if node.metadata.get("source") != "pyflow_engine":
                    continue
                if node.name in current_names:
                    continue
                if self.registry.unregister_node(node.node_id):
                    removed.append(node.name)

        return {"synced": synced, "removed": removed}

    def sync_subagents(self, agent_storage: Any, *, clear_missing: bool = False) -> dict[str, Any]:
        """Sync agent definitions from ``AgentStorage`` into the registry as AGENT nodes.

        Accepts any object exposing ``list_agents() -> dict[str, str]`` (mapping
        agent name → short description). When the storage exposes an
        ``agents`` dict of full ``AgentDefinition`` objects, additional metadata
        (capabilities, tools, mode) is captured.
        """
        synced: list[dict[str, Any]] = []
        current_names: set[str] = set()
        try:
            name_to_desc = agent_storage.list_agents() or {}
        except Exception as exc:
            logger.debug("[AppMatrix] list_agents failed: %s", exc)
            return {"synced": [], "removed": [], "skipped": "agent_listing_failed"}

        full_defs = getattr(agent_storage, "agents", {}) or {}
        for name, description in name_to_desc.items():
            current_names.add(str(name))
            agent_def = full_defs.get(name) if isinstance(full_defs, dict) else None
            metadata: dict[str, Any] = {"source": "agent_storage"}
            if agent_def is not None:
                metadata["capabilities"] = list(getattr(agent_def, "capabilities", []) or [])
                metadata["tools"] = list(getattr(agent_def, "tools", []) or [])
                metadata["mode"] = getattr(agent_def, "mode", "")
            node = self.registry.upsert_node(
                str(name),
                NodeType.AGENT,
                description=str(description),
                domain="agent",
                metadata=metadata,
            )
            self.registry.update_node_status(node.node_id, NodeStatus.ACTIVE)
            synced.append(node.to_dict())

        removed: list[str] = []
        if clear_missing:
            for node in self.registry.list_nodes(node_type=NodeType.AGENT):
                if node.metadata.get("source") != "agent_storage":
                    continue
                if node.name in current_names:
                    continue
                if self.registry.unregister_node(node.node_id):
                    removed.append(node.name)

        return {"synced": synced, "removed": removed}

    def sync_full_topology(
        self,
        *,
        agent_storage: Any | None = None,
        clear_missing: bool = False,
    ) -> dict[str, Any]:
        """Sync apps + workflows + agents in one pass for a complete topology view."""
        result: dict[str, Any] = {
            "apps": self.sync_apps(clear_missing=clear_missing),
            "workflows": self.sync_workflows(clear_missing=clear_missing),
        }
        if agent_storage is not None:
            result["agents"] = self.sync_subagents(agent_storage, clear_missing=clear_missing)
        result["topology_stats"] = self.registry.get_topology()["stats"]
        return result

    def connect_apps(
        self,
        source_app: str,
        target_app: str,
        *,
        source_port: str = "default",
        target_port: str = "default",
        description: str = "",
        transform: str = "",
    ) -> dict[str, Any]:
        source_node = self._require_app_node(source_app)
        target_node = self._require_app_node(target_app)
        binding = self.registry.add_binding(
            source_node.node_id,
            source_port,
            target_node.node_id,
            target_port,
            description=description,
            transform=transform,
        )
        return binding.to_dict()

    def register_pipeline(
        self,
        name: str,
        app_names: list[str],
        *,
        description: str = "",
        schedule: str = "",
    ) -> dict[str, Any]:
        node_ids = [self._require_app_node(app_name).node_id for app_name in app_names]
        pipeline = self.registry.register_pipeline(
            name,
            node_ids,
            description=description,
            schedule=schedule,
        )
        return pipeline.to_dict()

    def execute_pipeline(
        self,
        name: str,
        *,
        initial_payload: dict[str, Any] | None = None,
        action: str = "run",
        use_pyflow: bool | None = None,
    ) -> dict[str, Any]:
        """Execute a registered pipeline by chaining the apps in sequence.

        The pipeline must already be registered via ``register_pipeline``. By
        default the runtime will try ``PyFlowEngine`` first (when available) so
        that retries, audit logs and approval gates apply. When PyFlow is not
        configured the runtime falls back to a built-in sequential executor
        that simply calls ``app_manager.execute_app_api`` for each step,
        feeding the previous step's output into the next.
        """
        pipeline = self._find_pipeline(name)
        if pipeline is None:
            return {"success": False, "error": f"Pipeline '{name}' not found"}

        steps = list(pipeline.steps)
        if not steps:
            return {"success": False, "error": f"Pipeline '{name}' has no steps"}

        try:
            app_names = self._resolve_pipeline_app_names(steps)
        except KeyError as exc:
            return {"success": False, "error": str(exc)}

        prefer_pyflow = bool(self.pyflow_engine) if use_pyflow is None else use_pyflow
        if prefer_pyflow and self.pyflow_engine is not None:
            try:
                return self._execute_via_pyflow(
                    pipeline_name=name,
                    app_names=app_names,
                    initial_payload=initial_payload or {},
                    action=action,
                )
            except Exception as exc:
                logger.warning("[AppMatrix] pipeline '%s' pyflow execution failed, falling back: %s", name, exc)

        return self._execute_sequential(
            pipeline_name=name,
            app_names=app_names,
            initial_payload=initial_payload or {},
            action=action,
        )

    def _find_pipeline(self, name: str) -> Any | None:
        for pipeline in self.registry.list_pipelines():
            if pipeline.name == name:
                return pipeline
        return None

    def _resolve_pipeline_app_names(self, step_node_ids: list[str]) -> list[str]:
        names: list[str] = []
        for node_id in step_node_ids:
            node = self.registry.get_node(node_id)
            if node is None or node.node_type != NodeType.APP:
                raise KeyError(f"Pipeline step '{node_id}' is not an APP node")
            names.append(node.name)
        return names

    def _execute_sequential(
        self,
        *,
        pipeline_name: str,
        app_names: list[str],
        initial_payload: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        records: list[dict[str, Any]] = []
        current_payload: dict[str, Any] = dict(initial_payload)
        started_at = time.time()
        failure: dict[str, Any] | None = None

        for index, app_name in enumerate(app_names):
            step_started = time.time()
            try:
                result = self.app_manager.execute_app_api(app_name, action, current_payload)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
            duration = time.time() - step_started
            ok = bool(result.get("success", True))
            records.append({
                "index": index,
                "app": app_name,
                "action": action,
                "success": ok,
                "duration_seconds": round(duration, 4),
                "input": current_payload,
                "output": result,
            })
            if not ok:
                failure = {"app": app_name, "error": result.get("error", "unknown error")}
                break
            next_payload = result.get("output") if isinstance(result, dict) else None
            if isinstance(next_payload, dict):
                current_payload = next_payload
            else:
                current_payload = {"previous": result, "input": current_payload}

        return {
            "success": failure is None,
            "pipeline": pipeline_name,
            "run_id": run_id,
            "engine": "sequential",
            "started_at": started_at,
            "duration_seconds": round(time.time() - started_at, 4),
            "steps": records,
            "final_output": current_payload,
            "failure": failure,
        }

    def _execute_via_pyflow(
        self,
        *,
        pipeline_name: str,
        app_names: list[str],
        initial_payload: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        from core.assets.workflows.workflow_models import (
            FlowEdge,
            FlowNode,
            NodeType as FlowNodeType,
            WorkflowDef,
        )

        run_id = uuid.uuid4().hex[:12]
        nodes: dict[str, FlowNode] = {
            "_start": FlowNode(id="_start", type=FlowNodeType.START, label="start"),
            "_end": FlowNode(id="_end", type=FlowNodeType.END, label="end"),
        }
        edges: list[FlowEdge] = []
        previous_node_id = "_start"

        for index, app_name in enumerate(app_names):
            node_id = f"step_{index}_{app_name}"
            nodes[node_id] = FlowNode(
                id=node_id,
                type=FlowNodeType.TOOL,
                label=f"{app_name}.{action}",
                config={
                    "tool": "__app_matrix_pipeline__",
                    "args": {
                        "app": app_name,
                        "action": action,
                        "payload_var": (
                            "input" if previous_node_id == "_start"
                            else f"{previous_node_id}.output"
                        ),
                    },
                },
            )
            edges.append(FlowEdge(
                id=f"e_{previous_node_id}_{node_id}",
                source=previous_node_id,
                target=node_id,
            ))
            previous_node_id = node_id

        edges.append(FlowEdge(
            id=f"e_{previous_node_id}_end",
            source=previous_node_id,
            target="_end",
        ))

        workflow = WorkflowDef(
            id=f"app_matrix_pipeline_{pipeline_name}_{run_id}",
            name=f"AppMatrix Pipeline: {pipeline_name}",
            description=f"Auto-generated workflow for app pipeline '{pipeline_name}'",
            nodes=nodes,
            edges=edges,
            variables={"input": initial_payload},
        )

        original_callback = getattr(self.pyflow_engine.node_runtime, "tool_callback", None)
        try:
            self.pyflow_engine.configure_callbacks(
                tool_callback=self._make_pipeline_tool_callback(action=action, initial_payload=initial_payload),
            )
            result = self.pyflow_engine.run_workflow(workflow)
        finally:
            self.pyflow_engine.configure_callbacks(tool_callback=original_callback)

        return {
            "success": result.get("status") == "completed",
            "pipeline": pipeline_name,
            "run_id": run_id,
            "engine": "pyflow",
            "workflow_id": workflow.id,
            "result": result,
        }

    def _make_pipeline_tool_callback(self, *, action: str, initial_payload: dict[str, Any]):
        def _cb(tool: str | None, args: dict[str, Any] | None):
            args = args or {}
            if tool != "__app_matrix_pipeline__":
                raise RuntimeError(f"Unsupported tool in app matrix pipeline: {tool}")
            app_name = args.get("app", "")
            payload = initial_payload if args.get("payload_var") == "input" else args.get("payload", initial_payload)
            return self.app_manager.execute_app_api(app_name, args.get("action", action), dict(payload or {}))

        return _cb

    def get_app_summary(self, app_name: str) -> dict[str, Any] | None:
        node = self.registry.find_node(app_name, node_type=NodeType.APP)
        if node is None:
            return None
        summary = self.registry.get_node_summary(node.node_id)
        if summary is None:
            return None
        summary["contracts"] = self._extract_contract_metadata(node)
        return summary

    def get_overview(self) -> dict[str, Any]:
        apps = [app.to_dict() for app in self.app_manager.apps.values()]
        topology = self.registry.get_topology()
        service_marketplace = self.discover_services()
        return {
            "apps": apps,
            "topology": topology,
            "issues": self.registry.validate_graph(),
            "services": service_marketplace,
        }

    def discover_services(self, *, query: str = "", provides: str = "") -> dict[str, Any]:
        if self.capability_registry is None:
            return {"query": query, "provides": provides, "providers": [], "count": 0}
        lookup = provides or query
        if lookup:
            providers = self.capability_registry.find_providers(lookup, layer="app")
            providers["query"] = query
            return providers
        local = self.capability_registry.discover(layer="app", query=query, include_marketplace=False)
        return {
            "query": query,
            "provides": provides,
            "providers": local["local"],
            "count": len(local["local"]),
        }

    def request_service_grant(
        self,
        *,
        caller_app: str,
        target_app: str = "",
        capability_name: str = "",
        provides: str = "",
        requested_quota: int = 1,
        ttl_seconds: int = 3600,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.capability_registry is None:
            return {"success": False, "error": "Capability registry unavailable"}
        self.sync_apps()
        return self.capability_registry.issue_app_grant(
            caller_app=caller_app,
            target_app=target_app,
            capability_name=capability_name,
            provides=provides,
            requested_quota=requested_quota,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def list_service_grants(
        self,
        *,
        caller_app: str = "",
        provider_app: str = "",
    ) -> list[dict[str, Any]]:
        if self.capability_registry is None:
            return []
        return self.capability_registry.list_grants(
            caller_app=caller_app,
            provider_app=provider_app,
        )

    def invoke_service(
        self,
        *,
        caller_app: str,
        grant_token: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.capability_registry is None:
            return {"success": False, "error": "Capability registry unavailable"}
        return self.capability_registry.invoke_app_capability(
            caller_app=caller_app,
            grant_token=grant_token,
            action=action,
            payload=payload,
        )

    def update_app_contract_metadata(
        self,
        app_name: str,
        *,
        shared_datastores: list[str] | None = None,
        shared_schemas: list[dict[str, Any]] | None = None,
        data_contracts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update durable contract metadata for one app and resync its node."""
        result = self.app_manager.update_app_topology_metadata(
            app_name,
            shared_datastores=shared_datastores,
            shared_schemas=shared_schemas,
            data_contracts=data_contracts,
        )
        if not result.get("success"):
            raise KeyError(result.get("error", f"App '{app_name}' not found"))
        self.sync_apps()
        return {
            "app": result["app"],
            "summary": self.get_app_summary(app_name),
        }

    @staticmethod
    def _resolve_domain(app: AppDefinition) -> str:
        if app.tags:
            return str(app.tags[0])
        if app.mode:
            return str(app.mode)
        return "app"

    @staticmethod
    def _build_app_metadata(app: AppDefinition) -> dict[str, Any]:
        return {
            "source": "app_manager",
            "app_name": app.name,
            "display_name": app.display_name,
            "mode": app.mode,
            "enabled": app.enabled,
            "entry_point": app.entry_point,
            "api_enabled": app.api_enabled,
            "tags": list(app.tags),
            "agent_binding": app.agent_binding,
            "workflow_binding": app.workflow_binding,
            "knowledge_collections": list(app.knowledge_collections),
            "allowed_tools": list(app.allowed_tools),
            "shared_datastores": list(app.shared_datastores),
            "shared_schemas": [dict(item) for item in app.shared_schemas],
            "data_contracts": [dict(item) for item in app.data_contracts],
        }

    @staticmethod
    def _extract_contract_metadata(node: OrchestrationNode) -> dict[str, Any]:
        metadata = node.metadata
        return {
            "shared_datastores": list(metadata.get("shared_datastores", [])),
            "shared_schemas": [dict(item) for item in metadata.get("shared_schemas", [])],
            "data_contracts": [dict(item) for item in metadata.get("data_contracts", [])],
        }

    def _require_app_node(self, app_name: str) -> OrchestrationNode:
        node = self.registry.find_node(app_name, node_type=NodeType.APP)
        if node is None:
            raise KeyError(f"App '{app_name}' is not registered in the APP Brain topology")
        return node
