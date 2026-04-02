"""Generic LLM sub-task tool — prompt + input + schema → structured JSON.

Allows agents and workflows
to fire one-shot LLM calls that return validated JSON, without granting the
sub-call access to tools or memory.

Usage (as a LangChain tool for agents):

    tool = LLMTaskTool(llm=my_chat_model)
    result = tool.invoke({
        "prompt": "Classify the sentiment of the following review.",
        "input_text": "Great product, 5 stars!",
        "schema": {"type": "object", "properties": {"sentiment": {"type": "string"}}},
    })

Usage (as a workflow node helper):

    from core.llm_task_tool import run_llm_task
    output = run_llm_task(
        prompt="Summarise the following text in one sentence.",
        input_text=long_doc,
        llm=my_model,
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LLMTaskInput(BaseModel):
    prompt: str = Field(description="System-level instruction for the LLM (e.g. 'Classify sentiment')")
    input_text: str = Field(description="User-level input to process")
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON Schema the output must conform to. If omitted, raw text is returned.",
    )
    model: str | None = Field(default=None, description="Override model name (if supported by provider)")
    temperature: float | None = Field(default=None, description="Override temperature (0-2)")


_JSON_INSTRUCTION = (
    "\n\nYou MUST respond with ONLY a valid JSON object matching the given schema. "
    "Do NOT include markdown fences, explanations, or any text outside the JSON."
)

_MAX_VALIDATION_RETRIES = 2


def _validate_against_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    """Lightweight JSON Schema validation — checks required fields and types."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"Expected object, got {type(data).__name__}"]

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field_name in required:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    _JSON_TYPE_MAP = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for field_name, field_schema in properties.items():
        if field_name not in data:
            continue
        expected_type = field_schema.get("type")
        if expected_type and expected_type in _JSON_TYPE_MAP:
            if not isinstance(data[field_name], _JSON_TYPE_MAP[expected_type]):
                errors.append(
                    f"Field '{field_name}': expected {expected_type}, "
                    f"got {type(data[field_name]).__name__}"
                )
    return errors


def _extract_json(text: str) -> Any:
    """Extract JSON from text that may contain markdown fences or extra prose."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()
    return json.loads(text)


def run_llm_task(
    *,
    prompt: str,
    input_text: str,
    llm: BaseChatModel,
    schema: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Execute a one-shot LLM sub-task and return structured output.

    Returns ``{"success": True, "output": ...}`` on success or
    ``{"success": False, "error": "...", "raw": "..."}`` on failure.
    """
    system_content = prompt
    if schema:
        system_content += _JSON_INSTRUCTION
        system_content += f"\n\nRequired JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=input_text),
    ]

    invoke_kwargs: dict[str, Any] = {}
    if temperature is not None:
        invoke_kwargs["temperature"] = temperature

    for attempt in range(_MAX_VALIDATION_RETRIES + 1):
        try:
            response = llm.invoke(messages, **invoke_kwargs)
            raw = response.content if hasattr(response, "content") else str(response)

            if not schema:
                return {"success": True, "output": raw}

            parsed = _extract_json(raw)
            validation_errors = _validate_against_schema(parsed, schema)
            if not validation_errors:
                return {"success": True, "output": parsed}

            if attempt < _MAX_VALIDATION_RETRIES:
                messages.append(response)
                messages.append(
                    HumanMessage(
                        content=f"Validation errors: {validation_errors}. "
                        "Please fix and return ONLY valid JSON."
                    )
                )
                continue

            return {
                "success": False,
                "error": f"Schema validation failed after {_MAX_VALIDATION_RETRIES + 1} attempts: {validation_errors}",
                "raw": raw,
                "output": parsed,
            }

        except json.JSONDecodeError as exc:
            if attempt < _MAX_VALIDATION_RETRIES:
                messages.append(HumanMessage(content=f"Invalid JSON: {exc}. Return ONLY valid JSON."))
                continue
            return {"success": False, "error": f"JSON parse error: {exc}", "raw": raw if "raw" in dir() else ""}

        except Exception as exc:
            logger.error("LLM task failed: %s", exc)
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": "Unexpected exit from retry loop"}


class LLMTaskTool(BaseTool):
    """Agent-callable tool for one-shot LLM sub-tasks with optional schema validation."""

    name: str = "llm_task"
    description: str = (
        "Execute a focused LLM sub-task that returns structured output. "
        "Useful for classification, extraction, summarisation, translation, "
        "or any task that benefits from a dedicated system prompt and optional "
        "JSON Schema enforcement.  The sub-call has NO access to tools or memory."
    )
    args_schema: type[BaseModel] = LLMTaskInput
    llm: Any = None

    def _run(
        self,
        prompt: str,
        input_text: str,
        output_schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        if self.llm is None:
            return json.dumps({"success": False, "error": "LLM not configured"}, ensure_ascii=False)

        result = run_llm_task(
            prompt=prompt,
            input_text=input_text,
            llm=self.llm,
            schema=output_schema,
            temperature=temperature,
        )
        return json.dumps(result, ensure_ascii=False, default=str)
