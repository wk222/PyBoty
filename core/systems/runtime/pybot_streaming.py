"""Streaming adapter for PyBot chat output.

Provides real-time tool progress via:
1. stdout capture (legacy — DynamicToolMiddleware prints)
2. EventBus subscription (tool_call / tool_result events)
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

_TOOL_ICONS: dict[str, str] = {
    "web_search": "🔍",
    "web_fetch": "🌐",
    "vision": "👁️",
    "browser": "🖥️",
    "bash": "💻",
    "file_read": "📄",
    "file_write": "✏️",
}


class _PrintCapture:
    """Translate selected stdout lines into structured step events."""

    def __init__(self, original, event_queue: queue.Queue):
        self.original = original
        self.event_queue = event_queue

    def write(self, text: str) -> None:
        self.original.write(text)
        text = text.strip()
        if not text or len(text) <= 2:
            return

        icon = "📋"
        if "[DynamicToolMiddleware]" in text:
            text = text.replace("[DynamicToolMiddleware] ", "")
            if "✅" in text:
                icon = "✅"
            elif "🔧" in text:
                icon = "🔧"
            elif "🎯" in text:
                icon = "🎯"
            elif "📊" in text:
                icon = "📊"
            elif "📝" in text:
                icon = "📝"
            elif "❌" in text:
                icon = "❌"
            elif "⚠️" in text:
                icon = "⚠️"
        elif "[INFO]" in text:
            text = text.replace("[INFO] ", "")
            icon = "ℹ️"
        elif text.startswith("  -"):
            icon = "  "
        else:
            return

        self.event_queue.put({"type": "step", "content": text, "icon": icon})

    def flush(self) -> None:
        self.original.flush()


class _ToolEventRelay:
    """Subscribe to EventBus tool events and relay them as stream steps."""

    def __init__(self, event_queue: queue.Queue):
        self._queue = event_queue
        self._subscribed = False
        self._tool_start_times: dict[str, float] = {}

    def attach(self):
        try:
            from core.systems.runtime.event_bus import event_bus, EventType
            event_bus.subscribe(EventType.TOOL_CALL, self._on_tool_call, priority=90)
            event_bus.subscribe(EventType.TOOL_RESULT, self._on_tool_result, priority=90)
            self._subscribed = True
        except Exception:
            pass

    def detach(self):
        if not self._subscribed:
            return
        try:
            from core.systems.runtime.event_bus import event_bus, EventType
            event_bus.unsubscribe(EventType.TOOL_CALL, self._on_tool_call)
            event_bus.unsubscribe(EventType.TOOL_RESULT, self._on_tool_result)
        except Exception:
            pass
        self._subscribed = False

    def _on_tool_call(self, event):
        payload = event.payload or {}
        tool_name = payload.get("tool", payload.get("name", "unknown"))
        args_preview = str(payload.get("args", ""))[:120]
        icon = _TOOL_ICONS.get(tool_name, "🔧")
        self._tool_start_times[tool_name] = time.monotonic()
        self._queue.put({
            "type": "step",
            "content": f"Calling {tool_name}: {args_preview}",
            "icon": icon,
            "tool_progress": True,
        })

    def _on_tool_result(self, event):
        payload = event.payload or {}
        tool_name = payload.get("tool", payload.get("name", "unknown"))
        icon = _TOOL_ICONS.get(tool_name, "✅")
        elapsed = ""
        start = self._tool_start_times.pop(tool_name, None)
        if start:
            elapsed = f" ({time.monotonic() - start:.1f}s)"

        status = payload.get("status", "ok")
        if status == "error":
            icon = "❌"
            preview = str(payload.get("error", ""))[:100]
            self._queue.put({
                "type": "step",
                "content": f"{tool_name} failed{elapsed}: {preview}",
                "icon": icon,
                "tool_progress": True,
            })
        else:
            result_preview = str(payload.get("result", ""))[:100]
            self._queue.put({
                "type": "step",
                "content": f"{tool_name} done{elapsed}: {result_preview}",
                "icon": icon,
                "tool_progress": True,
            })


def stream_chat_events(
    *,
    message: str,
    chat_callable: Callable[[str], str],
    list_agents_callable: Callable[[], dict],
    list_tools_callable: Callable[[], dict],
) -> Iterator[dict]:
    """Yield structured events while a chat call runs in the background.

    Captures tool progress from both stdout and the EventBus.
    """
    step_queue: queue.Queue = queue.Queue()
    final_result = [None]
    error_result = [None]

    relay = _ToolEventRelay(step_queue)
    relay.attach()

    def run_agent() -> None:
        old_stdout = sys.stdout
        sys.stdout = _PrintCapture(old_stdout, step_queue)
        try:
            final_result[0] = chat_callable(message)
        except Exception as exc:
            error_result[0] = str(exc)
        finally:
            sys.stdout = old_stdout
            step_queue.put(None)

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    seen_tool_steps: set[str] = set()

    while True:
        try:
            event = step_queue.get(timeout=0.5)
            if event is None:
                break
            if event.get("tool_progress"):
                sig = event.get("content", "")
                if sig in seen_tool_steps:
                    continue
                seen_tool_steps.add(sig)
            yield event
        except queue.Empty:
            if not thread.is_alive():
                break
            yield {"type": "schedule"}

    relay.detach()

    if error_result[0]:
        yield {"type": "error", "content": f"❌ 错误: {error_result[0]}"}
    else:
        yield {
            "type": "done",
            "content": final_result[0] or "（无回复）",
            "agents": list(list_agents_callable().keys()),
            "tools": list(list_tools_callable().keys()),
        }
