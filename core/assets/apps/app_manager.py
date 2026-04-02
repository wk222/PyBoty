"""Sub-application manager for PyBot-created web apps."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.systems.runtime import ProjectPaths, safe_resolve
from core.tool_arg_repair import _repair_js_regex as sanitize_js_content

from .app_templates import APP_TEMPLATES

APP_METADATA_FILE = "app.json"
APP_ENTRY_FILE = "index.html"
APP_API_FILE = "api.py"
APP_STATIC_DIR = "static"

import time
_HELPERS_VERSION = str(int(time.time()))

APP_HELPERS_JS = """// PyBot App Helpers — DO NOT overwrite this file
// These helpers are always available in app.js

const _BASE = window.location.origin;
const _APP_NAME = (() => {
    const m = window.location.pathname.match(/\\/apps\\/([^/]+)/);
    return m ? m[1] : '';
})();

async function apiCall(endpoint, options = {}) {
    const isAppAction = !endpoint.startsWith('/') && !endpoint.startsWith('http');
    const url = isAppAction
        ? _BASE + '/api/apps/' + _APP_NAME + '/api'
        : _BASE + endpoint;

    let apiKey = localStorage.getItem('pybot_api_key') || 'dev-key';
    
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey
    };

    const fetchOpts = isAppAction
        ? {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ action: endpoint, payload: options }),
        }
        : {
            ...options,
            headers: { ...headers, ...(options.headers || {}) },
        };

    let resp = await fetch(url, fetchOpts);
    
    if (resp.status === 401) {
        const newKey = prompt("API Key Required (401 Unauthorized):", apiKey);
        if (newKey && newKey !== apiKey) {
            localStorage.setItem('pybot_api_key', newKey);
            fetchOpts.headers['Authorization'] = 'Bearer ' + newKey;
            resp = await fetch(url, fetchOpts);
        }
    }
    
    const data = await resp.json();
    if (isAppAction && data && data.success && data.result !== undefined) {
        return data.result;
    }
    return data;
}

async function dbQuery(sql) {
    return apiCall('/api/apps/~db/query', {
        method: 'POST',
        body: JSON.stringify({ sql })
    });
}

async function dbWrite(sql, params) {
    return apiCall('/api/apps/~db/write', {
        method: 'POST',
        body: JSON.stringify({ sql, params: params || [] })
    });
}

// --- Agent-Driven Helpers ---

let _agentThreadId = null;

async function agentEnsureThread() {
    if (_agentThreadId) return _agentThreadId;
    const data = await apiCall('/api/conversations', { method: 'POST', body: '{}' });
    _agentThreadId = data.id || data.thread_id;
    return _agentThreadId;
}

async function agentChat(message, onChunk) {
    const threadId = await agentEnsureThread();
    let apiKey = localStorage.getItem('pybot_api_key') || 'dev-key';
    
    let fetchOpts = {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + apiKey
        },
        body: JSON.stringify({ thread_id: threadId, message })
    };
    
    let resp = await fetch(_BASE + '/api/chat/stream', fetchOpts);
    
    if (resp.status === 401) {
        const newKey = prompt("API Key Required (401 Unauthorized):", apiKey);
        if (newKey && newKey !== apiKey) {
            localStorage.setItem('pybot_api_key', newKey);
            fetchOpts.headers['Authorization'] = 'Bearer ' + newKey;
            resp = await fetch(_BASE + '/api/chat/stream', fetchOpts);
        }
    }
    
    if (!onChunk) return resp.json();
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let contentFull = '';
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ')) continue;
            try {
                const evt = JSON.parse(trimmed.slice(6));
                if (evt.type === 'done' || evt.type === 'error') {
                    contentFull = evt.content || '';
                    onChunk(evt.content || '', contentFull);
                }
            } catch (_) {}
        }
    }
    return contentFull;
}

async function agentRunWorkflow(workflowName, inputVars = {}) {
    return apiCall('/api/workflows/trigger', {
        method: 'POST',
        body: JSON.stringify({ name: workflowName, input_vars: inputVars })
    });
}

