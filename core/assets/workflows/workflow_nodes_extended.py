"""Extended workflow node implementations inspired by Dify.

Nodes: http_request, question_classifier, variable_assigner, list_operator,
       parameter_extractor, iteration.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def run_http_request(config: dict[str, Any], resolve_var: Any = None) -> dict[str, Any]:
    """HTTP request node — fetch data from external APIs.

    Config:
        url: str
        method: GET | POST | PUT | PATCH | DELETE | HEAD
        headers: dict[str, str]
        params: dict[str, str]       (query params)
        body: dict | str             (for POST/PUT/PATCH)
        body_type: json | form | raw
        timeout: int (seconds, max 120)
        response_type: json | text
    """
    url = config.get("url", "")
    if not url:
        raise ValueError("http_request node requires a 'url'")

    method = config.get("method", "GET").upper()
    headers = dict(config.get("headers", {}))
    params = config.get("params", {})
    body_raw = config.get("body")
    body_type = config.get("body_type", "json")
    timeout = min(int(config.get("timeout", 30)), 120)
    response_type = config.get("response_type", "json")

    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urlencode(params)

    data = None
    if method in ("POST", "PUT", "PATCH") and body_raw is not None:
        if body_type == "json":
            data = json.dumps(body_raw, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif body_type == "form":
            data = urlencode(body_raw).encode("utf-8") if isinstance(body_raw, dict) else str(body_raw).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            data = str(body_raw).encode("utf-8")

    headers.setdefault("User-Agent", "PyBot-Workflow/1.0")
    req = Request(url, data=data, headers=headers, method=method)

    start = time.time()
    try:
        with urlopen(req, timeout=timeout) as resp:
            body_bytes = resp.read(1024 * 1024)
            status_code = resp.status
            resp_headers = dict(resp.getheaders())
    except HTTPError as exc:
        body_bytes = exc.read(1024 * 512)
        status_code = exc.code
        resp_headers = dict(exc.headers)
    except URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc.reason}") from exc

    elapsed = round(time.time() - start, 3)
    body_text = body_bytes.decode("utf-8", errors="replace")

    parsed_body: Any = body_text
    if response_type == "json":
        try:
            parsed_body = json.loads(body_text)
        except json.JSONDecodeError:
            parsed_body = body_text

    return {
        "status_code": status_code,
        "headers": resp_headers,
        "body": parsed_body,
        "elapsed_time": elapsed,
        "success": 200 <= status_code < 300,
    }


def run_question_classifier(
    config: dict[str, Any],
    agent_callback: Any,
) -> dict[str, Any]:
    """LLM-based question classifier — route to different branches.

    Config:
        query: str                          (the input to classify)
        classes: list[dict]                 (each with 'id', 'name', 'description')
        instruction: str (optional)         (additional classification guidance)
    """
    query = config.get("query", "")
    classes = config.get("classes", [])
    instruction = config.get("instruction", "")

    if not classes:
        raise ValueError("question_classifier requires at least one class")
    if not agent_callback:
        raise RuntimeError("question_classifier requires an LLM callback")

    class_descriptions = []
    for cls in classes:
        cls_id = cls.get("id", cls.get("name", ""))
        cls_name = cls.get("name", cls_id)
        cls_desc = cls.get("description", "")
        class_descriptions.append(f'- ID: "{cls_id}", Name: "{cls_name}", Description: "{cls_desc}"')

    prompt = (
        "Classify the following input into exactly one of the categories below. "
        "Respond with ONLY the category ID, nothing else.\n\n"
        "Categories:\n" + "\n".join(class_descriptions) + "\n\n"
    )
    if instruction:
        prompt += f"Additional instruction: {instruction}\n\n"
    prompt += f'Input: """{query}"""\n\nCategory ID:'

    result = str(agent_callback(prompt)).strip().strip('"').strip("'")

    valid_ids = {cls.get("id", cls.get("name", "")) for cls in classes}
    if result not in valid_ids:
        for cls in classes:
            if result.lower() in (cls.get("id", "").lower(), cls.get("name", "").lower()):
                result = cls.get("id", cls.get("name", ""))
                break
        else:
            result = classes[0].get("id", classes[0].get("name", ""))

    return {
        "class_id": result,
        "query": query,
        "_branch": result,
    }


def run_variable_assigner(
    config: dict[str, Any],
    workflow_variables: dict[str, Any],
    resolve_var: Any,
) -> dict[str, Any]:
    """Set or update workflow variables.

    Config:
        assignments: list[dict]
            Each: { variable: str, value: any, operation: set|append|increment }
    """
    assignments = config.get("assignments", [])
    if not assignments and "variable" in config:
        assignments = [{"variable": config["variable"], "value": config.get("value")}]

    results = {}
    for assign in assignments:
        var_name = assign.get("variable", "")
        value = assign.get("value")
        operation = assign.get("operation", "set")

        if isinstance(value, str):
            value = resolve_var(value)

        if operation == "append":
            existing = workflow_variables.get(var_name, [])
            if isinstance(existing, list):
                existing.append(value)
                value = existing
            else:
                value = [existing, value] if existing else [value]
        elif operation == "increment":
            existing = workflow_variables.get(var_name, 0)
            try:
                value = float(existing) + float(value)
            except (TypeError, ValueError):
                value = str(existing) + str(value)

        workflow_variables[var_name] = value
        results[var_name] = value

    return results


def run_list_operator(
    config: dict[str, Any],
    resolve_var: Any,
    evaluate_condition: Any = None,
) -> Any:
    """List manipulation operations.

    Config:
        operation: sort | reverse | unique | flatten | slice | head | tail |
                   length | contains | index_of | join | zip | group_by |
                   filter | map
        data: list (or variable reference)
        key: str (for sort, group_by)
        count: int (for head, tail)
        start/end: int (for slice)
        separator: str (for join)
        condition: str (for filter)
        expression: str (for map)
    """
    data = config.get("data")
    if isinstance(data, str):
        data = resolve_var(data)
    if data is None:
        data = []
    if not isinstance(data, list):
        data = [data]

    operation = config.get("operation", "length")

    if operation == "sort":
        key = config.get("key")
        reverse = config.get("reverse", False)
        if key and data and isinstance(data[0], dict):
            return sorted(data, key=lambda x: x.get(key, ""), reverse=reverse)
        return sorted(data, reverse=reverse)

    if operation == "reverse":
        return list(reversed(data))

    if operation == "unique":
        seen: list[Any] = []
        for item in data:
            hashable = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else item
            if hashable not in seen:
                seen.append(hashable)
                # keep the original item, not the hashable version
        unique: list[Any] = []
        seen_set: list[Any] = []
        for item in data:
            h = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else item
            if h not in seen_set:
                seen_set.append(h)
                unique.append(item)
        return unique

    if operation == "flatten":
        flat: list[Any] = []
        for item in data:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        return flat

    if operation == "slice":
        start = config.get("start", 0)
        end = config.get("end")
        return data[start:end]

    if operation == "head":
        return data[: config.get("count", 1)]

    if operation == "tail":
        return data[-config.get("count", 1) :]

    if operation == "length":
        return len(data)

    if operation == "contains":
        target = config.get("value")
        if isinstance(target, str):
            target = resolve_var(target)
        return target in data

    if operation == "index_of":
        target = config.get("value")
        if isinstance(target, str):
            target = resolve_var(target)
        try:
            return data.index(target)
        except ValueError:
            return -1

    if operation == "join":
        sep = config.get("separator", ", ")
        return sep.join(str(item) for item in data)

    if operation == "zip":
        other = config.get("other", [])
        if isinstance(other, str):
            other = resolve_var(other)
        if not isinstance(other, list):
            other = [other]
        return [list(pair) for pair in zip(data, other, strict=False)]

    if operation == "group_by":
        key = config.get("key", "")
        groups: dict[str, list[Any]] = {}
        for item in data:
            group_val = str(item.get(key, "_none_")) if isinstance(item, dict) else str(item)
            groups.setdefault(group_val, []).append(item)
        return groups

    return data


def run_parameter_extractor(
    config: dict[str, Any],
    agent_callback: Any,
) -> dict[str, Any]:
    """Extract structured parameters from text using LLM.

    Config:
        text: str
        parameters: list[dict]   (each with 'name', 'type', 'description', 'required')
        instruction: str (optional)
    """
    text = config.get("text", "")
    parameters = config.get("parameters", [])
    instruction = config.get("instruction", "")

    if not parameters:
        raise ValueError("parameter_extractor requires at least one parameter definition")
    if not agent_callback:
        raise RuntimeError("parameter_extractor requires an LLM callback")

    param_descriptions = []
    for param in parameters:
        required = "required" if param.get("required", False) else "optional"
        param_descriptions.append(
            f'  "{param["name"]}": {param.get("type", "string")} ({required}) — {param.get("description", "")}'
        )

    prompt = (
        "Extract the following parameters from the text below. "
        "Return a valid JSON object with the parameter values. "
        "Use null for missing optional parameters.\n\n"
        "Parameters:\n" + "\n".join(param_descriptions) + "\n\n"
    )
    if instruction:
        prompt += f"Additional instruction: {instruction}\n\n"
    prompt += f'Text: """{text}"""\n\nJSON:'

    result_text = str(agent_callback(prompt)).strip()
    json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
    if json_match:
        result_text = json_match.group(0)
    try:
        extracted = json.loads(result_text)
    except json.JSONDecodeError:
        extracted = {"raw_response": result_text}

    return extracted


def run_iteration(
    config: dict[str, Any],
    workflow_variables: dict[str, Any],
    resolve_var: Any,
    resolve_config: Any,
    exec_body: Any,
) -> dict[str, Any]:
    """Enhanced iteration node (Dify-style).

    Differs from foreach: supports break condition, parallel mode,
    and error policy at iteration level.

    Config:
        items: list or variable ref
        max_iterations: int (default 100)
        body: dict (action config)
        break_condition: str (optional, evaluated each iteration)
        parallel: bool (default False)
        error_policy: fail_fast | continue | skip (default fail_fast)
        output_type: array | last | first (default array)
    """
    items_ref = config.get("items", "[]")
    items = resolve_var(items_ref) if isinstance(items_ref, str) else items_ref
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = [items]
    if not isinstance(items, list):
        items = [items]

    max_iterations = config.get("max_iterations", 100)
    body = config.get("body", {})
    break_condition = config.get("break_condition")
    error_policy = config.get("error_policy", "fail_fast")
    output_type = config.get("output_type", "array")

    results: list[Any] = []
    errors: list[dict[str, Any]] = []

    for index, item in enumerate(items[:max_iterations]):
        workflow_variables["_iteration_item"] = item
        workflow_variables["_iteration_index"] = index
        workflow_variables["_iteration_length"] = len(items[:max_iterations])

        if break_condition:
            from core.workflow_graph_runtime import WorkflowGraphRuntime

            graph_rt = WorkflowGraphRuntime()

            class _FakeWf:
                variables = workflow_variables

            if graph_rt.evaluate_condition(break_condition, _FakeWf()):  # type: ignore[arg-type]
                break

        try:
            resolved_body = resolve_config(body)
            result = exec_body(resolved_body, item)
            results.append({"index": index, "item": item, "result": result})
        except Exception as exc:
            err = {"index": index, "item": item, "error": str(exc)}
            errors.append(err)
            if error_policy == "fail_fast":
                raise
            if error_policy == "skip":
                results.append(err)

    for key in ("_iteration_item", "_iteration_index", "_iteration_length"):
        workflow_variables.pop(key, None)

    output_data = results
    if output_type == "last" and results:
        output_data = results[-1]
    elif output_type == "first" and results:
        output_data = results[0]

    return {
        "items_count": len(items[:max_iterations]),
        "completed": len(results),
        "errors": len(errors),
        "results": output_data,
    }


# ── Database Query Node ──────────────────────────────────────────


def run_database_query(config: dict[str, Any], resolve_var: Any = None) -> dict[str, Any]:
    """Execute a SQL query via SQLAlchemy or sqlite3.

    Config:
        provider: sqlite | mysql | postgresql  (default sqlite)
        connection_string: str   (SQLAlchemy URL or file path for sqlite)
        query: str               (SQL statement)
        params: list | dict      (bind parameters)
        max_rows: int            (default 1000)
        readonly: bool           (default True — blocks INSERT/UPDATE/DELETE)
    """
    import sqlite3 as _sqlite3

    provider = config.get("provider", "sqlite")
    conn_str = config.get("connection_string", "")
    query = config.get("query", "")
    params = config.get("params", [])
    max_rows = min(int(config.get("max_rows", 1000)), 5000)
    readonly = config.get("readonly", True)

    if not query.strip():
        raise ValueError("database_query node requires a non-empty 'query'")

    if readonly:
        _q = query.strip().upper()
        for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"):
            if _q.startswith(kw):
                raise ValueError(f"readonly mode blocks {kw} statements")

    if provider == "sqlite":
        db_path = conn_str or ":memory:"
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row
        try:
            cursor = conn.execute(query, params if params else [])
            if cursor.description:
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchmany(max_rows)]
                return {"columns": columns, "rows": rows, "row_count": len(rows)}
            conn.commit()
            return {"affected_rows": cursor.rowcount}
        finally:
            conn.close()

    try:
        from sqlalchemy import create_engine
        from sqlalchemy import text as sa_text
    except ImportError as exc:
        raise RuntimeError("SQLAlchemy is required for non-sqlite database_query nodes") from exc

    engine = create_engine(conn_str, pool_pre_ping=True)
    with engine.connect() as conn:
        result = conn.execute(sa_text(query), params if isinstance(params, dict) else {})
        if result.returns_rows:
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchmany(max_rows)]
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        conn.commit()
        return {"affected_rows": result.rowcount}


# ── File Read / Write Nodes ──────────────────────────────────────


def _safe_file_path(path: str, workspace_root: str) -> str:
    """Ensure *path* is under *workspace_root*."""
    import os

    abs_path = os.path.realpath(os.path.join(workspace_root, path))
    abs_root = os.path.realpath(workspace_root)
    if not abs_path.startswith(abs_root):
        raise ValueError(f"路径越界: {path} 不在 {workspace_root} 内")
    return abs_path


def run_file_read(config: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    """Read a file from the workspace.

    Config:
        path: str          (relative to workspace root)
        encoding: str      (default utf-8)
        max_size: int      (bytes, default 1MB)
    """
    import os

    path = config.get("path", "")
    if not path:
        raise ValueError("file_read node requires a 'path'")
    encoding = config.get("encoding", "utf-8")
    max_size = min(int(config.get("max_size", 1_048_576)), 10_485_760)

    abs_path = _safe_file_path(path, workspace_root)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {path}")
    size = os.path.getsize(abs_path)
    if size > max_size:
        raise ValueError(f"文件过大: {size} bytes > max_size {max_size}")

    with open(abs_path, encoding=encoding) as f:
        content = f.read()
    return {"path": path, "content": content, "size": size}


def run_file_write(config: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    """Write content to a file in the workspace.

    Config:
        path: str          (relative to workspace root)
        content: str       (file content)
        encoding: str      (default utf-8)
        mode: str          (write | append, default write)
    """
    import os

    path = config.get("path", "")
    if not path:
        raise ValueError("file_write node requires a 'path'")
    content = config.get("content", "")
    encoding = config.get("encoding", "utf-8")
    mode = config.get("mode", "write")

    abs_path = _safe_file_path(path, workspace_root)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    open_mode = "a" if mode == "append" else "w"
    with open(abs_path, open_mode, encoding=encoding) as f:
        f.write(content)

    return {"path": path, "size": os.path.getsize(abs_path), "mode": mode}
