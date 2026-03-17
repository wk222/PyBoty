"""Streaming adapter for PyBot chat output."""

from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable, Iterator


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


def stream_chat_events(
    *,
    message: str,
    chat_callable: Callable[[str], str],
    list_agents_callable: Callable[[], dict],
    list_tools_callable: Callable[[], dict],
) -> Iterator[dict]:
    """Yield structured events while a chat call runs in the background."""
    step_queue: queue.Queue = queue.Queue()
    final_result = [None]
    error_result = [None]

    def run_agent() -> None:
        old_stdout = sys.stdout
        sys.stdout = _PrintCapture(old_stdout, step_queue)
        try:
            final_result[0] = chat_callable(message)
        except Exception as exc:  # pragma: no cover - defensive stream wrapper
            error_result[0] = str(exc)
        finally:
            sys.stdout = old_stdout
            step_queue.put(None)

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    while True:
        try:
            event = step_queue.get(timeout=0.5)
            if event is None:
                break
            yield event
        except queue.Empty:
            if not thread.is_alive():
                break
            yield {"type": "schedule"}

    if error_result[0]:
        yield {"type": "error", "content": f"❌ 错误: {error_result[0]}"}
    else:
        yield {
            "type": "done",
            "content": final_result[0] or "（无回复）",
            "agents": list(list_agents_callable().keys()),
            "tools": list(list_tools_callable().keys()),
        }