async function agentSearch(query) {
    return apiCall('/api/search?q=' + encodeURIComponent(query));
}

async function agentKnowledgeQuery(query, collection = 'default', topK = 5) {
    return apiCall('/api/knowledge/search', {
        method: 'POST',
        body: JSON.stringify({ query, collection, top_k: topK })
    });
}

async function agentListTools() {
    return apiCall('/api/tools');
}

async function agentCallTool(toolName, args = {}) {
    let raw = await apiCall('/api/apps/' + _APP_NAME + '/tool/' + encodeURIComponent(toolName) + '/run', {
        method: 'POST',
        body: JSON.stringify(args)
    });
    if (typeof raw === 'string') {
        try { raw = JSON.parse(raw); } catch (_) { /* keep string */ }
    }
    if (raw && typeof raw === 'object' && raw.success === true && raw.result !== undefined) {
        raw = raw.result;
    }
    if (Array.isArray(raw)) {
        Object.defineProperty(raw, 'list', { value: raw, enumerable: false });
        Object.defineProperty(raw, 'data', { value: raw, enumerable: false });
        Object.defineProperty(raw, 'result', { value: raw, enumerable: false });
    }
    return raw;
}
"""


def build_default_html(name: str, display_name: str, description: str) -> str:
    """Return the default app shell for a newly created app."""
    title = display_name or name
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="static/style.css">
</head>
<body>
    <div id="app">
        <h1>{title}</h1>
        <p>{description}</p>
    </div>
    <script src="static/pybot-helpers.js?v={_HELPERS_VERSION}"></script>
    <script src="static/app.js"></script>
</body>
</html>"""


DEFAULT_CSS = """* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f7fa;
    color: #333;
}
#app { max-width: 1200px; margin: 0 auto; padding: 20px; }
h1 { margin-bottom: 10px; color: #1a1a2e; }
"""

DEFAULT_JS = """// App JavaScript — custom code goes here
console.log('App loaded');
"""


def _coerce_string_list(value: Any) -> list[str]:
    """Normalize list-like metadata fields into a compact string list."""
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


