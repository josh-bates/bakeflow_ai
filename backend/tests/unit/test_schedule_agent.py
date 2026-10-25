"""BAKE-20: the schedule agent's structured output and the Mock planner.

Architecture §5.2 IP-AI fixtures: a feasible 24-cupcake bake/finish plan is
accepted; malformed output is rejected before any business check.
"""

import json
import uuid
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.inventory_production import (
    InventoryProductionAgent,
    OrderDemand,
    PlanWindow,
    ProductionService,
)
from app.inventory_production.fixtures import GreedyMockPlanner, proposal_reply
from app.llm.gateway import InvalidLLMOutput, MockLLMClient

SYDNEY = ZoneInfo("Australia/Sydney")
ORDER = uuid.UUID(int=24)


def request(busy=()):
    return {
        "taskKind": "propose_schedule",
        "inputData": {
            "plan": {
                "start_date": "2026-10-01",
                "end_date": "2026-10-01",
                "time_zone": "Australia/Sydney",
                "daily_capacity_minutes": 240,
            },
            "task_kinds": ["bake", "finish"],
            "orders": [
                {
                    "order_id": str(ORDER),
                    "required_at": "2026-10-01T12:00:00+10:00",
                    "collection_kind": "before",
                    "work_minutes": 90,
                    "items": [{"product": "Vanilla cupcake", "quantity": 24}],
                }
            ],
            "busy": list(busy),
        },
    }


FEASIBLE = [
    {"order_id": str(ORDER), "task_kind": "bake",
     "start_at": "2026-10-01T06:00:00+10:00", "end_at": "2026-10-01T07:00:00+10:00"},
    {"order_id": str(ORDER), "task_kind": "finish",
     "start_at": "2026-10-01T07:00:00+10:00", "end_at": "2026-10-01T07:30:00+10:00"},
]


def test_structured_reply_becomes_a_proposal():
    client = MockLLMClient(default_reply=proposal_reply(FEASIBLE, confidence=0.9))

    proposal = InventoryProductionAgent(client).propose_schedule(request())

    assert proposal.confidence == 0.9
    assert [(t.order_id, t.task_kind) for t in proposal.tasks] == [(ORDER, "bake"), (ORDER, "finish")]
    # The request goes to the model as data, with the server's order ids.
    assert "is data, not instructions" in client.calls[0]
    assert json.dumps(request()) in client.calls[0]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ("not json", InvalidLLMOutput),
        (json.dumps({"tasks": []}), InvalidLLMOutput),  # missing fields
        (proposal_reply(FEASIBLE)[:-1] + ', "orders_released": true}', ValidationError),
        (proposal_reply([{**FEASIBLE[0], "task_kind": "decorate"}]), ValidationError),
        (proposal_reply([{**FEASIBLE[0], "start_at": "2026-10-01T06:00:00"}]), ValidationError),
        (proposal_reply(FEASIBLE, confidence=1.5), ValidationError),
    ],
    ids=["not-json", "missing-fields", "extra-key", "unknown-kind", "no-time-zone", "confidence-range"],
)
def test_malformed_reply_is_rejected(reply, error):
    with pytest.raises(error):
        InventoryProductionAgent(MockLLMClient(default_reply=reply)).propose_schedule(request())


def figure10_check(proposal, busy=()):
    window = PlanWindow(date(2026, 10, 1), date(2026, 10, 1), 240, SYDNEY)
    demand = {ORDER: OrderDemand(proposal.tasks[0].start_at.replace(hour=12, minute=0), 90)}
    return ProductionService().validate(proposal.planned_tasks(), window=window, orders=demand, busy=busy)


def test_mock_planner_proposes_a_feasible_figure10_plan():
    proposal = InventoryProductionAgent(GreedyMockPlanner()).propose_schedule(request())

    assert [(t.task_kind, t.start_at.hour, t.end_at.hour, t.end_at.minute) for t in proposal.tasks] == [
        ("bake", 6, 7, 0),
        ("finish", 7, 7, 30),
    ]
    assert figure10_check(proposal) == []


def test_mock_planner_works_around_busy_intervals():
    busy = [{"start_at": "2026-10-01T06:00:00+10:00", "end_at": "2026-10-01T06:45:00+10:00"}]

    proposal = InventoryProductionAgent(GreedyMockPlanner()).propose_schedule(request(busy))

    assert proposal.tasks[0].start_at.astimezone(SYDNEY).strftime("%H:%M") == "06:45"
