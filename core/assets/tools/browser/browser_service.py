"""
BrowserService — Playwright wrapper with background thread and idle auto-release.

All Playwright calls run on a dedicated daemon thread. An idle timer
automatically shuts down the browser after a configurable period.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

_INTERACTIVE_TAGS = {
    "a", "button", "input", "textarea", "select", "option",
    "label", "details", "summary",
}
_SEMANTIC_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "td", "th", "caption", "blockquote", "pre", "code",
    "nav", "main", "article", "section", "form", "table",
    "img", "video",
}
_KEEP_TAGS = _INTERACTIVE_TAGS | _SEMANTIC_TAGS
_SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "meta", "link", "br", "hr"}

_SNAPSHOT_JS = """
() => {
    const KEEP = new Set(%s);
    const INTERACTIVE = new Set(%s);
    const SKIP = new Set(%s);
    const CLICKABLE_ROLES = new Set([
        "button","link","tab","menuitem","option","switch",
        "checkbox","radio","combobox","textbox","treeitem"
    ]);
    let refCounter = 0;
    const refMap = {};

    function visible(el) {
        if (!(el instanceof HTMLElement)) return true;
        const st = window.getComputedStyle(el);
        return st.display !== "none" && st.visibility !== "hidden" && parseFloat(st.opacity) !== 0;
    }

    function isInteractive(el) {
        const role = el.getAttribute("role");
        if (role && CLICKABLE_ROLES.has(role)) return true;
        if (el.hasAttribute("onclick") || el.hasAttribute("tabindex")) return true;
        if (el.getAttribute("contenteditable") === "true") return true;
        try {
            const st = window.getComputedStyle(el);
            if (st.cursor === "pointer") {
                const p = el.parentElement;
                if (!p || window.getComputedStyle(p).cursor !== "pointer") return true;
            }
        } catch(e) {}
        return false;
    }

    function walk(node) {
        if (node.nodeType === Node.TEXT_NODE) {
            const t = node.textContent.trim();
            return t || null;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return null;
        const tag = node.tagName.toLowerCase();
        if (SKIP.has(tag)) return null;
        if (!visible(node)) return null;

        const children = [];
        for (const ch of node.childNodes) {
            const r = walk(ch);
            if (r !== null) children.push(r);
        }

        const nativeInt = INTERACTIVE.has(tag);
        const implicitInt = !nativeInt && (node instanceof HTMLElement) && isInteractive(node);
        const keep = KEEP.has(tag) || implicitInt;

        if (!keep) {
            if (children.length === 0) return null;
            if (children.length === 1) return children[0];
            return children;
        }

        const obj = { tag };
        if (nativeInt || implicitInt) {
            refCounter++;
            obj.ref = refCounter;
            refMap[refCounter] = node;
        }
        if (tag === "a" && node.href) obj.href = node.getAttribute("href");
        if (tag === "img") { obj.alt = node.alt || ""; }
        if (["input","textarea","select"].includes(tag)) {
            obj.type = node.type || "text";
            if (node.placeholder) obj.placeholder = node.placeholder;
            if (node.value) obj.value = node.value;
        }
        if (children.length === 1 && typeof children[0] === "string") {
            obj.text = children[0];
        } else if (children.length > 0) {
            obj.children = children;
        }
        return obj;
    }

    const result = walk(document.body);
    window.__pybotRefMap = refMap;
    return { tree: result, refCount: refCounter };
}
""" % (
    str(list(_KEEP_TAGS)),
    str(list(_INTERACTIVE_TAGS)),
    str(list(_SKIP_TAGS)),
)


def _flatten_tree(node, indent=0, max_depth=8) -> List[str]:
    """Convert snapshot tree to compact text for LLM consumption."""
    if indent > max_depth * 2:
        return []
    if node is None:
        return []
    if isinstance(node, str):
        return [" " * indent + node]
    if isinstance(node, list):
        lines: list[str] = []
        for child in node:
            lines.extend(_flatten_tree(child, indent, max_depth))
        return lines
    if not isinstance(node, dict):
        return []

    tag = node.get("tag", "?")
    ref = node.get("ref")
    parts = [f"[{ref}] {tag}" if ref else tag]

    for attr in ("type", "href", "alt", "placeholder", "value"):
        val = node.get(attr)
        if val:
            s = str(val)[:60]
            parts.append(f'{attr}="{s}"')

    prefix = " " * indent
    header = prefix + " ".join(parts)
    text = node.get("text")
    if text:
        header += f": {text[:100]}"

    lines = [header]
    for child in node.get("children", []):
        lines.extend(_flatten_tree(child, indent + 2, max_depth))
    return lines


def _should_use_headless() -> bool:
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class BrowserService:
    """Manages a Playwright browser on a dedicated background thread."""

    IDLE_TIMEOUT = 300

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._headless: Optional[bool] = self._config.get("headless")
        self._thread: Optional[threading.Thread] = None
        self._task_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._alive = False
        self._ready = threading.Event()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        idle_cfg = self._config.get("idle_timeout")
        self._idle_timeout: float = float(idle_cfg) if idle_cfg is not None else self.IDLE_TIMEOUT
        self._idle_timer: Optional[threading.Timer] = None

    def _start_thread(self):
        with self._lock:
            if self._alive and self._thread and self._thread.is_alive():
                return
            self._task_queue = queue.Queue()
            self._alive = True
            self._ready = threading.Event()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="PyBotBrowser")
            self._thread.start()
            self._ready.wait(timeout=30)

    def _run_loop(self):
        logger.info("[Browser] Background thread started")
        try:
            self._launch()
        except Exception as e:
            logger.error("[Browser] Launch failed: %s", e)
            self._alive = False
            self._ready.set()
            return
        self._ready.set()

        while self._alive:
            try:
                task = self._task_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if task is None:
                break
            fn, args, kwargs, slot = task
            try:
                slot["value"] = fn(*args, **kwargs)
            except Exception as e:
                slot["error"] = e
            finally:
                slot["event"].set()

        self._shutdown()
        logger.info("[Browser] Background thread exited")

    def _launch(self):
        if self._headless is None:
            self._headless = _should_use_headless()
        launch_args = ["--disable-dev-shm-usage"]
        if self._headless:
            launch_args.append("--no-sandbox")
        vw = self._config.get("viewport_width", 1280)
        vh = self._config.get("viewport_height", 720)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless, args=launch_args)
        self._context = self._browser.new_context(
            viewport={"width": vw, "height": vh},
            user_agent="Mozilla/5.0 (compatible; PyBot/1.0) Chrome/131.0",
        )
        self._page = self._context.new_page()
        logger.info("[Browser] Ready (headless=%s)", self._headless)

    def _shutdown(self):
        self._cancel_idle()
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._playwright = None

    def _submit(self, fn: Callable, *args, **kwargs) -> Any:
        self._start_thread()
        if not self._alive:
            raise RuntimeError("Browser is not available")
        self._reset_idle()
        slot: Dict[str, Any] = {"event": threading.Event()}
        self._task_queue.put((fn, args, kwargs, slot))
        if not slot["event"].wait(timeout=120):
            raise TimeoutError("Browser operation timed out")
        if "error" in slot:
            raise slot["error"]
        return slot.get("value")

    def _reset_idle(self):
        self._cancel_idle()
        if self._idle_timeout > 0:
            self._idle_timer = threading.Timer(self._idle_timeout, self._on_idle)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _cancel_idle(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _on_idle(self):
        logger.info("[Browser] Idle %ds, auto-closing", self._idle_timeout)
        self.close()

    def close(self):
        self._cancel_idle()
        with self._lock:
            if not self._alive:
                return
            self._alive = False
            t = self._thread
        self._task_queue.put(None)
        if t and t.is_alive():
            t.join(timeout=10)
        with self._lock:
            self._thread = None

    @property
    def is_active(self) -> bool:
        return self._alive and self._page is not None

    def navigate(self, url: str, timeout: int = 30000) -> Dict[str, Any]:
        return self._submit(self._do_navigate, url, timeout)

    def _do_navigate(self, url: str, timeout: int) -> Dict[str, Any]:
        try:
            resp = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            status = resp.status if resp else None
        except Exception as e:
            return {"error": f"Navigation failed: {e}"}
        try:
            self._page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        self._page.wait_for_timeout(500)
        return {"url": self._page.url, "title": self._page.title(), "status": status}

    def snapshot(self, max_chars: int = 20000) -> str:
        return self._submit(self._do_snapshot, max_chars)

    def _do_snapshot(self, max_chars: int) -> str:
        try:
            result = self._page.evaluate(_SNAPSHOT_JS)
        except Exception as e:
            return f"[Snapshot error: {e}]"
        tree = result.get("tree")
        ref_count = result.get("refCount", 0)
        lines = _flatten_tree(tree)
        title = self._page.title()
        url = self._page.url
        header = f"Page: {title}  ({url})\nInteractive: {ref_count} elements\n---"
        body = "\n".join(lines)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n... [truncated]"
        return f"{header}\n{body}"

    def click(self, ref: Optional[int] = None, selector: Optional[str] = None) -> Dict[str, Any]:
        return self._submit(self._do_click, ref, selector)

    def _do_click(self, ref, selector) -> Dict[str, Any]:
        try:
            if ref is not None:
                result = self._page.evaluate(f"""
                    () => {{
                        const el = window.__pybotRefMap && window.__pybotRefMap[{ref}];
                        if (!el) return {{ error: "ref {ref} not found" }};
                        el.click();
                        return {{ clicked: true }};
                    }}
                """)
                if result.get("error"):
                    return result
                self._page.wait_for_timeout(500)
                return result
            elif selector:
                self._page.click(selector, timeout=5000)
                return {"clicked": True}
            return {"error": "Provide ref or selector"}
        except Exception as e:
            return {"error": str(e)}

    def fill(self, text: str, ref: Optional[int] = None, selector: Optional[str] = None) -> Dict[str, Any]:
        return self._submit(self._do_fill, text, ref, selector)

    def _do_fill(self, text, ref, selector) -> Dict[str, Any]:
        try:
            if ref is not None:
                self._page.evaluate(f"""
                    () => {{
                        const el = window.__pybotRefMap && window.__pybotRefMap[{ref}];
                        if (el) {{ el.focus(); el.value = ""; }}
                    }}
                """)
                self._page.keyboard.type(text)
                return {"filled": True}
            elif selector:
                self._page.fill(selector, text, timeout=5000)
                return {"filled": True}
            return {"error": "Provide ref or selector"}
        except Exception as e:
            return {"error": str(e)}

    def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        return self._submit(self._do_scroll, direction, amount)

    def _do_scroll(self, direction, amount) -> Dict[str, Any]:
        delta = {"down": (0, amount), "up": (0, -amount), "right": (amount, 0), "left": (-amount, 0)}
        dx, dy = delta.get(direction, (0, amount))
        self._page.mouse.wheel(dx, dy)
        self._page.wait_for_timeout(300)
        info = self._page.evaluate("() => ({scrollY: window.scrollY, scrollHeight: document.documentElement.scrollHeight})")
        return {"direction": direction, **info}

    def screenshot(self, full_page: bool = False, save_dir: str = "") -> str:
        return self._submit(self._do_screenshot, full_page, save_dir)

    def _do_screenshot(self, full_page, save_dir) -> str:
        d = save_dir or os.path.join(os.getcwd(), "tmp")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"screenshot_{uuid.uuid4().hex[:8]}.png")
        self._page.screenshot(path=path, full_page=full_page)
        return path

    def go_back(self) -> Dict[str, Any]:
        return self._submit(self._do_go_back)

    def _do_go_back(self) -> Dict[str, Any]:
        try:
            self._page.go_back(wait_until="domcontentloaded", timeout=10000)
            return {"url": self._page.url, "title": self._page.title()}
        except Exception as e:
            return {"error": str(e)}

    def evaluate(self, script: str) -> Dict[str, Any]:
        return self._submit(self._do_evaluate, script)

    def _do_evaluate(self, script) -> Dict[str, Any]:
        try:
            return {"result": self._page.evaluate(script)}
        except Exception as e:
            return {"error": str(e)}

    def press(self, key: str) -> Dict[str, Any]:
        return self._submit(self._do_press, key)

    def _do_press(self, key) -> Dict[str, Any]:
        try:
            self._page.keyboard.press(key)
            self._page.wait_for_timeout(300)
            return {"pressed": key}
        except Exception as e:
            return {"error": str(e)}