def _coerce_mapping_list(value: Any) -> list[dict[str, Any]]:
    """Normalize contract-like metadata into JSON-safe mapping objects."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, str):
        text = value.strip()
        return [{"name": text}] if text else []
    if isinstance(value, (list, tuple, set)):
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                result.append(dict(item))
                continue
            text = str(item).strip()
            if text:
                result.append({"name": text})
        return result
    text = str(value).strip()
    return [{"name": text}] if text else []


class AppMode:
    STATIC = "static"
    CHAT = "chat"
    WORKFLOW = "workflow"
    ASSISTANT = "assistant"
    RAG = "rag"


@dataclass(slots=True)
class AppDefinition:
    """Persisted metadata for a generated sub-application."""

    name: str
    display_name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = "agent"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    enabled: bool = True
    entry_point: str = APP_ENTRY_FILE
    api_enabled: bool = False
    tags: list[str] = field(default_factory=list)

    mode: str = "static"
    agent_binding: str = ""
    workflow_binding: str = ""
    knowledge_collections: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    shared_datastores: list[str] = field(default_factory=list)
    shared_schemas: list[dict[str, Any]] = field(default_factory=list)
    data_contracts: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_override: str = ""
    
    # App Isolation & Operations enhancements
    isolated_knowledge: bool = False
    isolated_storage: bool = False
    require_auth: bool = False
    api_keys: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> AppDefinition:
        """Build an app definition from persisted metadata."""
        return cls(
            name=name,
            display_name=str(data.get("display_name", name)),
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0.0")),
            author=str(data.get("author", "agent")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            enabled=bool(data.get("enabled", True)),
            entry_point=str(data.get("entry_point", APP_ENTRY_FILE)),
            api_enabled=bool(data.get("api_enabled", False)),
            tags=_coerce_string_list(data.get("tags", [])),
            mode=str(data.get("mode", "static")),
            agent_binding=str(data.get("agent_binding", "")),
            workflow_binding=str(data.get("workflow_binding", "")),
            knowledge_collections=_coerce_string_list(data.get("knowledge_collections", [])),
            allowed_tools=_coerce_string_list(data.get("allowed_tools", [])),
            shared_datastores=_coerce_string_list(data.get("shared_datastores", [])),
            shared_schemas=_coerce_mapping_list(data.get("shared_schemas", [])),
            data_contracts=_coerce_mapping_list(data.get("data_contracts", [])),
            system_prompt_override=str(data.get("system_prompt_override", "")),
            isolated_knowledge=bool(data.get("isolated_knowledge", False)),
            isolated_storage=bool(data.get("isolated_storage", False)),
            require_auth=bool(data.get("require_auth", False)),
            api_keys=list(data.get("api_keys", [])),
            exports=_coerce_string_list(data.get("exports", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the app definition to JSON-ready data."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "enabled": self.enabled,
            "entry_point": self.entry_point,
            "api_enabled": self.api_enabled,
            "tags": self.tags,
            "mode": self.mode,
            "agent_binding": self.agent_binding,
            "workflow_binding": self.workflow_binding,
            "knowledge_collections": self.knowledge_collections,
            "allowed_tools": self.allowed_tools,
            "shared_datastores": self.shared_datastores,
            "shared_schemas": self.shared_schemas,
            "data_contracts": self.data_contracts,
            "system_prompt_override": self.system_prompt_override,
            "isolated_knowledge": self.isolated_knowledge,
            "isolated_storage": self.isolated_storage,
            "require_auth": self.require_auth,
            "api_keys": self.api_keys,
            "exports": self.exports,
        }

    @property
    def is_agent_driven(self) -> bool:
        return self.mode in (AppMode.CHAT, AppMode.ASSISTANT, AppMode.RAG, AppMode.WORKFLOW)


class AppManager:
    """Manage the lifecycle, files, and API execution for PyBot sub-apps."""

    def __init__(self, apps_dir: str = "workspace/apps", project_paths: ProjectPaths | None = None):
        self.project_paths = project_paths or ProjectPaths.from_root()
        self.apps_dir_path = Path(apps_dir).resolve()
        self.apps_dir = str(self.apps_dir_path)
        self.apps: dict[str, AppDefinition] = {}
        self.apps_dir_path.mkdir(parents=True, exist_ok=True)
        self.reload_apps()

    @staticmethod
    def _is_valid_app_name(name: str) -> bool:
        return bool(name) and name.replace("_", "").replace("-", "").isalnum()

    def _app_dir(self, name: str) -> Path:
        return self.apps_dir_path / name

    def _metadata_path(self, name: str) -> Path:
        return self._app_dir(name) / APP_METADATA_FILE

    def _static_dir(self, name: str) -> Path:
        return self._app_dir(name) / APP_STATIC_DIR

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"Expected object JSON in {path}")
        return data

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _persist_definition(self, definition: AppDefinition) -> None:
        self._write_json(self._metadata_path(definition.name), definition.to_dict())
        self.apps[definition.name] = definition

    def _update_definition(self, app_name: str, **changes: Any) -> AppDefinition:
        definition = self.apps[app_name]
        data = definition.to_dict()
        data.update(changes)
        updated = AppDefinition.from_dict(app_name, data)
        self._persist_definition(updated)
        return updated

    def discover_capabilities(self, query: str = "") -> list[dict[str, Any]]:
        """Search across all apps for exported capabilities."""
        results = []
        query_lower = query.lower()
        for app in self.apps.values():
            if not app.enabled:
                continue
            for export in app.exports:
                if query_lower in export.lower() or query_lower in app.name.lower() or query_lower in app.description.lower():
                    results.append({
                        "app_name": app.name,
                        "app_description": app.description,
                        "capability": export,
                        "requires_auth": app.require_auth,
                    })
        return results

    _IMPORT_TO_PACKAGE: dict[str, str] = {
        "requests": "requests",
        "httpx": "httpx",
        "aiohttp": "aiohttp",
        "bs4": "beautifulsoup4",
        "lxml": "lxml",
        "yaml": "pyyaml",
        "PIL": "pillow",
        "pandas": "pandas",
        "numpy": "numpy",
        "pydantic": "pydantic",
    }

    @staticmethod
    def _detect_dependencies(api_code: str) -> list[str]:
        """Scan api.py imports and return pip package names for third-party modules."""
        import re as _re

        imports: set[str] = set()
        for m in _re.finditer(r"^\s*(?:import|from)\s+(\w+)", api_code, _re.MULTILINE):
            mod = m.group(1)
            if mod in AppManager._IMPORT_TO_PACKAGE:
                imports.add(AppManager._IMPORT_TO_PACKAGE[mod])
        return sorted(imports)

    def _runner_script(self, api_path: Path, db_path: Path) -> str:
        try:
            api_code = api_path.read_text(encoding="utf-8")
        except Exception:
            api_code = ""
        deps = self._detect_dependencies(api_code)
        deps_str = json.dumps(deps)

        return f"""# /// script
