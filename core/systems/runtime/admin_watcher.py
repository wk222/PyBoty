"""Background watcher daemon for the Admin agent to monitor ecosystem telemetry."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from core.systems.runtime.daemon import BackgroundDaemon
from core.systems.runtime.event_bus import Event, EventType, event_bus

logger = logging.getLogger(__name__)


class AdminWatcherDaemon:
    """Monitors global events and triggers LLM-based synthesis for ecosystem health."""

    def __init__(self, llm: Any, daemon: BackgroundDaemon, workspace_dir: str | Path, interval_sec: float = 300.0):
        self.llm = llm
        self.daemon = daemon
        self.workspace_dir = Path(workspace_dir)
        self.interval_sec = interval_sec
        self.last_check_time = time.time() - interval_sec

        # Register the job with the background daemon
        self.daemon.add_job(
            name="admin_telemetry_watcher",
            interval_sec=self.interval_sec,
            func=self._run_analysis_cycle,
        )

    def _run_analysis_cycle(self) -> None:
        """Analyze recent events and generate a health report."""
        now = time.time()

        # Fetch events since last check
        recent_events = event_bus.persistent_history(since=self.last_check_time, limit=1000)
        self.last_check_time = now

        if not recent_events:
            return

        # Filter for interesting events
        interesting_events = []
        for event in recent_events:
            if event.type in (
                EventType.ERROR,
                EventType.SUBAGENT_FAILED,
                EventType.GUARDRAIL_FAIL,
                EventType.TOOL_RESULT,
            ):
                # For tool results, only include errors
                if event.type == EventType.TOOL_RESULT:
                    payload = event.payload
                    if isinstance(payload, dict) and payload.get("success") is False:
                        interesting_events.append(event)
                else:
                    interesting_events.append(event)

        if len(interesting_events) > 0:
            logger.info("AdminWatcherDaemon found %d interesting events. Generating report.", len(interesting_events))

            event_summary = []
            for e in interesting_events[:30]:
                event_summary.append(f"- [{e.type.value}] Source: {e.source}, Payload: {e.payload}")

            summary_text = "\n".join(event_summary)

            prompt = (
                "You are the PyBot Admin Telemetry Analyzer.\n"
                "Review the following recent system events (errors, failures, etc.) from the App Matrix ecosystem.\n"
                "Generate a concise 'Ecosystem Health & Capability Gap Report'.\n"
                "Identify recurring issues, missing tools/skills, and suggest specific "
                "actions the Admin should take to repair or evolve the system.\n\n"
                f"Recent Events:\n{summary_text}"
            )

            try:
                response = self.llm.invoke(
                    [
                        SystemMessage(content="You are a system telemetry analyzer."),
                        HumanMessage(content=prompt),
                    ]
                )
                report_content = response.content
                gap_candidates = self.extract_gap_candidates(interesting_events)

                # Save the report to the workspace
                reports_dir = self.workspace_dir / "telemetry_reports"
                reports_dir.mkdir(parents=True, exist_ok=True)

                report_path = reports_dir / f"health_report_{int(now)}.md"
                report_path.write_text(report_content, encoding="utf-8")
                gaps_path = reports_dir / f"capability_gaps_{int(now)}.json"
                gaps_path.write_text(self._serialize_gap_candidates(gap_candidates), encoding="utf-8")
                logger.info("Generated telemetry report at %s", report_path)

                # Emit event so the Admin agent can pick it up and self-evolve
                event_bus.emit(
                    Event(
                        type=EventType.TELEMETRY_REPORT_GENERATED,
                        payload={
                            "report_path": str(report_path),
                            "report_content": report_content,
                            "gaps_path": str(gaps_path),
                            "capability_gap_candidates": gap_candidates,
                        },
                        source="AdminWatcherDaemon",
                    )
                )
                for candidate in gap_candidates:
                    event_bus.emit(
                        Event(
                            type=EventType.CAPABILITY_GAP_DETECTED,
                            payload=candidate,
                            source="AdminWatcherDaemon",
                        )
                    )

            except Exception as exc:
                logger.error("Failed to generate telemetry report: %s", exc)

    @staticmethod
    def extract_gap_candidates(events: list[Event]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            key = (event.type.value, event.source)
            item = grouped.setdefault(
                key,
                {
                    "event_type": event.type.value,
                    "source": event.source,
                    "count": 0,
                    "samples": [],
                },
            )
            item["count"] += 1
            if len(item["samples"]) < 3:
                item["samples"].append(event.payload)

        candidates: list[dict[str, Any]] = []
        for (_etype, source), group in grouped.items():
            if group["count"] < 2:
                continue
            summary = " ".join(str(sample) for sample in group["samples"]).lower()
            hint = "general_runtime_gap"
            if "timeout" in summary:
                hint = "latency_or_batching_gap"
            elif "not found" in summary or "missing" in summary:
                hint = "missing_capability_gap"
            elif "auth" in summary or "permission" in summary:
                hint = "auth_or_policy_gap"
            normalized_source = source.replace(":", "_").replace("/", "_").replace(" ", "_").lower()
            candidates.append(
                {
                    "source": source,
                    "event_type": group["event_type"],
                    "occurrences": group["count"],
                    "gap_type": hint,
                    "suggested_capability_name": f"{normalized_source}_{hint}",
                    "samples": group["samples"],
                }
            )
        candidates.sort(key=lambda item: item["occurrences"], reverse=True)
        return candidates

    @staticmethod
    def _serialize_gap_candidates(candidates: list[dict[str, Any]]) -> str:
        import json

        return json.dumps(
            {
                "version": "1.0",
                "candidates": candidates,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
