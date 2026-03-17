from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from core.onboarding import OnboardingWizard, build_initial_config, launch_selected_mode


def make_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=100)


def test_build_initial_config_creates_expected_schema():
    config = build_initial_config(api_base="https://example.com/v1", api_key="secret", model="gpt-4o-mini")

    assert config["llm_config"]["api_base"] == "https://example.com/v1"
    assert config["llm_config"]["api_key"] == "secret"
    assert config["llm_config"]["model"] == "gpt-4o-mini"
    assert config["agent_config"]["thread_id"] == "default_session"


def test_launch_selected_mode_returns_command():
    launched: list[list[str]] = []
    command = launch_selected_mode("2", executable="python", runner=lambda cmd, check=False: launched.append(cmd))

    assert command == ["python", "interactive_cli.py"]
    assert launched == [["python", "interactive_cli.py"]]


def test_onboarding_wizard_saves_config_and_skips_launch(tmp_path: Path):
    answers = iter(["https://example.com/v1", "secret-key", "gpt-4o-mini", "3"])

    wizard = OnboardingWizard(
        console=make_console(),
        prompt=lambda *args, **kwargs: next(answers),
        runner=lambda *args, **kwargs: None,
        executable="python",
        config_path=tmp_path / "config.json",
        clear_screen_fn=lambda: None,
    )

    saved_path = wizard.run()

    assert saved_path == (tmp_path / "config.json").resolve()
    payload = saved_path.read_text(encoding="utf-8")
    assert "secret-key" in payload
    assert "gpt-4o-mini" in payload
