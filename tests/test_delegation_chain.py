"""Tests for delegation chain visualization and Docker sandbox adapter."""

from __future__ import annotations

from core.agent_capability_profile import AgentCapabilityProfile
from core.subagent_governance import build_delegation_chain, format_delegation_tree
from core.subagent_sandbox import list_sandbox_adapters


class TestDelegationChain:
    def test_empty_agents(self):
        chain = build_delegation_chain([])
        assert len(chain) == 1
        assert chain[0]["name"] == "root"

    def test_single_level(self):
        agents = [
            {
                "name": "worker",
                "role": "builder",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("builder"),
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain) == 2
        assert chain[1]["name"] == "worker"
        assert chain[1]["level"] == 1

    def test_multi_level(self):
        agents = [
            {
                "name": "manager",
                "role": "coordinator",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("manager"),
            },
            {
                "name": "worker",
                "role": "builder",
                "parent": "manager",
                "capability_profile": AgentCapabilityProfile.from_value("builder"),
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain) == 3
        assert chain[2]["level"] == 2
        assert chain[2]["name"] == "worker"

    def test_restrictions_accumulate(self):
        restricted_profile = AgentCapabilityProfile.from_value("builder")
        restricted_profile = AgentCapabilityProfile(
            **{
                **restricted_profile.to_dict(),
                "allow_code_execution": False,
                "allow_workflow_management": False,
            }
        )
        agents = [
            {
                "name": "restricted",
                "role": "limited",
                "parent": None,
                "capability_profile": restricted_profile,
            },
        ]
        chain = build_delegation_chain(agents)
        assert len(chain[1]["new_restrictions"]) > 0

    def test_format_delegation_tree_ascii(self):
        agents = [
            {
                "name": "mgr",
                "role": "coordinator",
                "parent": None,
                "capability_profile": AgentCapabilityProfile.from_value("manager"),
            },
        ]
        chain = build_delegation_chain(agents)
        tree = format_delegation_tree(chain)
        assert "root" in tree
        assert "mgr" in tree
        assert "|--" in tree


class TestDockerSandboxAdapter:
    def test_docker_in_adapter_list(self):
        adapters = list_sandbox_adapters()
        names = [a["name"] for a in adapters]
        assert "docker" in names
        docker_adapter = next(a for a in adapters if a["name"] == "docker")
        assert docker_adapter["backend"] == "docker"
