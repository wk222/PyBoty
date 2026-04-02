"""Helpers for materializing draft capabilities from Admin telemetry gaps."""

from __future__ import annotations

from typing import Any


def materialize_capability_gap_draft(
    host_agent: Any,
    candidate: Any,
    *,
    target_name: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    asset_kind = str(getattr(candidate, "recommended_asset_kind", "") or "skill").strip().lower()
    resolved_name = (target_name or getattr(candidate, "suggested_capability_name", "")).strip()
    if not resolved_name:
        return {"success": False, "error": "Missing target capability name"}

    if asset_kind == "skill":
        return _materialize_skill_draft(host_agent, candidate, resolved_name, overwrite=overwrite)
    if asset_kind == "tool":
        return _materialize_tool_draft(host_agent, candidate, resolved_name, overwrite=overwrite)
    if asset_kind == "app":
        return _materialize_app_draft(host_agent, candidate, resolved_name, overwrite=overwrite)
    if asset_kind == "workflow":
        return _materialize_workflow_draft(host_agent, candidate, resolved_name, overwrite=overwrite)
    return {"success": False, "error": f"Unsupported asset kind: {asset_kind}"}


def _materialize_skill_draft(
    host_agent: Any,
    candidate: Any,
    target_name: str,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    from core.assets.skills.skill_loading import build_skill_bundle
    from core.assets.skills.skill_models import SkillDefinition

    skill_registry = getattr(host_agent, "skill_registry", None)
    if skill_registry is None:
        return {"success": False, "error": "Skill registry unavailable"}

    description = f"Draft skill synthesized for the recurring {candidate.gap_type} observed in {candidate.source}."
    prompt_extension = (
        f"Use this draft skill to address {candidate.suggested_capability_name}.\n\n"
        f"Gap source: {candidate.source}\n"
        f"Gap type: {candidate.gap_type}\n"
        "This is an auto-generated draft and should be validated against the attached samples before rollout."
    )
    skill_def = SkillDefinition(
        name=target_name,
        description=description,
        author="PyBot Admin",
        capabilities=[candidate.suggested_capability_name, candidate.gap_type],
        system_prompt_extension=prompt_extension,
        metadata={
            "draft": True,
            "autopoietic_source": "capability_gap_candidate",
            "candidate_id": candidate.candidate_id,
            "source": candidate.source,
            "recommended_publish_target": candidate.recommended_publish_target,
        },
    )
    bundle = build_skill_bundle(skill_def)
    try:
        install_result = skill_registry.import_skill_bundle(
            target_name,
            bundle,
            overwrite=overwrite,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    _refresh_registry(host_agent)
    return {
        "success": True,
        "asset_kind": "skill",
        "name": target_name,
        "files": sorted(bundle),
        "install_result": install_result,
        "publish_target": candidate.recommended_publish_target,
    }


def _materialize_tool_draft(
    host_agent: Any,
    candidate: Any,
    target_name: str,
    *,
    overwrite: bool,  # noqa: ARG001
) -> dict[str, Any]:
    from core.assets.tools.tool_creation_support import (
        build_tool_definition,
        persist_validated_tool_definition,
        validate_tool_name,
    )
    from core.assets.tools.tool_runtime import build_dynamic_tool

    storage = getattr(host_agent, "storage", None)
    if storage is None:
        return {"success": False, "error": "Global tool storage unavailable"}
    if not overwrite and storage.get_tool(target_name) is not None:
        return {"success": False, "error": f"Tool '{target_name}' already exists"}

    try:
        validate_tool_name(target_name)
        tool_definition = build_tool_definition(
            tool_name=target_name,
            description=(
                f"Draft tool synthesized for {candidate.suggested_capability_name} from "
                f"{candidate.source} {candidate.gap_type} signals."
            ),
            parameters=[
                {
                    "name": "payload",
                    "type": "dict",
                    "description": "Input payload to handle while the draft capability is being refined.",
                }
            ],
            code=(
                "result = {\n"
                "    'status': 'draft',\n"
                f"    'candidate_id': {candidate.candidate_id!r},\n"
                f"    'capability': {candidate.suggested_capability_name!r},\n"
                "    'message': 'Auto-generated draft tool placeholder. Replace with real logic after validation.',\n"
                "    'payload': payload,\n"
                "}\n"
            ),
            dependencies=[],
            usage_guide=(
                "Draft placeholder tool synthesized from telemetry. "
                "Use it as a starting point for implementation and validation."
            ),
        )
        persist_validated_tool_definition(
            storage,
            tool_definition,
            validator=build_dynamic_tool,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    _refresh_registry(host_agent)
    return {
        "success": True,
        "asset_kind": "tool",
        "name": target_name,
        "storage": "global",
        "publish_target": candidate.recommended_publish_target,
    }


def _materialize_app_draft(
    host_agent: Any,
    candidate: Any,
    target_name: str,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    app_manager = getattr(host_agent, "app_manager", None)
    app_matrix = getattr(host_agent, "app_matrix", None)
    if app_manager is None:
        return {"success": False, "error": "App manager unavailable"}
    existing_app = app_manager.get_app(target_name)
    if existing_app is not None:
        if not overwrite:
            return {"success": False, "error": f"App '{target_name}' already exists"}
        deleted = app_manager.delete_app(target_name)
        if not deleted.get("success"):
            return {"success": False, "error": deleted.get("error", f"Failed to replace app '{target_name}'")}

    created = app_manager.create_app(
        target_name,
        display_name=target_name.replace("_", " ").title(),
        description=(
            f"Draft APP synthesized for {candidate.suggested_capability_name} based on "
            f"recurring {candidate.gap_type} signals."
        ),
        mode="assistant",
        exports=[candidate.suggested_capability_name],
        tags=["draft", "autopoietic"],
    )
    if not created.get("success"):
        return created

    app_manager.update_app_file(
        target_name,
        "api.py",
        (
            "if action == 'health':\n"
            f"    result = {{'status': 'draft', 'candidate_id': {candidate.candidate_id!r}}}\n"
            "elif action == 'handle':\n"
            "    result = {\n"
            "        'status': 'draft',\n"
            f"        'capability': {candidate.suggested_capability_name!r},\n"
            "        'payload': payload,\n"
            "        'message': 'Auto-generated APP draft placeholder. Implement real behavior after validation.',\n"
            "    }\n"
            "else:\n"
            "    result = {'status': 'unknown_action', 'action': action}\n"
        ),
    )
    app_manager.update_app_topology_metadata(
        target_name,
        data_contracts=[
            {
                "name": candidate.suggested_capability_name,
                "kind": "draft_capability_contract",
                "source": candidate.source,
            }
        ],
    )
    if app_matrix is not None:
        app_matrix.sync_apps()
    _refresh_registry(host_agent)
    return {
        "success": True,
        "asset_kind": "app",
        "name": target_name,
        "path": created.get("path", ""),
        "publish_target": candidate.recommended_publish_target,
    }


def _materialize_workflow_draft(
    host_agent: Any,
    candidate: Any,
    target_name: str,
    *,
    overwrite: bool,  # noqa: ARG001
) -> dict[str, Any]:
    engine = getattr(host_agent, "pyflow_engine", None)
    if engine is None:
        return {"success": False, "error": "Workflow engine unavailable"}
    try:
        engine.get_workflow_definition(target_name)
    except Exception:
        existing_workflow = None
    else:
        existing_workflow = target_name
    if existing_workflow is not None:
        if not overwrite:
            return {"success": False, "error": f"Workflow '{target_name}' already exists"}
        engine.delete_workflow_definition(target_name)

    workflow_definition = {
        "name": target_name,
        "description": (
            f"Draft workflow synthesized for {candidate.suggested_capability_name} from {candidate.source} telemetry."
        ),
        "tags": ["draft", "autopoietic"],
        "nodes": [
            {
                "id": "draft_handler",
                "type": "code",
                "label": "Draft Handler",
                "code": (
                    "result = {\n"
                    "    'status': 'draft',\n"
                    f"    'candidate_id': {candidate.candidate_id!r},\n"
                    f"    'capability': {candidate.suggested_capability_name!r},\n"
                    "}\n"
                ),
            }
        ],
    }
    try:
        workflow_id = engine.create_workflow_definition(target_name, workflow_definition)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    _refresh_registry(host_agent)
    return {
        "success": True,
        "asset_kind": "workflow",
        "name": target_name,
        "workflow_id": workflow_id,
        "publish_target": candidate.recommended_publish_target,
    }


def _refresh_registry(host_agent: Any) -> None:
    registry = getattr(host_agent, "capability_registry", None)
    if registry is None:
        return
    try:
        registry.refresh_local_index(save=True)
    except Exception:
        return


def validate_capability_gap_draft(
    host_agent: Any,
    candidate: Any,
) -> dict[str, Any]:
    artifact = dict(getattr(candidate, "draft_artifact", {}) or {})
    if not artifact:
        return {"success": False, "error": "No draft artifact found for capability gap candidate"}

    asset_kind = str(artifact.get("asset_kind", "")).strip().lower()
    if asset_kind == "skill":
        return _validate_skill_draft(host_agent, candidate, artifact)
    if asset_kind == "tool":
        return _validate_tool_draft(host_agent, candidate, artifact)
    if asset_kind == "app":
        return _validate_app_draft(host_agent, candidate, artifact)
    if asset_kind == "workflow":
        return _validate_workflow_draft(host_agent, candidate, artifact)
    return {"success": False, "error": f"Unsupported draft asset kind: {asset_kind}"}


def publish_capability_gap_draft(
    host_agent: Any,
    candidate: Any,
    *,
    publish_to_hub: bool = False,
    hub_url: str = "",
    hub_token: str = "",
    version: str = "0.1.0",
    changelog: str = "",
) -> dict[str, Any]:
    artifact = dict(getattr(candidate, "draft_artifact", {}) or {})
    if not artifact:
        return {"success": False, "error": "No draft artifact found for capability gap candidate"}

    asset_kind = str(artifact.get("asset_kind", "")).strip().lower()
    if asset_kind == "skill":
        return _publish_skill_draft(
            host_agent,
            candidate,
            artifact,
            publish_to_hub=publish_to_hub,
            hub_url=hub_url,
            hub_token=hub_token,
            version=version,
            changelog=changelog,
        )
    if asset_kind == "tool":
        return _publish_tool_draft(host_agent, candidate, artifact)
    if asset_kind == "app":
        return _publish_app_draft(
            host_agent,
            candidate,
            artifact,
            publish_to_hub=publish_to_hub,
            hub_url=hub_url,
            hub_token=hub_token,
            version=version,
            changelog=changelog,
        )
    if asset_kind == "workflow":
        return _publish_workflow_draft(host_agent, candidate, artifact)
    return {"success": False, "error": f"Unsupported draft asset kind: {asset_kind}"}


def _validate_skill_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    skill_registry = getattr(host_agent, "skill_registry", None)
    skill_marketplace = getattr(host_agent, "skill_marketplace", None)
    skill_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if skill_registry is None or skill_marketplace is None:
        return {"success": False, "error": "Skill validation surfaces unavailable"}
    if skill_registry.get_skill(skill_name) is None:
        return {"success": False, "error": f"Skill '{skill_name}' not found"}

    skill_dir = skill_registry.skill_dir(skill_name)
    if skill_dir is None:
        return {"success": False, "error": f"Unable to resolve skill directory for '{skill_name}'"}

    validation = skill_marketplace.validate_skill(str(skill_dir))
    payload = {
        "success": True,
        "valid": bool(validation.get("valid", False)),
        "asset_kind": "skill",
        "name": skill_name,
        "validation": validation,
        "contract": _safe_capability_contract(host_agent, skill_name),
    }
    _emit_validation_event(candidate, payload)
    return payload


def _validate_tool_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    from core.assets.tools.tool_runtime import build_dynamic_tool

    storage = getattr(host_agent, "storage", None)
    tool_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if storage is None:
        return {"success": False, "error": "Tool storage unavailable"}

    definition = storage.get_tool(tool_name)
    if definition is None:
        return {"success": False, "error": f"Tool '{tool_name}' not found"}

    try:
        build_dynamic_tool(definition, project_paths=getattr(host_agent, "paths", None))
        valid = True
        error = ""
    except Exception as exc:
        valid = False
        error = str(exc)

    payload = {
        "success": True,
        "valid": valid,
        "asset_kind": "tool",
        "name": tool_name,
        "error": error,
        "contract": _safe_capability_contract(host_agent, tool_name),
    }
    _emit_validation_event(candidate, payload)
    return payload


def _validate_app_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    from core.assets.apps.app_verifier import AppVerificationService

    app_manager = getattr(host_agent, "app_manager", None)
    app_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if app_manager is None:
        return {"success": False, "error": "App manager unavailable"}

    verification = AppVerificationService(app_manager).verify_app(app_name, auto_fix=False)
    health = app_manager.execute_app_api(app_name, "health", {})
    valid = bool(
        verification.get("success")
        and verification.get("verdict") in {"PASS", "NEEDS_IMPROVEMENT"}
        and health.get("success")
    )
    payload = {
        "success": True,
        "valid": valid,
        "asset_kind": "app",
        "name": app_name,
        "verification": verification,
        "healthcheck": health,
        "contract": _safe_capability_contract(host_agent, app_name),
    }
    _emit_validation_event(candidate, payload)
    return payload


def _validate_workflow_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    import json

    engine = getattr(host_agent, "pyflow_engine", None)
    workflow_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if engine is None:
        return {"success": False, "error": "Workflow engine unavailable"}

    try:
        definition = engine.get_workflow_definition(workflow_name)
        parsed = engine.parse_workflow(json.dumps(definition, ensure_ascii=False))
        valid = parsed is not None
        error = ""
    except Exception as exc:
        definition = None
        valid = False
        error = str(exc)

    payload = {
        "success": True,
        "valid": valid,
        "asset_kind": "workflow",
        "name": workflow_name,
        "definition": definition,
        "error": error,
        "contract": _safe_capability_contract(host_agent, workflow_name),
    }
    _emit_validation_event(candidate, payload)
    return payload


def _publish_skill_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
    *,
    publish_to_hub: bool,
    hub_url: str,
    hub_token: str,
    version: str,
    changelog: str,
) -> dict[str, Any]:
    registry = getattr(host_agent, "capability_registry", None)
    skill_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if registry is None:
        return {"success": False, "error": "Capability registry unavailable"}
    return registry.publish_skill(
        skill_name,
        version=version,
        changelog=changelog,
        publish_to_hub=publish_to_hub,
        hub_url=hub_url,
        hub_token=hub_token,
    )


def _publish_tool_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    from core.systems.runtime.event_bus import Event, EventType, event_bus

    storage = getattr(host_agent, "storage", None)
    tool_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if storage is None:
        return {"success": False, "error": "Tool storage unavailable"}
    if storage.get_tool(tool_name) is None:
        return {"success": False, "error": f"Tool '{tool_name}' not found"}

    _refresh_registry(host_agent)
    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_PUBLISHED,
            source="capability_synthesis",
            payload={"kind": "tool", "name": tool_name, "remote": False, "version": "draft"},
        )
    )
    return {
        "success": True,
        "asset_kind": "tool",
        "name": tool_name,
        "published_locally": True,
        "contract": _safe_capability_contract(host_agent, tool_name),
    }


def _publish_app_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
    *,
    publish_to_hub: bool,
    hub_url: str,
    hub_token: str,
    version: str,
    changelog: str,
) -> dict[str, Any]:
    from core.assets.apps.app_packager import AppPackager
    from core.systems.runtime.event_bus import Event, EventType, event_bus

    app_manager = getattr(host_agent, "app_manager", None)
    app_matrix = getattr(host_agent, "app_matrix", None)
    registry = getattr(host_agent, "capability_registry", None)
    app_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if app_manager is None:
        return {"success": False, "error": "App manager unavailable"}
    if app_manager.get_app(app_name) is None:
        return {"success": False, "error": f"App '{app_name}' not found"}

    remote_result: dict[str, Any] | None = None
    if app_matrix is not None:
        app_matrix.sync_apps()
    _refresh_registry(host_agent)

    if publish_to_hub:
        if not hub_url.strip():
            return {"success": False, "error": "hub_url is required when publish_to_hub=true"}
        if registry is None:
            return {"success": False, "error": "Capability registry unavailable for remote publish"}
        hub_client = registry._hub_client_factory(hub_url, hub_token or None)  # noqa: SLF001
        packager = AppPackager(app_manager.apps_dir)
        remote_result = packager.publish_to_hub(
            app_name,
            hub_client,
            version=version,
            changelog=changelog,
        )
        if not remote_result.get("success"):
            return remote_result

    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_PUBLISHED,
            source="capability_synthesis",
            payload={"kind": "app", "name": app_name, "remote": bool(remote_result), "version": version},
        )
    )
    return {
        "success": True,
        "asset_kind": "app",
        "name": app_name,
        "published_locally": True,
        "remote": remote_result,
        "contract": _safe_capability_contract(host_agent, app_name),
    }


