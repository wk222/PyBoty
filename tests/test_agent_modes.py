from __future__ import annotations

import pytest

from agent import AdminPyBot, AppMatrixPyBot, PyBot, create_admin_agent, create_app_matrix_agent
from core.modes import resolve_mode_profile


def test_mode_surface_methods_are_auto_attached():
    assert callable(PyBot.submit_admin_goal)
    assert callable(PyBot.sync_app_matrix_registry)


def test_create_admin_agent_uses_admin_mode(monkeypatch, tmp_path):
    def _fake_init(self, *args, **kwargs):
        self.root_mode = kwargs.get("root_mode", "admin")
        self._attach_admin_runtime = kwargs.get("attach_admin_runtime", True)

    monkeypatch.setattr(AdminPyBot, "__init__", _fake_init)

    agent = create_admin_agent(paths=None, workspace_dir=str(tmp_path))

    assert isinstance(agent, AdminPyBot)
    assert agent.root_mode == "admin"
    assert agent._attach_admin_runtime is True


def test_create_app_matrix_agent_uses_app_matrix_mode(monkeypatch, tmp_path):
    def _fake_init(self, *args, **kwargs):
        self.root_mode = kwargs.get("root_mode", "app_matrix")
        self._attach_admin_runtime = kwargs.get("attach_admin_runtime", True)

    monkeypatch.setattr(AppMatrixPyBot, "__init__", _fake_init)

    agent = create_app_matrix_agent(paths=None, workspace_dir=str(tmp_path))

    assert isinstance(agent, AppMatrixPyBot)
    assert agent.root_mode == "app_matrix"
    assert agent._attach_admin_runtime is True


def test_plan_app_matrix_topology_falls_back_without_llm():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("app_matrix")
    agent._attach_admin_runtime = True
    agent.admin = None
    agent.app_matrix = None
    agent.llm = None

    plan = agent.plan_app_matrix_topology(
        goal_name="shared_goal",
        goal_description="Coordinate apps around a business process",
    )

    assert plan["summary"] == "Coordinate apps around a business process"
    assert "planner_llm_unavailable" in plan["planning_notes"]


def test_update_app_matrix_node_metadata_delegates_to_runtime():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("app_matrix")
    agent._attach_admin_runtime = True
    agent.admin = None

    class StubAppMatrix:
        def update_app_contract_metadata(self, app_name, **kwargs):
            return {"app_name": app_name, "metadata": kwargs}

    agent.app_matrix = StubAppMatrix()

    result = agent.update_app_matrix_node_metadata(
        "crm",
        shared_datastores=["customer_db"],
        data_contracts=[{"name": "customer_sync"}],
    )

    assert result["app_name"] == "crm"
    assert result["metadata"]["shared_datastores"] == ["customer_db"]


def test_get_mode_profile_returns_modular_capability_flags():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("app_matrix")
    agent._attach_admin_runtime = True
    agent.admin = None
    agent.app_matrix = object()

    profile = agent.get_mode_profile()

    assert profile["name"] == "app_matrix"
    assert profile["capabilities"]["app_orchestration"] is True
    assert profile["effective_capabilities"]["app_orchestration"] is True


def test_assistant_mode_blocks_app_matrix_surfaces():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("assistant")
    agent._attach_admin_runtime = False
    agent.admin = None
    agent.app_matrix = None

    with pytest.raises(RuntimeError, match="APP 编排运行时"):
        agent.sync_app_matrix_registry()


def test_explicit_runtime_override_enables_durable_goal_capability():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("assistant")
    agent._attach_admin_runtime = True
    agent.admin = None
    agent.app_matrix = None

    assert agent.supports_mode_capability("durable_goal_loop") is True


def test_app_matrix_mode_exposes_capability_gap_management():
    agent = object.__new__(PyBot)
    agent.mode_profile = resolve_mode_profile("app_matrix")
    agent._attach_admin_runtime = True

    class StubAdminRuntime:
        def list_capability_gap_candidates(self, *, status=""):
            return [{"candidate_id": "gap-1", "status": status or "detected"}]

        def get_capability_gap_candidate(self, candidate_id):
            return {"candidate_id": candidate_id, "status": "detected"}

        def draft_capability_gap_candidate(self, candidate_id, *, target_name="", overwrite=False):
            return {
                "success": True,
                "candidate_id": candidate_id,
                "target_name": target_name,
                "overwrite": overwrite,
            }

        def validate_capability_gap_candidate(self, candidate_id):
            return {"success": True, "candidate_id": candidate_id, "valid": True}

        def publish_capability_gap_candidate(self, candidate_id, **kwargs):
            return {"success": True, "candidate_id": candidate_id, **kwargs}

        def close_capability_gap_candidate(self, candidate_id, **kwargs):
            return {"success": True, "candidate_id": candidate_id, **kwargs}

        def start_capability_gap_rollout(self, candidate_id, **kwargs):
            return {"success": True, "candidate_id": candidate_id, **kwargs}

        def evaluate_capability_gap_rollout(self, candidate_id, **kwargs):
            return {"success": True, "candidate_id": candidate_id, **kwargs}

        def promote_capability_gap_candidate(self, candidate_id, *, auto_start=True):
            return {"success": True, "candidate_id": candidate_id, "auto_start": auto_start}

    agent.admin = StubAdminRuntime()
    agent.app_matrix = object()

    listed = agent.list_capability_gap_candidates(status="detected")
    detail = agent.get_capability_gap_candidate("gap-1")
    drafted = agent.draft_capability_gap_candidate("gap-1", target_name="draft_gap", overwrite=True)
    validated = agent.validate_capability_gap_candidate("gap-1")
    published = agent.publish_capability_gap_candidate("gap-1", version="0.2.0")
    closed = agent.close_capability_gap_candidate("gap-1", target_name="draft_gap")
    rollout = agent.start_capability_gap_rollout("gap-1", strategy="shadow", target="app-matrix")
    evaluated = agent.evaluate_capability_gap_rollout("gap-1", outcome="healthy", note="looks good")
    promoted = agent.promote_capability_gap_candidate("gap-1", auto_start=False)

    assert listed[0]["candidate_id"] == "gap-1"
    assert detail["candidate_id"] == "gap-1"
    assert drafted["success"] is True
    assert drafted["target_name"] == "draft_gap"
    assert drafted["overwrite"] is True
    assert validated["valid"] is True
    assert published["version"] == "0.2.0"
    assert closed["target_name"] == "draft_gap"
    assert rollout["strategy"] == "shadow"
    assert evaluated["outcome"] == "healthy"
    assert promoted["success"] is True
    assert promoted["auto_start"] is False
