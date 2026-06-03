from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import ToolMessage
import pytest

from agent import PyBot
from core.assets.tools import ToolCallRuntime, DelegatedToolApprovalRuntime, DynamicToolInventory
from core.systems.governance.approval_queue import ApprovalQueue
from core.systems.governance import AgentControlPolicy
from core.systems.governance.tool_control_runtime import ToolControlRuntime
from core.systems.integration import (
    PluginRegistry,
    discover_plugins,
    get_plugin_registry,
    reset_plugin_registry,
    PluginManifest,
)
from core.plugin_sdk.file_lock import FileLockTimeout, acquire_file_lock
from core.plugin_sdk.webhook_guards import WebhookValidationError, verify_hmac_signature


from core.plugin_sdk import (
    PluginHost,
    on_settings_change,
    on_shutdown,
    on_startup,
    on_task_heartbeat,
    plugin_host as global_host,
    pybot_plugin,
)
from core.plugin_sdk.installer import (
    InstallSource,
    InstallSourceKind,
    install,
    list_installed,
    uninstall,
)
from core.plugin_sdk.manifest import (
    MANIFEST_FILENAME,
    CompatRule,
    ManifestError,
    ManifestKind,
    PyBotManifest,
    _check_semver_rule,
)
from core.plugin_sdk.marketplace import (
    MARKETPLACE_SCHEMA_VERSION,
    MarketplaceEntry,
    MarketplaceError,
    MarketplaceIndex,
)
from core.plugin_sdk.settings import (
    SETTINGS_FILENAME,
    SettingsError,
    SettingsSchema,
    SettingsStore,
)
from core.systems.runtime.event_bus import Event, EventType, event_bus


# ---------------------------------------------------------------------------
# Test Plugin Lifecyle (formerly test_plugin_lifecycle.py)
# ---------------------------------------------------------------------------

@pybot_plugin(id="demo", name="Demo plugin")
class _DemoPlugin:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []

    @on_startup
    def started(self) -> None:
        self.events.append(("startup", ()))

    @on_shutdown
    def stopped(self) -> None:
        self.events.append(("shutdown", ()))

    @on_settings_change
    def reconfigured(self, plugin_id: str, settings: dict[str, Any]) -> None:
        self.events.append(("settings", (plugin_id, settings)))

    @on_task_heartbeat
    def heartbeat(self, snapshot: dict[str, Any]) -> None:
        self.events.append(("heartbeat", (snapshot,)))


@pytest.fixture
def host() -> PluginHost:
    h = PluginHost()
    yield h
    h.reset()


@pytest.fixture
def plugin() -> _DemoPlugin:
    return _DemoPlugin()


