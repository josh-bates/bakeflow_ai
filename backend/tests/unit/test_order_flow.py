"""Tests for the order workflow stages and audit log (BAKE-8)."""

from __future__ import annotations

import pytest

from app.workflow.order_flow import (
    OrderStage,
    RecordRef,
    TransitionRejected,
    WorkflowService,
    AIOperationFailed,
)

ORDER = RecordRef(record_type="Order", record_id="O1", version=1)

ALLOWED = [
    (OrderStage.DRAFT, OrderStage.QUOTED),
    (OrderStage.DRAFT, OrderStage.CANCELLED),
    (OrderStage.QUOTED, OrderStage.CONFIRMED),
    (OrderStage.QUOTED, OrderStage.CANCELLED),
    (OrderStage.CONFIRMED, OrderStage.SCHEDULED),
    (OrderStage.CONFIRMED, OrderStage.CANCELLED),
    (OrderStage.SCHEDULED, OrderStage.IN_PRODUCTION),
    (OrderStage.IN_PRODUCTION, OrderStage.COMPLETED),
]

BLOCKED = [
    (OrderStage.DRAFT, OrderStage.COMPLETED),
    (OrderStage.DRAFT, OrderStage.CONFIRMED),
    (OrderStage.QUOTED, OrderStage.SCHEDULED),
    (OrderStage.SCHEDULED, OrderStage.CANCELLED),
    (OrderStage.IN_PRODUCTION, OrderStage.CANCELLED),
    (OrderStage.COMPLETED, OrderStage.CANCELLED),
    (OrderStage.CANCELLED, OrderStage.DRAFT),
]


@pytest.mark.parametrize("current,requested", ALLOWED)
def test_allowed_transitions_move_the_order(current, requested):
    service = WorkflowService()
    result = service.move_order(ORDER, current, requested, actor="owner", event_type="test")
    assert result is requested


@pytest.mark.parametrize("current,requested", BLOCKED)
def test_blocked_transitions_are_rejected(current, requested):
    service = WorkflowService()
    with pytest.raises(TransitionRejected):
        service.move_order(ORDER, current, requested, actor="owner", event_type="test")


@pytest.mark.parametrize("current,requested", BLOCKED)
def test_blocked_transitions_write_nothing(current, requested):
    service = WorkflowService()
    with pytest.raises(TransitionRejected):
        service.move_order(ORDER, current, requested, actor="owner", event_type="test")
    assert service.audit_log == ()


def test_one_audit_entry_per_transition():
    service = WorkflowService()
    stage = OrderStage.DRAFT
    for nxt in (
        OrderStage.QUOTED,
        OrderStage.CONFIRMED,
        OrderStage.SCHEDULED,
        OrderStage.IN_PRODUCTION,
        OrderStage.COMPLETED,
    ):
        stage = service.move_order(ORDER, stage, nxt, actor="owner", event_type="test")
    assert len(service.audit_log) == 5


def test_audit_entry_records_both_states():
    service = WorkflowService()
    service.move_order(
        ORDER, OrderStage.DRAFT, OrderStage.QUOTED,
        actor="owner", event_type="quotationApproved",
    )
    entry = service.audit_log[0]
    assert entry.previous_state == "Draft"
    assert entry.resulting_state == "Quoted"
    assert entry.actor == "owner"
    assert entry.subject == ORDER


def test_always_failing_task_gets_exactly_three_attempts():
    service = WorkflowService()
    attempts = []

    def attempt():
        attempts.append(1)
        return "unusable"

    with pytest.raises(AIOperationFailed):
        service.run_ai_task(
            ORDER, OrderStage.DRAFT, attempt, lambda r: False,
            actor="workflow", event_type="generateContent",
        )

    assert len(attempts) == 3


def test_crashing_task_also_burns_attempts():
    service = WorkflowService()
    attempts = []

    def attempt():
        attempts.append(1)
        raise RuntimeError("malformed response")

    with pytest.raises(AIOperationFailed):
        service.run_ai_task(
            ORDER, OrderStage.DRAFT, attempt, lambda r: True,
            actor="workflow", event_type="generateContent",
        )

    assert len(attempts) == 3


def test_usable_result_stops_retrying():
    service = WorkflowService()
    attempts = []

    def attempt():
        attempts.append(1)
        return "good" if len(attempts) == 2 else "bad"

    result = service.run_ai_task(
        ORDER, OrderStage.DRAFT, attempt, lambda r: r == "good",
        actor="workflow", event_type="generateContent",
    )

    assert result == "good"
    assert len(attempts) == 2


def test_failure_is_logged_and_the_order_does_not_move():
    service = WorkflowService()

    with pytest.raises(AIOperationFailed):
        service.run_ai_task(
            ORDER, OrderStage.DRAFT, lambda: "bad", lambda r: False,
            actor="workflow", event_type="generateContent",
        )

    assert len(service.audit_log) == 1
    entry = service.audit_log[0]
    assert entry.event_type == "generateContentFailed"
    assert entry.previous_state == "Draft"
    assert entry.resulting_state == "Draft"