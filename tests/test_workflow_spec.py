from __future__ import annotations

from core.assets.workflows.workflow_spec import export_workflow_spec, parse_workflow_spec, strip_workflow_runtime

SPEC_TEXT = """
name: demo_workflow
description: Demo workflow

nodes:
  - id: step1
    type: exec
    command: echo hello
  - id: step2
    type: llm
    prompt: "summarize ${step1.output}"

edges:
  - step1 -> step2
""".strip()


def test_workflow_spec_round_trip():
    definition = parse_workflow_spec(SPEC_TEXT)

    assert definition["name"] == "demo_workflow"
    assert definition["nodes"][0]["type"] == "start"
    assert definition["nodes"][-1]["type"] == "end"

    rendered = export_workflow_spec(definition)
    reparsed = parse_workflow_spec(rendered)

    assert reparsed["name"] == "demo_workflow"
    assert [node["id"] for node in reparsed["nodes"][1:-1]] == ["step1", "step2"]


def test_strip_workflow_runtime_removes_runtime_fields():
    definition = parse_workflow_spec(SPEC_TEXT)
    definition["status"] = "running"
    definition["resume_token"] = "token"
    definition["nodes"][1]["status"] = "completed"
    definition["nodes"][1]["output"] = {"value": 1}

    stripped = strip_workflow_runtime(definition)

    assert "status" not in stripped
    assert "resume_token" not in stripped
    assert "status" not in stripped["nodes"][1]
    assert "output" not in stripped["nodes"][1]


def test_workflow_spec_api_accepts_spec(client):
    create_response = client.post(
        "/api/workflows/from-spec",
        json={"name": "demo_spec", "spec_content": SPEC_TEXT},
    )
    assert create_response.status_code == 200
    assert create_response.json()["success"] is True

    definition_response = client.get("/api/workflows/demo_spec/definition")
    assert definition_response.status_code == 200

    payload = definition_response.json()
    assert payload["spec_content"]
    assert "yaml_content" not in payload
