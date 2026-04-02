"""Workflow specific exceptions."""

from __future__ import annotations


class WorkflowApprovalPause(Exception):
    """Raised when a workflow needs human approval before continuing."""

    def __init__(self, workflow_id: str, node_id: str, resume_token: str, prompt: str, approval_id: str):
        self.workflow_id = workflow_id
        self.node_id = node_id
        self.resume_token = resume_token
        self.prompt = prompt
        self.approval_id = approval_id
        super().__init__(f"Workflow paused for approval: {prompt}")


class WorkflowTimerPause(Exception):
    """Raised when a DELAY node uses durable timer (persists across restarts)."""

    def __init__(self, workflow_id: str, node_id: str, resume_at: float, resume_token: str):
        self.workflow_id = workflow_id
        self.node_id = node_id
        self.resume_at = resume_at
        self.resume_token = resume_token
        super().__init__(f"Workflow paused until {resume_at}")


class WorkflowSignalPause(Exception):
    """Raised when a wait_signal node awaits an external event."""

    def __init__(self, workflow_id: str, node_id: str, signal_name: str, resume_token: str):
        self.workflow_id = workflow_id
        self.node_id = node_id
        self.signal_name = signal_name
        self.resume_token = resume_token
        super().__init__(f"Workflow waiting for signal: {signal_name}")
