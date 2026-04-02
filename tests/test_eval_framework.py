from __future__ import annotations

import pytest

from core.systems.eval.eval_framework import EvalFramework
from core.systems.eval.eval_models import TestCase as EvalTestCase


def test_eval_framework_saves_and_loads_suite(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    cases = [
        EvalTestCase(
            name="math",
            input_prompt="1+1等于几？",
            expected_contains=["2"],
            min_score=0.4,
        )
    ]

    framework.save_test_suite("smoke", cases)
    loaded = framework.load_test_suite("smoke")

    assert len(loaded) == 1
    assert loaded[0].name == "math"
    assert loaded[0].expected_contains == ["2"]
    assert (temp_paths.workspace_dir / "tests" / "smoke.json").exists()


def test_eval_framework_blocks_suite_path_escape(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))

    with pytest.raises(ValueError, match="Invalid suite name"):
        framework.save_test_suite("../escape", [])


def test_eval_framework_runs_suite_and_persists_report(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    framework.set_agent_callback(lambda prompt: "答案是 2，而且代码是 print('hi')")

    result = framework.run_test_suite(
        [
            EvalTestCase(
                name="code",
                input_prompt="写代码回答 1+1 等于几",
                expected_contains=["2", "print"],
                min_score=0.4,
            )
        ]
    )

    assert result["passed"] == 1
    report_files = list((temp_paths.workspace_dir / "test_results").glob("eval_*.json"))
    assert report_files


def test_eval_framework_falls_back_to_heuristics_when_llm_eval_is_invalid(temp_paths):
    framework = EvalFramework(str(temp_paths.workspace_dir))
    framework.set_agent_callback(lambda prompt: "not-json")

    result = framework.eval_response("写一个函数", "def hello():\n    return 'hi'")

    assert result.test_name == "heuristic_eval"
    assert result.score > 0