class TestPluginLifecycleClass:
    def test_register_uses_id_from_decorator(self, host, plugin):
        pid = host.register(plugin)
        assert pid == "demo"
        assert host.list_plugins() == ["demo"]
        assert host.get("demo") is plugin

    def test_register_falls_back_to_class_path(self, host):
        class _Bare:
            pass

        pid = host.register(_Bare())
        assert "_Bare" in pid

    def test_explicit_plugin_id_overrides_decorator(self, host, plugin):
        pid = host.register(plugin, plugin_id="custom")
        assert pid == "custom"
        assert host.get("custom") is plugin
        assert host.get("demo") is None

    def test_startup_invokes_on_startup_hook(self, host, plugin):
        host.register(plugin)
        host.startup()
        assert ("startup", ()) in plugin.events

    def test_register_after_startup_runs_hook_immediately(self, host, plugin):
        host.startup()
        host.register(plugin)
        assert ("startup", ()) in plugin.events

    def test_shutdown_invokes_on_shutdown_hook(self, host, plugin):
        host.register(plugin)
        host.shutdown()
        assert ("shutdown", ()) in plugin.events

    def test_unregister_invokes_on_shutdown(self, host, plugin):
        host.register(plugin)
        host.unregister("demo")
        assert ("shutdown", ()) in plugin.events
        assert host.list_plugins() == []

    def test_settings_changed_dispatches_only_to_target(self, host):
        a = _DemoPlugin()
        b = _DemoPlugin()
        host.register(a, plugin_id="a")
        host.register(b, plugin_id="b")
        host.settings_changed("a", {"x": 1})
        a_events = [e for e in a.events if e[0] == "settings"]
        b_events = [e for e in b.events if e[0] == "settings"]
        assert a_events == [("settings", ("a", {"x": 1}))]
        assert b_events == []

    def test_task_heartbeat_dispatches_to_all(self, host):
        a = _DemoPlugin()
        b = _DemoPlugin()
        host.register(a, plugin_id="a")
        host.register(b, plugin_id="b")
        snap = {"task_id": "t1", "progress": 0.5}
        host.task_heartbeat(snap)
        for p in (a, b):
            events = [e for e in p.events if e[0] == "heartbeat"]
            assert events == [("heartbeat", (snap,))]

    def test_event_bus_heartbeat_flows_into_host(self, host, plugin):
        host.register(plugin)
        snap = {"task_id": "t-evt", "progress": 0.1}
        event_bus.emit(
            Event(
                type=EventType.SCHEDULE_RUN,
                payload={"task_event": "task.heartbeat", "task_id": "t-evt", "snapshot": snap},
                source="test",
            )
        )
        heartbeat_events = [e for e in plugin.events if e[0] == "heartbeat"]
        assert heartbeat_events == [("heartbeat", (snap,))]

    def test_event_bus_other_events_do_not_call_heartbeat(self, host, plugin):
        host.register(plugin)
        event_bus.emit(
            Event(
                type=EventType.SCHEDULE_RUN,
                payload={"task_event": "task.spawned", "task_id": "x"},
                source="test",
            )
        )
        assert not any(e[0] == "heartbeat" for e in plugin.events)

    def test_failing_hook_does_not_propagate(self, host):
        class _Boom:
            @on_startup
            def boom(self) -> None:
                raise RuntimeError("nope")

        host.register(_Boom(), plugin_id="boom")
        host.startup()  # must not raise

    def test_global_singleton_is_a_pluginhost(self):
        assert isinstance(global_host, PluginHost)


# ---------------------------------------------------------------------------
# Test Plugin Manifest (formerly test_plugin_manifest.py)
# ---------------------------------------------------------------------------

_MINIMAL = {
    "id": "demo_skill",
    "kind": "skill",
    "version": "0.1.0",
    "entrypoint": "demo_skill:run",
}


