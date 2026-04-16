"""Tests for multi-tenant workspace isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.systems.runtime.multi_tenant import (
    TenantManager,
    TenantProfile,
    TenantWorkspace,
    create_tenant_manager,
)


class TestTenantWorkspace:
    def test_create_workspace(self, tmp_path):
        ws = TenantWorkspace.create("user1", tmp_path)
        assert ws.tenant_id == "user1"
        assert ws.root.exists()
        assert ws.tools_dir.exists()
        assert ws.memory_dir.exists()
        assert str(ws.root).endswith("tenants/user1")

    def test_separate_workspaces(self, tmp_path):
        ws1 = TenantWorkspace.create("user1", tmp_path)
        ws2 = TenantWorkspace.create("user2", tmp_path)
        assert ws1.root != ws2.root
        assert ws1.tools_dir != ws2.tools_dir


class TestTenantManager:
    def test_resolve_default(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        assert mgr.resolve_tenant() == "default"

    def test_resolve_from_header(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        assert mgr.resolve_tenant(header_tenant="alice") == "alice"

    def test_resolve_from_api_key(self, tmp_path):
        config = {"api_key_tenants": {"sk-alice-123": "alice", "sk-bob-456": "bob"}}
        mgr = TenantManager(base_dir=tmp_path, config=config)
        assert mgr.resolve_tenant(api_key="sk-alice-123") == "alice"
        assert mgr.resolve_tenant(api_key="sk-bob-456") == "bob"
        assert mgr.resolve_tenant(api_key="unknown") == "default"

    def test_header_takes_priority(self, tmp_path):
        config = {"api_key_tenants": {"sk-123": "alice"}}
        mgr = TenantManager(base_dir=tmp_path, config=config)
        assert mgr.resolve_tenant(header_tenant="bob", api_key="sk-123") == "bob"

    def test_get_workspace_creates_dirs(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        ws = mgr.get_workspace("user1")
        assert ws.root.exists()
        assert ws.tools_dir.exists()

    def test_workspace_cached(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        ws1 = mgr.get_workspace("user1")
        ws2 = mgr.get_workspace("user1")
        assert ws1 is ws2

    def test_get_profile_default(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        profile = mgr.get_profile("user1")
        assert profile.tenant_id == "user1"
        assert profile.enabled is True

    def test_config_profiles(self, tmp_path):
        config = {
            "tenants": {
                "alice": {
                    "display_name": "Alice",
                    "max_tools": 50,
                    "canvas_default": "focused",
                },
            },
        }
        mgr = TenantManager(base_dir=tmp_path, config=config)
        profile = mgr.get_profile("alice")
        assert profile.display_name == "Alice"
        assert profile.max_tools == 50
        assert profile.canvas_default == "focused"

    def test_list_tenants(self, tmp_path):
        config = {"tenants": {"alice": {"display_name": "Alice"}}}
        mgr = TenantManager(base_dir=tmp_path, config=config)
        mgr.get_workspace("bob")
        tenants = mgr.list_tenants()
        names = {t["tenant_id"] for t in tenants}
        assert "alice" in names
        assert "bob" in names

    def test_delete_tenant(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        mgr.get_workspace("temp")
        assert mgr.delete_tenant("temp") is True
        assert "temp" not in mgr._profiles

    def test_cannot_delete_default(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        assert mgr.delete_tenant("default") is False

    def test_get_stats(self, tmp_path):
        mgr = TenantManager(base_dir=tmp_path)
        ws = mgr.get_workspace("user1")
        (ws.tools_dir / "test.json").write_text("{}")
        stats = mgr.get_stats("user1")
        assert stats["tenant_id"] == "user1"
        assert stats["tools"] >= 1


class TestFactory:
    def test_create_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYBOT_RUNTIME_HOME", str(tmp_path))
        mgr = create_tenant_manager()
        assert mgr.resolve_tenant() == "default"