def _publish_workflow_draft(
    host_agent: Any,
    candidate: Any,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    from core.systems.runtime.event_bus import Event, EventType, event_bus

    engine = getattr(host_agent, "pyflow_engine", None)
    workflow_name = str(artifact.get("name") or getattr(candidate, "suggested_capability_name", "")).strip()
    if engine is None:
        return {"success": False, "error": "Workflow engine unavailable"}

    publish_result = engine.publish_workflow(workflow_name)
    _refresh_registry(host_agent)
    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_PUBLISHED,
            source="capability_synthesis",
            payload={"kind": "workflow", "name": workflow_name, "remote": False, "version": "draft"},
        )
    )
    return {
        "success": True,
        "asset_kind": "workflow",
        "name": workflow_name,
        "publish_result": publish_result,
        "contract": _safe_capability_contract(host_agent, workflow_name),
    }


def _safe_capability_contract(host_agent: Any, capability_name: str) -> dict[str, Any]:
    registry = getattr(host_agent, "capability_registry", None)
    if registry is None:
        return {}
    try:
        return dict(registry.get_capability_contract(capability_name) or {})
    except Exception:
        return {}


def _emit_validation_event(candidate: Any, payload: dict[str, Any]) -> None:
    from core.systems.runtime.event_bus import Event, EventType, event_bus

    event_bus.emit(
        Event(
            type=EventType.CAPABILITY_DRAFT_VALIDATED,
            source="capability_synthesis",
            payload={
                "candidate_id": getattr(candidate, "candidate_id", ""),
                "suggested_capability_name": getattr(candidate, "suggested_capability_name", ""),
                **payload,
            },
        )
    )
