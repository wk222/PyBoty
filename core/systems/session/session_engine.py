"""PyBotSessionEngine — unified work-session loop.

Single entry point that routes chat turns, background tasks, and gateway
continuations through one session spine.  All run kinds are recorded to the
session timeline so the full history is visible in one place.

Usage::

    engine = PyBotSessionEngine.from_pybot(bot)
    result = engine.run_chat("Summarise the last sprint")
    engine.switch_mode("app_matrix")
    status = engine.status()
"""

from __future__ import annotations

import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.systems.context.context_budget import BudgetAssessment, ContextBudgetManager
    from core.systems.session.session_runtime import SessionRecord, SessionRuntime


RUN_KIND_CHAT = "chat"
RUN_KIND_BACKGROUND = "background"
RUN_KIND_GATEWAY = "gateway"
RUN_KIND_WORKFLOW = "workflow"

VALID_MODES = {"assistant", "app_matrix", "admin"}


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_kind: str
    status: str
    response: str
    mode: str
    elapsed: float
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_kind": self.run_kind,
            "status": self.status,
            "response": self.response,
            "mode": self.mode,
            "elapsed": round(self.elapsed, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ModeTransition:
    previous_mode: str
    new_mode: str
    session_key: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_mode": self.previous_mode,
            "new_mode": self.new_mode,
            "session_key": self.session_key,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SessionStatus:
    session_key: str
    thread_id: str
    mode: str
    mode_history: list[str]
    status: str
    message_count: int
    last_message_at: float | None
    timeline_events: int
    sidechains: list[str]
    budget_level: str
    estimated_tokens: int
    context_limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "thread_id": self.thread_id,
            "mode": self.mode,
            "mode_history": list(self.mode_history),
            "status": self.status,
            "message_count": self.message_count,
            "last_message_at": self.last_message_at,
            "timeline_events": self.timeline_events,
            "sidechains": list(self.sidechains),
            "budget_level": self.budget_level,
            "estimated_tokens": self.estimated_tokens,
            "context_limit": self.context_limit,
        }


