"""Tests for core.assets.workflows.task_definition — TaskDefinition and TaskPipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.assets.workflows.task_definition import TaskDefinition, TaskPipeline, TaskStatus


class ResearchResult(BaseModel):
    summary: str = Field(description="Research summary")
    findings: list[str] = Field(description="Key findings")


class TestTaskDefinition:
    def test_build_prompt_basic(self):
        task = TaskDefinition(
            name="research",
            description="Research AI trends",
            expected_output="A summary with findings",
        )
        prompt = task.build_prompt()
        assert "research" in prompt.lower()
        assert "Expected Output" in prompt

    def test_build_prompt_with_schema(self):
        task = TaskDefinition(
            name="research",
            description="Research AI trends",
            expected_output="A JSON result",
            output_schema=ResearchResult,
        )
        prompt = task.build_prompt()
        assert "Output Schema" in prompt
        assert "summary" in prompt

    def test_build_prompt_with_context(self):
        task = TaskDefinition(
            name="write_report",
            description="Write report",
            expected_output="A report",
            context_from=["research"],
        )
        prompt = task.build_prompt(context={"research": "AI is growing fast"})
        assert "Context from Previous Tasks" in prompt
        assert "AI is growing fast" in prompt

    def test_validate_output_no_schema(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        ok, err = task.validate_output("any string")
        assert ok is True

    def test_validate_output_dict(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output({"summary": "s", "findings": ["a"]})
        assert ok is True

    def test_validate_output_json_string(self):
        import json
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output(json.dumps({"summary": "s", "findings": ["a"]}))
        assert ok is True

    def test_validate_output_invalid(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output({"summary": "s"})
        assert ok is False
        assert "findings" in err

    def test_validate_output_wrong_type(self):
        task = TaskDefinition(
            name="t", description="d", expected_output="e",
            output_schema=ResearchResult,
        )
        ok, err = task.validate_output(42)
        assert ok is False

    def test_lifecycle(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        assert task.status == TaskStatus.PENDING
        task.mark_started()
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.attempt == 1
        task.mark_completed("done")
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "done"
        assert task.elapsed_seconds is not None

    def test_can_retry(self):
        task = TaskDefinition(name="t", description="d", expected_output="e", max_retries=2)
        assert task.can_retry is True
        task.mark_started()
        task.mark_started()
        task.mark_started()
        assert task.can_retry is False

    def test_to_dict(self):
        task = TaskDefinition(
            name="research",
            description="Research AI",
            expected_output="Summary",
            agent_name="researcher",
            output_schema=ResearchResult,
        )
        d = task.to_dict()
        assert d["name"] == "research"
        assert d["agent_name"] == "researcher"
        assert d["output_schema"] == "ResearchResult"
        assert d["status"] == "pending"


class TestTaskPipeline:
    def test_sequential_execution(self):
        task1 = TaskDefinition(name="step1", description="Do step 1", expected_output="result1")
        task2 = TaskDefinition(
            name="step2", description="Do step 2",
            expected_output="result2", context_from=["step1"],
        )

        def execute_fn(prompt: str, agent_name=None):
            if "step1" in prompt.lower():
                return "step1 output"
            return "step2 output"

        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn)
        assert "step1" in results
        assert "step2" in results
        assert task1.status == TaskStatus.COMPLETED
        assert task2.status == TaskStatus.COMPLETED

    def test_stop_on_failure(self):
        task1 = TaskDefinition(name="fail_task", description="Will fail", expected_output="x")
        task2 = TaskDefinition(name="skip_task", description="Skipped", expected_output="y")

        def execute_fn(prompt, agent_name=None):
            raise RuntimeError("boom")

        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn, stop_on_failure=True)
        assert len(results) == 0
        assert task1.status == TaskStatus.FAILED
        assert task2.status == TaskStatus.PENDING

    def test_continue_on_failure(self):
        call_count = 0

        def execute_fn(prompt, agent_name=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first fails")
            return "ok"

        task1 = TaskDefinition(name="t1", description="d1", expected_output="e1")
        task2 = TaskDefinition(name="t2", description="d2", expected_output="e2")
        pipeline = TaskPipeline(tasks=[task1, task2])
        results = pipeline.run(execute_fn, stop_on_failure=False)
        assert task1.status == TaskStatus.FAILED
        assert task2.status == TaskStatus.COMPLETED
        assert "t2" in results

    def test_output_validation_retry(self):
        call_count = 0

        def execute_fn(prompt, agent_name=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return '{"summary": "s"}'
            return '{"summary": "s", "findings": ["a"]}'

        task = TaskDefinition(
            name="validated",
            description="d",
            expected_output="JSON",
            output_schema=ResearchResult,
            max_retries=1,
        )
        pipeline = TaskPipeline(tasks=[task])
        pipeline.run(execute_fn)
        assert task.status == TaskStatus.COMPLETED
        assert call_count == 2

    def test_summary(self):
        task = TaskDefinition(name="t", description="d", expected_output="e")
        pipeline = TaskPipeline(tasks=[task])
        summary = pipeline.summary()
        assert len(summary) == 1
        assert summary[0]["name"] == "t"
