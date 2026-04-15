from __future__ import annotations

import json

from core.assets.tools import get_permission_tools


class _DummyPermissionMiddleware:
    def __init__(self) -> None:
        self.snapshot = {
            "mode": "default",
            "rules": {},
            "write_tools": ["set_permission_mode"],
            "recent_events": [],
            "rule_count": 0,
            "summary": "mode=default, 0 active rules",
        }

    def get_permission_snapshot(self):
        return dict(self.snapshot)

    def set_permission_mode(self, mode):
        self.snapshot["mode"] = str(mode)
        self.snapshot["summary"] = f"mode={mode}, {self.snapshot['rule_count']} active rules"
        return self.get_permission_snapshot()

    def add_permission_rule(self, tool_name, verdict, *, reason="", source="session"):
        self.snapshot["rules"][tool_name] = {"verdict": verdict, "reason": reason, "source": source}
        self.snapshot["rule_count"] = len(self.snapshot["rules"])
        self.snapshot["summary"] = f"mode={self.snapshot['mode']}, {self.snapshot['rule_count']} active rules"
        return self.get_permission_snapshot()

    def remove_permission_rule(self, tool_name):
        self.snapshot["rules"].pop(tool_name, None)
        self.snapshot["rule_count"] = len(self.snapshot["rules"])
        self.snapshot["summary"] = f"mode={self.snapshot['mode']}, {self.snapshot['rule_count']} active rules"
        return self.get_permission_snapshot()

    def clear_permission_rules(self):
        self.snapshot["rules"] = {}
        self.snapshot["rule_count"] = 0
        self.snapshot["summary"] = f"mode={self.snapshot['mode']}, 0 active rules"
        return self.get_permission_snapshot()


def test_permission_tools_manage_control_plane():
    tools = {tool.name: tool for tool in get_permission_tools(_DummyPermissionMiddleware())}

    state = json.loads(tools["get_permission_state"]._run())
    assert state["success"] is True
    assert state["permission"]["mode"] == "default"

    mode_result = json.loads(tools["set_permission_mode"]._run(mode="plan"))
    assert mode_result["permission"]["mode"] == "plan"

    rule_result = json.loads(
        tools["set_permission_rule"]._run(
            tool_name="read_file",
            verdict="ask",
            reason="manual review",
            source="session",
        )
    )
    assert rule_result["permission"]["rules"]["read_file"]["verdict"] == "ask"

    remove_result = json.loads(tools["remove_permission_rule"]._run(tool_name="read_file"))
    assert remove_result["permission"]["rule_count"] == 0

    clear_result = json.loads(tools["clear_permission_rules"]._run())
    assert clear_result["permission"]["rule_count"] == 0