# requires-python = ">=3.10"
# dependencies = {deps_str}
# ///
import json, os, sys, traceback, sqlite3

DB_PATH = {json.dumps(str(db_path))}
os.environ["DB_PATH"] = DB_PATH

def db_query(sql, params=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(sql, params or [])
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def db_execute(sql, params=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(sql, params or [])
    conn.commit()
    conn.close()

try:
    with open(sys.argv[1], "r", encoding="utf-8") as file:
        request = json.load(file)

    action = request["action"]
    payload = request["payload"]
    result = None

    with open({json.dumps(str(api_path))}, "r", encoding="utf-8") as file:
        api_code = file.read()

    exec_globals = {{
        "__builtins__": __builtins__,
        "action": action,
        "payload": payload,
        "DB_PATH": DB_PATH,
        "result": None,
        "db_query": db_query,
        "db_execute": db_execute,
    }}
    exec(api_code, exec_globals)
    result = exec_globals.get("result")

    with open(sys.argv[2], "w", encoding="utf-8") as file:
        json.dump({{"result": result}}, file, ensure_ascii=False)
except Exception as exc:
    with open(sys.argv[2], "w", encoding="utf-8") as file:
        json.dump({{"result": {{"error": str(exc), "traceback": traceback.format_exc()}}}}, file)
"""

    def _runner_command(self, script_path: Path, input_path: Path, output_path: Path) -> list[str]:
        if shutil.which("uv"):
            return ["uv", "run", str(script_path), str(input_path), str(output_path)]
        return [sys.executable, str(script_path), str(input_path), str(output_path)]

    def reload_apps(self) -> None:
        """Reload persisted app metadata from disk."""
        self.apps = {}
        if not self.apps_dir_path.exists():
            return

        for entry in sorted(self.apps_dir_path.iterdir()):
            if not entry.is_dir():
                continue
            metadata_path = entry / APP_METADATA_FILE
            if not metadata_path.exists():
                continue
            try:
                self.apps[entry.name] = AppDefinition.from_dict(entry.name, self._read_json(metadata_path))
            except Exception as exc:
                print(f"[AppManager] Failed to load app {entry.name}: {exc}")

    def _discover_apps(self) -> None:
        """Backward-compatible alias for older call sites."""
        self.reload_apps()

    def list_apps(self) -> list[dict[str, Any]]:
        return [app.to_dict() for app in self.apps.values()]

    def get_app(self, name: str) -> AppDefinition | None:
        return self.apps.get(name)

    def get_app_dir(self, name: str) -> str:
        return str(self._app_dir(name))

    def create_app(
        self,
        name: str,
        display_name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        mode: str = "static",
        agent_binding: str = "",
        workflow_binding: str = "",
        knowledge_collections: list[str] | None = None,
        shared_datastores: list[str] | None = None,
        shared_schemas: list[dict[str, Any]] | None = None,
        data_contracts: list[dict[str, Any]] | None = None,
        system_prompt_override: str = "",
        isolated_knowledge: bool = False,
        isolated_storage: bool = False,
        require_auth: bool = False,
        api_keys: list[str] | None = None,
        exports: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self._is_valid_app_name(name):
            return {"success": False, "error": "App name must be alphanumeric (with _ or -)"}

        app_dir = self._app_dir(name)
        if app_dir.exists():
            return {"success": False, "error": f"App '{name}' already exists"}

        app_dir.mkdir(parents=True, exist_ok=True)
        self._static_dir(name).mkdir(parents=True, exist_ok=True)

        definition = AppDefinition(
            name=name,
            display_name=display_name or name,
            description=description,
            tags=_coerce_string_list(tags),
            mode=mode,
            agent_binding=agent_binding,
            workflow_binding=workflow_binding,
            knowledge_collections=_coerce_string_list(knowledge_collections),
            shared_datastores=_coerce_string_list(shared_datastores),
            shared_schemas=_coerce_mapping_list(shared_schemas),
            data_contracts=_coerce_mapping_list(data_contracts),
            system_prompt_override=system_prompt_override,
            isolated_knowledge=isolated_knowledge,
            isolated_storage=isolated_storage,
            require_auth=require_auth,
            api_keys=_coerce_string_list(api_keys),
            exports=_coerce_string_list(exports),
        )
        
        if isolated_storage:
            (app_dir / "data").mkdir(parents=True, exist_ok=True)
            
        self._persist_definition(definition)

        template = APP_TEMPLATES.get(mode)
        if template:
            html_builder = template["html_builder"]
            if mode == "workflow":
                html_content = html_builder(name, display_name or name, description, workflow_binding)
            else:
                html_content = html_builder(name, display_name or name, description)
            
            # Inject version to avoid cache
            html_content = html_content.replace('src="static/pybot-helpers.js"', f'src="static/pybot-helpers.js?v={_HELPERS_VERSION}"')
            
            css_content = template["css"]
            js_content = template["js"]
        else:
            html_content = build_default_html(name, display_name or name, description)
            css_content = DEFAULT_CSS
            js_content = DEFAULT_JS

        self._write_text(app_dir / APP_ENTRY_FILE, html_content)
        self._write_text(self._static_dir(name) / "style.css", css_content)
        self._write_text(self._static_dir(name) / "pybot-helpers.js", APP_HELPERS_JS)
        self._write_text(self._static_dir(name) / "app.js", js_content)

        return {"success": True, "app_name": name, "mode": mode, "path": str(app_dir)}

    def update_app_topology_metadata(
        self,
        name: str,
        *,
        shared_datastores: list[str] | None = None,
        shared_schemas: list[dict[str, Any]] | None = None,
        data_contracts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update persisted APP Brain contract metadata for an app."""
        if name not in self.apps:
            return {"success": False, "error": f"App '{name}' not found"}

        changes: dict[str, Any] = {"updated_at": time.time()}
        if shared_datastores is not None:
            changes["shared_datastores"] = _coerce_string_list(shared_datastores)
        if shared_schemas is not None:
            changes["shared_schemas"] = _coerce_mapping_list(shared_schemas)
        if data_contracts is not None:
            changes["data_contracts"] = _coerce_mapping_list(data_contracts)

        updated = self._update_definition(name, **changes)
        return {"success": True, "app": updated.to_dict()}

    def update_app_file(self, app_name: str, file_path: str, content: str) -> dict[str, Any]:
        if app_name not in self.apps:
            return {"success": False, "error": f"App '{app_name}' not found"}

        app_dir = self._app_dir(app_name)
        try:
            full_path = safe_resolve(app_dir, file_path)
        except PermissionError:
            return {"success": False, "error": "Path traversal not allowed"}

        relative_path = full_path.relative_to(app_dir).as_posix()
        if full_path.suffix == ".js":
            content = sanitize_js_content(content)
        try:
            if relative_path == APP_METADATA_FILE:
                metadata = json.loads(content)
                if not isinstance(metadata, dict):
                    return {"success": False, "error": "app.json must contain a JSON object"}
                metadata.setdefault("name", app_name)
                metadata["updated_at"] = time.time()
                definition = AppDefinition.from_dict(app_name, metadata)
                self._persist_definition(definition)
            else:
                self._write_text(full_path, content)
                changes: dict[str, Any] = {"updated_at": time.time()}
                if relative_path == APP_API_FILE:
                    changes["api_enabled"] = True
                self._update_definition(app_name, **changes)
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"Invalid JSON for app.json: {exc}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        return {"success": True, "file": file_path, "size": len(content)}

    def read_app_file(self, app_name: str, file_path: str) -> dict[str, Any]:
        if app_name not in self.apps:
            return {"success": False, "error": f"App '{app_name}' not found"}

        try:
            full_path = safe_resolve(self._app_dir(app_name), file_path)
        except PermissionError:
            return {"success": False, "error": "Path traversal not allowed"}

        if not full_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        return {"success": True, "file": file_path, "content": full_path.read_text(encoding="utf-8")}

    def list_app_files(self, app_name: str) -> dict[str, Any]:
        if app_name not in self.apps:
            return {"success": False, "error": f"App '{app_name}' not found"}

        app_dir = self._app_dir(app_name)
        files: list[dict[str, Any]] = []
        for full_path in sorted(path for path in app_dir.rglob("*") if path.is_file()):
            files.append(
                {
                    "path": full_path.relative_to(app_dir).as_posix(),
                    "size": full_path.stat().st_size,
                }
            )
        return {"success": True, "files": files}

    def delete_app(self, name: str) -> dict[str, Any]:
        if name not in self.apps:
            return {"success": False, "error": f"App '{name}' not found"}

        app_dir = self._app_dir(name)
        if app_dir.exists():
            shutil.rmtree(app_dir)
        del self.apps[name]
        return {"success": True, "deleted": name}

    def toggle_app(self, name: str, enabled: bool) -> dict[str, Any]:
        if name not in self.apps:
            return {"success": False, "error": f"App '{name}' not found"}

        self._update_definition(name, enabled=enabled, updated_at=time.time())
        return {"success": True, "app": name, "enabled": enabled}

    def execute_app_api(self, app_name: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if app_name not in self.apps:
            return {"success": False, "error": f"App '{app_name}' not found"}
        if not self.apps[app_name].api_enabled:
            return {"success": False, "error": f"App '{app_name}' has no API backend"}

        api_path = self._app_dir(app_name) / APP_API_FILE
        if not api_path.exists():
            return {"success": False, "error": "api.py not found"}

        run_id = uuid.uuid4().hex[:12]
        work_dir = self.project_paths.tools_workspace_dir / "apps" / app_name / run_id
        work_dir.mkdir(parents=True, exist_ok=True)

        input_path = work_dir / "input.json"
        output_path = work_dir / "output.json"
        script_path = work_dir / "runner.py"

        app_def = self.apps[app_name]
        
        # Determine DB path based on isolation setting
        if app_def.isolated_storage:
            db_dir = self._app_dir(app_name) / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "app.db"
        else:
            db_path = self.project_paths.workspace_data_dir / "agent.db"

        self._write_json(input_path, {"action": action, "payload": payload})
        self._write_text(
            script_path, self._runner_script(api_path.resolve(), db_path)
        )

        try:
            process = subprocess.run(
                self._runner_command(script_path, input_path, output_path),
                capture_output=True,
                text=False,
                timeout=30,
            )
            stderr_str = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "API execution timed out (30s)"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        if not output_path.exists():
            return {
                "success": False,
                "error": "API execution produced no output",
                "stderr": stderr_str[-500:],
            }

        try:
            out_data = self._read_json(output_path)
        except Exception as exc:
            return {"success": False, "error": f"Failed to parse API output: {exc}"}
        return {"success": True, "result": out_data.get("result")}
