from __future__ import annotations

import json

from core.workflow_definition_runtime import WorkflowDefinitionRuntime
from core.workflow_models import NodeType


def test_workflow_definition_runtime_builds_linear_workflow_without_mutating_input():
    runtime = WorkflowDefinitionRuntime()
    payload = {
        "name": "demo",
        "nodes": [
            {
                "id": "lookup",
                "type": "tool",
                "label": "Lookup",
                "tool": "search_docs",
            }
        ],
    }

    workflow = runtime.build_workflow(payload)

    assert [node["id"] for node in payload["nodes"]] == ["lookup"]
    assert list(workflow.nodes) == ["_start", "lookup", "_end"]
    assert workflow.nodes["lookup"].type == NodeType.TOOL
    assert workflow.nodes["lookup"].config == {"tool": "search_docs"}
    assert [(edge.source, edge.target) for edge in workflow.edges] == [
        ("_start", "lookup"),
        ("lookup", "_end"),
    ]


def test_workflow_definition_runtime_parses_json_definition():
    runtime = WorkflowDefinitionRuntime()
    definition = json.dumps(
        {
            "name": "demo",
            "tags": "ops, nightly",
            "nodes": [
                {
                    "id": "code_step",
                    "type": "code",
                    "code": "print('hi')",
                    "position": "{'x': 20, 'y': 40}",
                }
            ],
        },
        ensure_ascii=False,
    )

    workflow = runtime.parse_workflow(definition)

    assert workflow.tags == ["ops", "nightly"]
    assert workflow.nodes["code_step"].config["code"] == "print('hi')"
    assert workflow.nodes["code_step"].position == {"x": 20, "y": 40}


def test_workflow_definition_runtime_preserves_declared_edges():
    runtime = WorkflowDefinitionRuntime()
    workflow = runtime.build_workflow(
        {
            "name": "branched",
            "nodes": [
                {"id": "_start", "type": "start"},
                {"id": "route", "type": "condition"},
                {"id": "yes", "type": "exec"},
                {"id": "_end", "type": "end"},
            ],
            "edges": [
                {"source": "_start", "target": "route"},
                {"source": "route", "target": "yes", "condition": "ok"},
                {"source": "yes", "target": "_end"},
            ],
        }
    )

    assert [(edge.source, edge.target, edge.condition) for edge in workflow.edges] == [
        ("_start", "route", None),
        ("route", "yes", "ok"),
        ("yes", "_end", None),
    ]
