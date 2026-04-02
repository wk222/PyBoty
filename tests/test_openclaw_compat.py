from __future__ import annotations

from pathlib import Path

from core.assets.skills.openclaw_compat import (
    build_openclaw_runtime_env,
    build_openclaw_skill_bridge_report,
    build_openclaw_source_specs,
    import_openclaw_channels_for_pybot,
)
from core.assets.skills.skill_models import SkillDefinition


def test_build_openclaw_source_specs_resolves_relative_extra_dirs(tmp_path: Path):
    repo_root = tmp_path / "openclaw-main"
    repo_skill = repo_root / "skills" / "weather"
    repo_skill.mkdir(parents=True, exist_ok=True)
    (repo_skill / "SKILL.md").write_text(
        """---
name: weather
description: Weather
---
""",
        encoding="utf-8",
    )

    config_path = tmp_path / ".openclaw" / "openclaw.json"
    extra_dir = config_path.parent / "shared-skills"
    extra_skill = extra_dir / "ops"
    extra_skill.mkdir(parents=True, exist_ok=True)
    (extra_skill / "SKILL.md").write_text(
        """---
name: ops
description: Ops
---
""",
        encoding="utf-8",
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
skills:
  load:
    extraDirs:
      - ./shared-skills
""".strip(),
        encoding="utf-8",
    )

    bridge = build_openclaw_source_specs(
        repo_root,
        config_path=config_path,
        source_name="vendor",
        include_extra_dirs=True,
    )

    assert bridge["config_loaded"] is True
    assert [item["name"] for item in bridge["source_specs"]] == [
        "vendor",
        "vendor_extra_1_shared_skills",
    ]
    assert bridge["config_summary"]["extra_dirs"] == [str(extra_dir.resolve())]


def test_build_openclaw_skill_bridge_report_uses_skill_key_and_entry_state():
    skill = SkillDefinition(
        name="weather-skill",
        description="Weather",
        skill_format="openclaw",
        openclaw_metadata={"skillKey": "weather", "emoji": "⛅"},
        primary_env="WEATHER_TOKEN",
        requires_config=["channels.weather"],
    )

    report = build_openclaw_skill_bridge_report(
        skill,
        {
            "skills": {
                "entries": {
                    "weather": {
                        "enabled": True,
                        "apiKey": "secret",
                        "env": {"WEATHER_TOKEN": "from-config"},
                        "config": {"endpoint": "https://example.com"},
                    }
                }
            },
            "channels": {"weather": {"token": "configured"}},
        },
    )

    assert report["entry_key"] == "weather"
    assert report["entry_present"] is True
    assert report["entry_enabled"] is True
    assert report["entry_api_key_present"] is True
    assert report["entry_env_keys"] == ["WEATHER_TOKEN"]
    assert report["entry_config_keys"] == ["endpoint"]
    assert report["primary_env_bridge"][0]["available_via_api_key"] is True
    assert report["primary_env_bridge"][0]["available_via_entry_env"] is True
    assert report["global_config_bridge"][0] == {"path": "channels.weather", "present": True}


def test_build_openclaw_runtime_env_prefers_entry_env_and_api_key():
    skill = SkillDefinition(
        name="weather",
        description="Weather",
        skill_format="openclaw",
        primary_env="WEATHER_TOKEN",
    )

    env = build_openclaw_runtime_env(
        skill,
        {
            "skills": {
                "entries": {
                    "weather": {
                        "enabled": True,
                        "apiKey": "secret-api-key",
                        "env": {"OTHER_TOKEN": "other", "WEATHER_TOKEN": "preferred"},
                    }
                }
            }
        },
    )

    assert env == {"OTHER_TOKEN": "other", "WEATHER_TOKEN": "preferred"}


def test_build_openclaw_runtime_env_respects_disabled_entry():
    skill = SkillDefinition(
        name="weather",
        description="Weather",
        skill_format="openclaw",
        primary_env="WEATHER_TOKEN",
    )

    env = build_openclaw_runtime_env(
        skill,
        {"skills": {"entries": {"weather": {"enabled": False, "apiKey": "secret"}}}},
    )

    assert env == {}


def test_import_openclaw_channels_for_pybot_imports_supported_and_skips_rest():
    result = import_openclaw_channels_for_pybot(
        {
            "channels": {
                "wechat": {"token": "wechat-token"},
                "webhook": {"enabled": True},
                "discord": {"enabled": True},
            }
        },
        {"existing": {"enabled": True}},
    )

    assert set(result["imported"]) == {"wechat", "webhook"}
    assert result["channels"]["existing"]["enabled"] is True
    assert result["channels"]["wechat"]["kind"] == "wechat"
    assert result["channels"]["webhook"]["kind"] == "webhook"
    assert result["skipped"] == [{"name": "discord", "reason": "unsupported_by_pybot"}]