class PyBotSessionEngine:
    """Unified work-session loop above PyBot chat and background runs.

    Routes all run kinds through one session spine so the entire history —
    chat turns, background tasks, gateway continuations, workflow handoffs —
    lives in one timeline that can be inspected, compacted, and replayed.
    """

    def __init__(
        self,
        pybot: Any,
        session_runtime: SessionRuntime,
        *,
        session_key: str = "",
        budget_manager: ContextBudgetManager | None = None,
    ) -> None:
        self._pybot = pybot
        self._session_runtime = session_runtime
        self._session_key = session_key.strip() or _derive_session_key(pybot)
        self._budget_manager = budget_manager
        self._ensure_session()

    @classmethod
    def from_pybot(cls, pybot: Any, *, session_key: str = "") -> PyBotSessionEngine:
        """Construct an engine from a live PyBot instance."""
        from core.systems.context.context_budget import ContextBudgetManager

        sr = getattr(pybot, "session_runtime", None)
        if sr is None:
            raise ValueError("PyBot instance has no session_runtime attached")

        model_name = getattr(pybot, "model_name", "")
        budget = ContextBudgetManager(model_name=model_name)
        return cls(pybot, sr, session_key=session_key, budget_manager=budget)

    def _ensure_session(self) -> None:
        try:
            thread_id = getattr(self._pybot, "thread_id", self._session_key)
            mode = getattr(self._pybot, "root_mode", "assistant")
            self._session_runtime.ensure_session(
                session_key=self._session_key,
                thread_id=thread_id,
                primary_mode=mode,
            )
        except Exception:
            pass

    def _add_timeline_event(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            self._session_runtime.add_timeline_event(
                session_key=self._session_key,
                kind=kind,
                payload=payload,
            )
        except Exception:
            pass

    def run_chat(
        self,
        message: str,
        *,
        label: str = "",
    ) -> RunResult:
        """Run a chat turn through the session spine."""
        run_id = _new_run_id("chat")
        t0 = time.time()
        mode = self._current_mode()

        self._add_timeline_event(
            "run_start",
            {"run_id": run_id, "run_kind": RUN_KIND_CHAT, "mode": mode, "label": label},
        )

        try:
            chat_fn = getattr(self._pybot, "chat", None)
            if chat_fn is None:
                raise RuntimeError("PyBot instance has no .chat() method")

            response = chat_fn(message)
            elapsed = time.time() - t0

            result = RunResult(
                run_id=run_id,
                run_kind=RUN_KIND_CHAT,
                status="completed",
                response=str(response),
                mode=mode,
                elapsed=elapsed,
            )
            self._add_timeline_event(
                "run_end",
                {
                    "run_id": run_id,
                    "run_kind": RUN_KIND_CHAT,
                    "status": "completed",
                    "elapsed": round(elapsed, 3),
                },
            )
            if self._budget_manager:
                self._budget_manager.record_usage(0, 0, run_kind=RUN_KIND_CHAT, label=label)
            return result

        except Exception as exc:
            elapsed = time.time() - t0
            err_str = str(exc)
            self._add_timeline_event(
                "run_end",
                {"run_id": run_id, "run_kind": RUN_KIND_CHAT, "status": "error", "error": err_str},
            )
            return RunResult(
                run_id=run_id,
                run_kind=RUN_KIND_CHAT,
                status="error",
                response="",
                mode=mode,
                elapsed=elapsed,
                error=err_str,
            )

    def run_background(
        self,
        task: str,
        *,
        sidechain_purpose: str = "background_task",
        label: str = "",
    ) -> RunResult:
        """Run a background task in a named sidechain."""
        run_id = _new_run_id("bg")
        t0 = time.time()
        mode = self._current_mode()

        sidechain_id = f"sc-{run_id}"
        try:
            self._session_runtime.upsert_sidechain(
                self._session_key,
                purpose=sidechain_purpose,
                status="active",
                sidechain_id=sidechain_id,
            )
        except Exception:
            pass

        self._add_timeline_event(
            "run_start",
            {
                "run_id": run_id,
                "run_kind": RUN_KIND_BACKGROUND,
                "sidechain_id": sidechain_id,
                "purpose": sidechain_purpose,
                "label": label,
            },
        )

        try:
            chat_fn = getattr(self._pybot, "chat", None)
            if chat_fn is None:
                raise RuntimeError("PyBot instance has no .chat() method")

            response = chat_fn(task)
            elapsed = time.time() - t0

            try:
                self._session_runtime.upsert_sidechain(
                    self._session_key,
                    purpose=sidechain_purpose,
                    status="completed",
                    summary=str(response)[:200],
                    sidechain_id=sidechain_id,
                )
            except Exception:
                pass

            result = RunResult(
                run_id=run_id,
                run_kind=RUN_KIND_BACKGROUND,
                status="completed",
                response=str(response),
                mode=mode,
                elapsed=elapsed,
                metadata={"sidechain_id": sidechain_id, "purpose": sidechain_purpose},
            )
            self._add_timeline_event(
                "run_end",
                {"run_id": run_id, "run_kind": RUN_KIND_BACKGROUND, "status": "completed", "elapsed": round(elapsed, 3)},
            )
            return result

        except Exception as exc:
            elapsed = time.time() - t0
            err_str = str(exc)
            try:
                self._session_runtime.upsert_sidechain(
                    self._session_key,
                    purpose=sidechain_purpose,
                    status="error",
                    summary=err_str[:200],
                    sidechain_id=sidechain_id,
                )
            except Exception:
                pass
            self._add_timeline_event(
                "run_end",
                {"run_id": run_id, "run_kind": RUN_KIND_BACKGROUND, "status": "error", "error": err_str},
            )
            return RunResult(
                run_id=run_id,
                run_kind=RUN_KIND_BACKGROUND,
                status="error",
                response="",
                mode=mode,
                elapsed=elapsed,
                error=err_str,
                metadata={"sidechain_id": sidechain_id},
            )

    def switch_mode(self, new_mode: str) -> ModeTransition:
        """Switch the session's active mode profile."""
        normalized = str(new_mode).strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(f"Unknown mode: {new_mode!r}. Valid modes: {sorted(VALID_MODES)}")

        previous = self._current_mode()

        try:
            self._session_runtime.switch_mode(self._session_key, new_mode=normalized)
        except (KeyError, ValueError, Exception):
            pass

        try:
            from core.modes import resolve_mode_profile

            self._pybot.mode_profile = resolve_mode_profile(normalized)
            self._pybot.root_mode = normalized
        except Exception:
            pass

        return ModeTransition(
            previous_mode=previous,
            new_mode=normalized,
            session_key=self._session_key,
        )

    def assess_budget(self) -> BudgetAssessment:
        """Return current context budget assessment."""
        if self._budget_manager is None:
            from core.systems.context.context_budget import ContextBudgetManager

            model_name = getattr(self._pybot, "model_name", "")
            self._budget_manager = ContextBudgetManager(model_name=model_name)

        try:
            record = self._session_runtime.get_session(self._session_key)
        except Exception:
            record = None

        return self._budget_manager.assess(record)

    def status(self) -> SessionStatus:
        """Return a lightweight status snapshot of the current session."""
        budget = self.assess_budget()

        try:
            record = self._session_runtime.get_session(self._session_key)
        except Exception:
            record = None

        if isinstance(record, dict):
            mode = str(record.get("primary_mode", "assistant")) or "assistant"
            mode_history = list(record.get("mode_history", []))
            sess_status = str(record.get("status", "active")) or "active"
            message_count = int(record.get("message_count", 0) or 0)
            last_message_at = record.get("last_message_at")
            timeline_events = len(record.get("timeline", []))
            thread_id = str(record.get("thread_id", self._session_key))
        else:
            mode = self._current_mode()
            mode_history = [mode]
            sess_status = "unknown"
            message_count = 0
            last_message_at = None
            timeline_events = 0
            thread_id = getattr(self._pybot, "thread_id", self._session_key)

        sidechains: list[str] = []
        try:
            raw_sidechains = self._session_runtime.get_sidechains(self._session_key)
            sidechains = [f"{sc.get('purpose', '')}:{sc.get('status', '')}" for sc in raw_sidechains]
        except Exception:
            pass

        return SessionStatus(
            session_key=self._session_key,
            thread_id=thread_id,
            mode=mode,
            mode_history=mode_history,
            status=sess_status,
            message_count=message_count,
            last_message_at=last_message_at,
            timeline_events=timeline_events,
            sidechains=sidechains,
            budget_level=budget.level,
            estimated_tokens=budget.estimated_tokens,
            context_limit=budget.context_limit,
        )

    def _current_mode(self) -> str:
        try:
            record = self._session_runtime.get_session(self._session_key)
            if isinstance(record, dict):
                return str(record.get("primary_mode", "assistant")) or "assistant"
        except Exception:
            pass
        return getattr(self._pybot, "root_mode", "assistant")

    @property
    def session_key(self) -> str:
        return self._session_key

    @property
    def budget_manager(self) -> ContextBudgetManager | None:
        return self._budget_manager


def _derive_session_key(pybot: Any) -> str:
    thread_id = str(getattr(pybot, "thread_id", "default")).strip()
    return thread_id or f"sess-{uuid.uuid4().hex[:10]}"


def _new_run_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"
