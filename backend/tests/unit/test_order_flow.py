"""Tests for the order workflow stages and audit log (BAKE-8)."""

from __future__ import annotations

import uuid

import pytest

from app.llm.gateway import LLMCallFailed
from app.shared.refs import RecordRef
from app.workflow.order_flow import (
    OrderStage,
    TransitionRejected,
    WorkflowService,
    AIOperationFailed,
)

ORDER = RecordRef(record_type="Order", record_id=uuid.uuid4(), version=1)

ALLOWED = [
    (OrderStage.DRAFT, OrderStage.QUOTED),
    (OrderStage.DRAFT, OrderStage.CANCELLED),
    (OrderStage.QUOTED, OrderStage.CONFIRMED),
    (OrderStage.QUOTED, OrderStage.CANCELLED),
    (OrderStage.CONFIRMED, OrderStage.SCHEDULED),
    (OrderStage.CONFIRMED, OrderStage.CANCELLED),
    (OrderStage.SCHEDULED, OrderStage.IN_PRODUCTION),
    # [BAKE-41] A scheduled order can still be cancelled.
    (OrderStage.SCHEDULED, OrderStage.CANCELLED),
    # [BAKE-21] A released revision can leave it out.
    (OrderStage.SCHEDULED, OrderStage.CONFIRMED),
    (OrderStage.IN_PRODUCTION, OrderStage.COMPLETED),
]

BLOCKED = [
    (OrderStage.DRAFT, OrderStage.COMPLETED),
    (OrderStage.DRAFT, OrderStage.CONFIRMED),
    (OrderStage.QUOTED, OrderStage.SCHEDULED),
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
        raise LLMCallFailed("malformed response")

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

# --- a defect in our own code is not an AI failure (BAKE-41) ---------------


def test_a_bug_in_our_own_code_is_not_retried_or_reported_as_an_ai_failure():
    service = WorkflowService()
    attempts = []

    def attempt():
        attempts.append(1)
        raise TypeError("unsupported operand type(s)")

    with pytest.raises(TypeError):
        service.run_ai_task(
            ORDER, OrderStage.DRAFT, attempt, lambda r: True,
            actor="workflow", event_type="generateContent",
        )

    assert len(attempts) == 1
    assert service.audit_log == ()


def test_a_caller_can_widen_what_counts_as_retryable():
    service = WorkflowService()
    attempts = []

    def attempt():
        attempts.append(1)
        raise RuntimeError("the agent's own wrapper failed")

    with pytest.raises(AIOperationFailed):
        service.run_ai_task(
            ORDER, OrderStage.DRAFT, attempt, lambda r: True,
            actor="workflow", event_type="generateContent",
            retry_on=(RuntimeError,),
        )

    assert len(attempts) == 3
