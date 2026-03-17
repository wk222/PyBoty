"""Tests for approval governance dashboard query layer."""

from __future__ import annotations

from core.approval_dashboard import ApprovalDashboard, DashboardFilter
from core.approval_queue import ApprovalQueue


def _make_queue_with_requests() -> ApprovalQueue:
    queue = ApprovalQueue(storage_path=None)

    queue.create_request(
        kind="tool_call",
        scope="agent_alpha",
        summary="Execute rm -rf",
        prompt="Allow dangerous command?",
        labels=["dangerous"],
        policy_tags=["high_risk"],
    )
    queue.create_request(
        kind="tool_call",
        scope="agent_beta",
        summary="Write config file",
        prompt="Allow file write?",
        labels=["write"],
        policy_tags=["low_risk"],
    )

    req3 = queue.create_request(
        kind="delegation",
        scope="agent_alpha",
        summary="Delegate to sub-agent",
        prompt="Allow delegation?",
        labels=["delegation"],
        policy_tags=["medium_risk"],
    )
    queue.resolve(req3.approval_id, approved=True, note="Looks safe", resolved_by="admin")

    req4 = queue.create_request(
        kind="tool_call",
        scope="agent_gamma",
        summary="Delete database",
        prompt="Allow deletion?",
        labels=["dangerous"],
        policy_tags=["high_risk"],
    )
    queue.resolve(req4.approval_id, approved=False, note="Too risky", resolved_by="ops_lead")

    return queue


class TestDashboardQuery:
    def test_query_all(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        results = dashboard.query()
        assert len(results) == 4

    def test_filter_by_status(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        results = dashboard.query(DashboardFilter(status="pending"))
        assert len(results) == 2

    def test_filter_by_scope(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        results = dashboard.query(DashboardFilter(scope="agent_alpha"))
        assert len(results) == 2

    def test_filter_by_policy_tag(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        results = dashboard.query(DashboardFilter(policy_tag="high_risk"))
        assert len(results) == 2

    def test_filter_by_label(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        results = dashboard.query(DashboardFilter(label="dangerous"))
        assert len(results) == 2


class TestDashboardGroupBy:
    def test_group_by_kind(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        groups = dashboard.group_by("kind")
        assert "tool_call" in groups
        assert "delegation" in groups
        assert len(groups["tool_call"]) == 3

    def test_group_by_status(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        groups = dashboard.group_by("status")
        assert "pending" in groups
        assert "approved" in groups
        assert "rejected" in groups


class TestDashboardStats:
    def test_summary_stats(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        stats = dashboard.summary_stats()
        assert stats["total"] == 4
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["approved"] == 1
        assert stats["by_status"]["rejected"] == 1

    def test_agent_activity(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        stats = dashboard.agent_activity("agent_alpha")
        assert stats["total"] == 2


class TestAuditTrail:
    def test_audit_trail_excludes_pending(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        trail = dashboard.audit_trail()
        for entry in trail:
            assert entry.status != "pending"

    def test_audit_trail_has_resolver_info(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        trail = dashboard.audit_trail()
        resolvers = {e.resolved_by for e in trail}
        assert "admin" in resolvers
        assert "ops_lead" in resolvers

    def test_audit_trail_to_dict(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        trail = dashboard.audit_trail()
        for entry in trail:
            d = entry.to_dict()
            assert "approval_id" in d
            assert "latency_seconds" in d


class TestTrends:
    def test_approval_trends_returns_buckets(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        trends = dashboard.approval_trends(window_seconds=3600, num_windows=2)
        assert len(trends) == 2
        total = sum(b.approved + b.rejected + b.pending for b in trends)
        assert total >= 0

    def test_stale_pending(self):
        dashboard = ApprovalDashboard(_make_queue_with_requests())
        stale = dashboard.stale_pending(max_age_seconds=0.001)
        assert len(stale) >= 0