def _make_extension(root: Path, *, manifest_overrides: dict | None = None) -> Path:
    ext = root / "src"
    ext.mkdir(parents=True, exist_ok=True)
    manifest = {**_MINIMAL, **(manifest_overrides or {})}
    (ext / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    (ext / "demo_skill.py").write_text("def run(): return 'hi'", encoding="utf-8")
    return ext


class TestPluginManifestClass:
    def test_minimal_manifest_round_trips(self):
        m = PyBotManifest.from_dict(_MINIMAL)
        assert m.kind is ManifestKind.SKILL
        assert m.id == "demo_skill"
        assert m.version == "0.1.0"
        data = m.to_dict()
        assert data["kind"] == "skill"
        assert data["permissions"] == []

    def test_full_manifest_with_extras(self):
        m = PyBotManifest.from_dict(
            {
                **_MINIMAL,
                "name": "Demo Skill",
                "description": "Hello",
                "permissions": ["network", "fs:read"],
                "settings_schema": "settings.schema.json",
                "cron": "*/30 * * * *",
                "compat": {"pybot": ">=0.1.0"},
                "custom_field": {"foo": 1},
            }
        )
        assert m.permissions == ("network", "fs:read")
        assert m.settings_schema == "settings.schema.json"
        assert m.cron == "*/30 * * * *"
        assert m.extra == {"custom_field": {"foo": 1}}

    @pytest.mark.parametrize(
        "field, value, msg",
        [
            ("id", "Bad-ID-With-CAPS", "must match"),
            ("id", "x", "must match"),  # too short
            ("kind", "wizardry", "unknown kind"),
            ("version", "0.1", "SemVer"),
            ("entrypoint", "", "required"),
            ("entrypoint", "pkg/mod:fn", "Python dotted form"),
        ],
    )
    def test_invalid_fields_raise(self, field, value, msg):
        bad = {**_MINIMAL, field: value}
        with pytest.raises(ManifestError, match=msg):
            PyBotManifest.from_dict(bad)

    def test_unknown_permission_rejected(self):
        with pytest.raises(ManifestError, match="unknown permission"):
            PyBotManifest.from_dict({**_MINIMAL, "permissions": ["banana"]})

    def test_invalid_cron_rejected(self):
        with pytest.raises(ManifestError, match="cron"):
            PyBotManifest.from_dict({**_MINIMAL, "cron": "not a cron"})

    def test_compat_pybot_must_be_string(self):
        with pytest.raises(ManifestError, match="compat.pybot"):
            PyBotManifest.from_dict({**_MINIMAL, "compat": {"pybot": 5}})

    def test_assert_compatible(self):
        m = PyBotManifest.from_dict({**_MINIMAL, "compat": {"pybot": ">=0.0.1"}})
        m.assert_compatible(pybot_version="9.9.9")  # ok
        m_bad = PyBotManifest.from_dict({**_MINIMAL, "compat": {"pybot": ">=99.0.0"}})
        with pytest.raises(ManifestError, match="requires PyBot"):
            m_bad.assert_compatible(pybot_version="0.5.0")

    @pytest.mark.parametrize(
        "actual, rule, expected",
        [
            ("1.0.0", "*", True),
            ("1.0.0", ">=1.0.0", True),
            ("1.0.0", ">1.0.0", False),
            ("0.9.0", ">=1.0.0", False),
            ("1.5.3", ">=1.0.0,<2.0.0", True),
            ("2.0.0", ">=1.0.0,<2.0.0", False),
            ("1.2.3", "==1.2.3", True),
        ],
    )
    def test_semver_comparator(self, actual, rule, expected):
        assert _check_semver_rule(actual, rule) is expected

    def test_install_local_copies_payload(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"

        result = install(str(payload), extensions_root=target)

        install_dir = target / "demo_skill"
        assert install_dir.exists()
        assert (install_dir / MANIFEST_FILENAME).exists()
        assert (install_dir / "demo_skill.py").exists()
        assert (install_dir / ".install.json").exists()
        assert result.manifest.id == "demo_skill"
        assert result.upgraded_from is None

    def test_install_emits_capability_installed(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"

        captured: list[Event] = []

        def _on(event: Event) -> None:
            if event.payload.get("id") == "demo_skill":
                captured.append(event)

        event_bus.subscribe(EventType.CAPABILITY_INSTALLED, _on)
        try:
            install(str(payload), extensions_root=target)
        finally:
            event_bus.unsubscribe(EventType.CAPABILITY_INSTALLED, _on)

        assert captured, "expected a CAPABILITY_INSTALLED event"
        payload_dict = captured[-1].payload
        assert payload_dict["kind"] == "skill"
        assert payload_dict["version"] == "0.1.0"

    def test_install_upgrade_records_previous_version(self, tmp_path):
        src1 = _make_extension(tmp_path / "ext1")
        target = tmp_path / "extensions"
        install(str(src1), extensions_root=target)

        src2 = _make_extension(tmp_path / "ext2", manifest_overrides={"version": "0.2.0"})
        result = install(str(src2), extensions_root=target)

        assert result.upgraded_from == "0.1.0"
        assert result.manifest.version == "0.2.0"

    def test_install_dry_run_creates_nothing(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"
        result = install(str(payload), extensions_root=target, dry_run=True)
        assert result.manifest.id == "demo_skill"
        assert not (target / "demo_skill").exists()

    def test_install_rejects_ungranted_permissions(self, tmp_path):
        payload = _make_extension(
            tmp_path / "ext", manifest_overrides={"permissions": ["network"]}
        )
        target = tmp_path / "extensions"
        with pytest.raises(PermissionError, match="ungranted permissions"):
            install(
                str(payload),
                extensions_root=target,
                grant_permissions=set(),
            )

    def test_install_grants_all_when_grant_set_is_none(self, tmp_path):
        payload = _make_extension(
            tmp_path / "ext", manifest_overrides={"permissions": ["network"]}
        )
        target = tmp_path / "extensions"
        result = install(str(payload), extensions_root=target, grant_permissions=None)
        assert "network" in result.manifest.permissions

    def test_install_missing_manifest_raises(self, tmp_path):
        src = tmp_path / "ext"
        src.mkdir()
        with pytest.raises(ManifestError, match=MANIFEST_FILENAME):
            install(str(src), extensions_root=tmp_path / "extensions")

    def test_list_installed_skips_broken_directories(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"
        install(str(payload), extensions_root=target)
        (target / "broken").mkdir()  # no manifest

        out = list_installed(target)
        ids = [m.id for m in out]
        assert ids == ["demo_skill"]

    def test_uninstall_removes_directory_and_returns_true(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"
        install(str(payload), extensions_root=target)
        assert uninstall("demo_skill", extensions_root=target) is True
        assert not (target / "demo_skill").exists()
        assert uninstall("demo_skill", extensions_root=target) is False

    def test_autodetect_local(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        src = InstallSource.autodetect(str(payload))
        assert src.kind is InstallSourceKind.LOCAL

    def test_autodetect_git_url(self):
        src = InstallSource.autodetect("https://github.com/user/repo.git")
        assert src.kind is InstallSourceKind.GIT

    def test_autodetect_marketplace_falls_back_to_name(self):
        src = InstallSource.autodetect("nice-extension")
        assert src.kind is InstallSourceKind.MARKETPLACE
        assert src.location == "nice-extension"

    def test_marketplace_install_requires_resolver(self, tmp_path):
        target = tmp_path / "extensions"
        src = InstallSource(InstallSourceKind.MARKETPLACE, "anything")
        with pytest.raises(RuntimeError, match="marketplace_resolver"):
            install(src, extensions_root=target)

    def test_marketplace_install_resolves_to_local_then_installs(self, tmp_path):
        payload = _make_extension(tmp_path / "ext")
        target = tmp_path / "extensions"

        def resolver(name: str) -> InstallSource:
            assert name == "demo_skill"
            return InstallSource(InstallSourceKind.LOCAL, str(payload))

        src = InstallSource(InstallSourceKind.MARKETPLACE, "demo_skill")
        result = install(src, extensions_root=target, marketplace_resolver=resolver)
        assert result.manifest.id == "demo_skill"
        assert (target / "demo_skill" / MANIFEST_FILENAME).exists()


# ---------------------------------------------------------------------------
# Test Marketplace and Settings (formerly test_marketplace_and_settings.py)
# ---------------------------------------------------------------------------

_MARKET_RAW = {
    "schema_version": MARKETPLACE_SCHEMA_VERSION,
    "updated_at": 1717720000.0,
    "entries": [
        {
            "id": "weather",
            "kind": "skill",
            "version": "0.3.1",
            "name": "Weather skill",
            "description": "Calls OpenWeatherMap.",
            "git": "https://example.com/weather.git",
            "tags": ["api", "weather"],
        },
        {
            "id": "monitor",
            "kind": "agent",
            "version": "1.0.0",
            "description": "BDA-K monitor",
            "path": "/local/path/monitor",
            "tags": ["ml", "monitor"],
        },
    ],
}

_BASE_SCHEMA = {
    "type": "object",
    "title": "Demo",
    "required": ["api_key"],
    "properties": {
        "api_key": {
            "type": "string",
            "title": "API key",
            "minLength": 8,
        },
        "interval_seconds": {
            "type": "integer",
            "default": 300,
            "minimum": 30,
            "maximum": 86400,
        },
        "alerting": {
            "type": "boolean",
            "default": True,
        },
        "channel": {
            "type": "string",
            "enum": ["email", "wechat", "feishu"],
            "default": "email",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
    },
}


def _install_demo(tmp_path: Path) -> Path:
    src = tmp_path / "ext"
    src.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": "demo",
        "kind": "skill",
        "version": "0.1.0",
        "entrypoint": "demo:run",
        "settings_schema": "settings.schema.json",
    }
    (src / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    (src / "settings.schema.json").write_text(json.dumps(_BASE_SCHEMA), encoding="utf-8")
    target = tmp_path / "extensions"
    install(str(src), extensions_root=target)
    return target


class TestMarketplaceAndSettingsClass:
    def test_index_round_trip(self, tmp_path):
        p = tmp_path / "marketplace.json"
        p.write_text(json.dumps(_MARKET_RAW), encoding="utf-8")
        idx = MarketplaceIndex.from_path(p)
        assert idx.schema_version == MARKETPLACE_SCHEMA_VERSION
        assert len(idx.entries) == 2
        assert idx.find("weather") is not None
        assert idx.find("missing") is None

    def test_index_search_by_text(self):
        idx = MarketplaceIndex.from_dict(_MARKET_RAW)
        out = idx.search("weather")
        assert [e.id for e in out] == ["weather"]

    def test_index_search_by_kind_and_tag(self):
        idx = MarketplaceIndex.from_dict(_MARKET_RAW)
        assert [e.id for e in idx.search(kind="agent")] == ["monitor"]
        assert [e.id for e in idx.search(tag="api")] == ["weather"]

    def test_entry_install_url_prefers_git_then_path(self):
        entry = MarketplaceEntry(id="x", kind="skill", version="0.0.1", git="g", path="p")
        assert entry.install_url() == "g"
        entry2 = MarketplaceEntry(id="x", kind="skill", version="0.0.1", path="p")
        assert entry2.install_url() == "p"

    def test_entry_install_url_raises_when_empty(self):
        entry = MarketplaceEntry(id="x", kind="skill", version="0.0.1")
        with pytest.raises(MarketplaceError):
            entry.install_url()

    def test_index_rejects_non_object(self):
        with pytest.raises(MarketplaceError):
            MarketplaceIndex.from_dict([])  # type: ignore[arg-type]

    def test_index_rejects_entry_missing_id(self):
        with pytest.raises(MarketplaceError, match="id"):
            MarketplaceIndex.from_dict({"entries": [{"kind": "skill", "version": "0.0.1"}]})

    def test_schema_parses_all_property_kinds(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        assert s.title == "Demo"
        names = [p.name for p in s.properties]
        assert names == ["api_key", "interval_seconds", "alerting", "channel", "tags"]
        api_key = next(p for p in s.properties if p.name == "api_key")
        assert api_key.required and api_key.min_length == 8

    def test_schema_defaults_extracted(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        defaults = s.defaults()
        assert defaults["interval_seconds"] == 300
        assert defaults["alerting"] is True
        assert defaults["channel"] == "email"

    def test_schema_validate_succeeds_on_well_formed(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        out = s.validate({"api_key": "abcdefgh", "interval_seconds": 60, "tags": ["x"]})
        assert out["interval_seconds"] == 60
        assert out["tags"] == ["x"]

    def test_schema_validate_drops_unknown_keys(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        out = s.validate({"api_key": "abcdefgh", "_legacy": "ignored"})
        assert "_legacy" not in out

    def test_schema_validate_enum_violation(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="must be one of"):
            s.validate({"api_key": "abcdefgh", "channel": "sms"})

    def test_schema_validate_minimum_violation(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="below minimum"):
            s.validate({"api_key": "abcdefgh", "interval_seconds": 5})

    def test_schema_validate_min_length_violation(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="minLength"):
            s.validate({"api_key": "tiny"})

    def test_schema_validate_array_item_type(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="items"):
            s.validate({"api_key": "abcdefgh", "tags": ["ok", 5]})

    def test_schema_validate_required_missing(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="missing required"):
            s.validate({})

    def test_schema_validate_boolean_strict(self):
        s = SettingsSchema.from_dict(_BASE_SCHEMA)
        with pytest.raises(SettingsError, match="boolean"):
            s.validate({"api_key": "abcdefgh", "alerting": "yes"})

    def test_schema_rejects_unknown_property_type(self):
        with pytest.raises(SettingsError, match="unsupported type"):
            SettingsSchema.from_dict({"properties": {"x": {"type": "weird"}}})

    def test_store_returns_defaults_when_no_settings_yet(self, tmp_path):
        target = _install_demo(tmp_path)
        store = SettingsStore(target)
        with pytest.raises(SettingsError):
            # api_key is required so reading defaults alone must still validate;
            # since defaults don't contain api_key, validation throws.
            store.read("demo")

    def test_store_round_trip(self, tmp_path):
        target = _install_demo(tmp_path)
        store = SettingsStore(target)
        written = store.write("demo", {"api_key": "abcdefgh", "interval_seconds": 60})
        assert written["interval_seconds"] == 60

        file = target / "demo" / SETTINGS_FILENAME
        assert json.loads(file.read_text(encoding="utf-8")) == written

        out = store.read("demo")
        assert out["api_key"] == "abcdefgh"
        assert out["interval_seconds"] == 60
        assert out["alerting"] is True  # came from defaults

    def test_store_write_rejects_invalid(self, tmp_path):
        target = _install_demo(tmp_path)
        store = SettingsStore(target)
        with pytest.raises(SettingsError):
            store.write("demo", {"api_key": "bad"})  # too short

    def test_store_write_emits_settings_changed_event(self, tmp_path):
        target = _install_demo(tmp_path)
        store = SettingsStore(target)

        captured: list[Event] = []

        def _on(event: Event) -> None:
            if (event.payload or {}).get("task_event") == "settings.changed":
                captured.append(event)

        event_bus.subscribe(EventType.SCHEDULE_RUN, _on)
        try:
            store.write("demo", {"api_key": "abcdefgh"})
        finally:
            event_bus.unsubscribe(EventType.SCHEDULE_RUN, _on)

        assert captured, "expected a settings.changed event"
        payload = captured[-1].payload
        assert payload["extension_id"] == "demo"
        assert payload["settings"]["api_key"] == "abcdefgh"

    def test_store_write_unknown_extension_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "nope")
        with pytest.raises(SettingsError):
            store.write("ghost", {"api_key": "abcdefgh"})

    def test_store_handles_missing_schema_gracefully(self, tmp_path):
        target = tmp_path / "extensions"
        target.mkdir()
        (target / "bare").mkdir()
        # No manifest, no schema — read returns whatever is on disk.
        store = SettingsStore(target)
        assert store.read("bare") == {}


# ── Runtime and Hook Tests ────────────────────────────────────────────

def _write_plugin(tmp_path: Path, *, plugin_id: str = "demo_plugin") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text(
        """
from core.plugin_sdk import on_message, on_tool_call, pybot_plugin

events = []


@pybot_plugin(id="demo_plugin", name="Demo Plugin")
class DemoPlugin:
    def on_load(self, manifest):
        events.append(("load", manifest.id))

    def on_enable(self, manifest):
        events.append(("enable", manifest.id))

    def on_disable(self, manifest):
        events.append(("disable", manifest.id))

    def on_unload(self, manifest):
        events.append(("unload", manifest.id))

    @on_tool_call(when="before")
    def before_tool(self, ctx):
        ctx.arguments["from_plugin"] = "yes"
        if ctx.tool_name == "blocked_tool":
            ctx.block("blocked by plugin")

    @on_tool_call(when="after")
    def after_tool(self, ctx):
        if hasattr(ctx.result, "content"):
            ctx.result.content = "decorated:" + str(ctx.result.content)

    @on_message
    def before_message(self, ctx):
        ctx.content = ctx.content.upper()
""".strip(),
        encoding="utf-8",
    )
    (plugin_dir / "pybot.plugin.json").write_text(
        json.dumps(
            {
                "id": "demo_plugin",
                "name": "Demo Plugin",
                "version": "1.0.0",
                "capabilities": ["hooks"],
                "entry_point": plugin_id,
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _build_tool_runtime(*, registry: PluginRegistry) -> ToolCallRuntime:
    return ToolCallRuntime(
        inventory=DynamicToolInventory(),
        control_runtime=ToolControlRuntime(
            control_policy=AgentControlPolicy.from_config({"mode": "open"}),
            approval_scope="root:test",
        ),
        delegated_runtime=DelegatedToolApprovalRuntime(
            approval_queue=ApprovalQueue(),
            approval_scope="root:test",
        ),
        plugin_registry=registry,
    )


def test_plugin_registry_lifecycle_and_hooks(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()

    discovered = discover_plugins([str(tmp_path)], registry=registry)
    assert [item.id for item in discovered] == ["demo_plugin"]

    runtime = registry.enable_plugin("demo_plugin")
    assert runtime.enabled is True
    assert runtime.module is not None
    assert runtime.module.events[:2] == [("load", "demo_plugin"), ("enable", "demo_plugin")]
    assert len(runtime.before_tool_call_handlers) == 1
    assert len(runtime.after_tool_call_handlers) == 1
    assert len(runtime.message_handlers) == 1

    registry.disable_plugin("demo_plugin")
    registry.unload_plugin("demo_plugin")
    assert runtime.module.events[-2:] == [("disable", "demo_plugin"), ("unload", "demo_plugin")]


def test_tool_call_runtime_applies_plugin_before_and_after_hooks(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()
    discover_plugins([str(tmp_path)], registry=registry)
    registry.enable_plugin("demo_plugin")
    runtime = _build_tool_runtime(registry=registry)

    request = SimpleNamespace(tool_call={"name": "echo_tool", "args": {"value": "hello"}, "id": "call_1"})
    captured: dict[str, str] = {}

    def handler(req):
        captured["from_plugin"] = req.tool_call["args"]["from_plugin"]
        return ToolMessage(content="ok", tool_call_id="call_1", status="success")

    result = runtime.run_tool_call(request, handler)

    assert captured["from_plugin"] == "yes"
    assert isinstance(result, ToolMessage)
    assert result.content == "decorated:ok"


def test_tool_call_runtime_blocks_when_plugin_cancels(tmp_path: Path):
    _write_plugin(tmp_path)
    registry = PluginRegistry()
    discover_plugins([str(tmp_path)], registry=registry)
    registry.enable_plugin("demo_plugin")
    runtime = _build_tool_runtime(registry=registry)

    request = SimpleNamespace(tool_call={"name": "blocked_tool", "args": {"value": "hello"}, "id": "call_2"})
    called = {"count": 0}

    def handler(_req):
        called["count"] += 1
        return ToolMessage(content="ok", tool_call_id="call_2", status="success")

    result = runtime.run_tool_call(request, handler)

    assert called["count"] == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "blocked by plugin" in result.content


def test_pybot_chat_applies_message_plugin_hooks(tmp_path: Path):
    reset_plugin_registry()
    _write_plugin(tmp_path)
    discover_plugins([str(tmp_path)], registry=get_plugin_registry())
    get_plugin_registry().enable_plugin("demo_plugin")

    class _Bus:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def record_invocation(self, name: str, ok: bool) -> None:
            self.calls.append((name, ok))

    bot = PyBot.__new__(PyBot)
    bot.storage = SimpleNamespace(tools={})
    bot.capability_bus = _Bus()
    bot.thread_id = "thread-1"
    bot._initialize_agent = lambda: None
    bot._do_invoke = lambda message, *, tools_before: {"response": message}

    result = bot.chat("hello plugin")

    assert result == "HELLO PLUGIN"
    assert bot.capability_bus.calls[-1] == ("chat", True)


# ── File Lock & Webhook Verification & Protection ─────────────────────

def test_file_lock_acquires_and_releases(tmp_path: Path):
    lock_file = tmp_path / "test.lock"

    with acquire_file_lock(lock_file):
        assert lock_file.exists()

    assert not lock_file.exists()


def test_file_lock_timeout(tmp_path: Path):
    lock_file = tmp_path / "test.lock"

    with acquire_file_lock(lock_file):
        with pytest.raises(FileLockTimeout):
            with acquire_file_lock(lock_file, timeout=0.1, retry_interval=0.05):
                pass


def test_webhook_hmac_signature():
    secret = "my-secret"
    payload = b'{"hello": "world"}'

    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    valid_sig = mac.hexdigest()

    assert verify_hmac_signature(payload, valid_sig, secret) is True
    assert verify_hmac_signature(payload, "sha256=" + valid_sig, secret, header_prefix="sha256=") is True
    assert verify_hmac_signature(payload, "wrong-sig", secret) is False


def test_plugin_uninstall_protection():
    registry = PluginRegistry()
    manifest = PluginManifest(
        id="core-plugin",
        name="Core",
        metadata={"protected": True},
    )
    registry.register(manifest)

    with pytest.raises(ValueError, match="is protected and cannot be uninstalled"):
        registry.unregister("core-plugin")

