"""[O1] BAKE-37: the owner-requested low-stock explanation (AR-02 extension).

The alerts themselves are deterministic (``InventoryService.low_stock``): an
ingredient is low when its available stock (on hand less IP-02 commitments)
is below its reorder level, and the suggested reorder brings it back to twice
the level. Reading them never calls a model.

``LowStockExplainer.explain`` runs only when the owner asks. It gives
``InventoryProductionAgent.explain_low_stock`` the low ingredients' figures in
a server-built ``TaskRequest`` (architecture §5.2): on hand, committed,
reorder level and suggested reorder, which are never negative, and available
when it is not negative. ``check_low_stock_explanation`` is BAKE-31's check,
held to each ingredient: every number must be one of the figures of the
ingredient it is said of (the one named straight after it, as in "300 g of
flour", or else the one named last before it) with its unit straight after it,
every low ingredient must be named with its own suggested reorder, and no
other ingredient may be named. So a reply cannot swap two ingredients'
figures (review of BAKE-37).

The agent changes nothing: it cannot set a reorder level, change stock or
order anything, and nothing is saved but the ``ai_requests`` row [R5].
``WorkflowService.run_ai_task`` owns the shared budget; if no explanation is
usable, the owner gets the alerts with ``UNAVAILABLE_NOTICE``. With no alert
the agent is not called. If stock changed while the agent ran, the current
alerts come back with ``CHANGED_NOTICE`` instead of an explanation of old
figures.

Design gap: the architecture routes this request through
``WorkflowService.handle`` (``LOW_STOCK_EXPLANATION_REQUESTED``) and
``dispatch``. Neither exists yet, so this follows BAKE-31 and calls
``run_ai_task`` directly.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError

from app.inventory_production.inventory import InventoryService, StockLevel
from app.inventory_production.scheduling import InventoryProductionAgent, LowStockExplanation
from app.inventory_production.shortages import IngredientFacts, check_facts
from app.llm.gateway import ILLMClient, LLMCallFailed
from app.shared import RecordRef, ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

TASK_KIND = "explain_low_stock"
PROMPT_VERSION = "explain_low_stock/1"
EVENT_TYPE = "explainLowStock"
UNAVAILABLE_NOTICE = "Explanation temporarily unavailable. The low-stock list is unchanged."
CHANGED_NOTICE = (
    "Stock changed while the explanation was being written. The list shows current stock;"
    " ask again for an explanation."
)
_log = logging.getLogger(__name__)


class LowStockStatus(StrEnum):
    # The agent's text passed ``check_low_stock_explanation``.
    EXPLAINED = "Explained"
    # No usable explanation within the budget; the alerts are unchanged.
    UNAVAILABLE = "Unavailable"
    # Nothing is low; the agent was not called.
    NO_ALERTS = "NoAlerts"
    # Stock changed while the agent ran; the current alerts, unexplained.
    CHANGED = "Changed"


@dataclass(frozen=True)
class LowStockOutcome:
    status: LowStockStatus
    lines: tuple[StockLevel, ...]
    text: str = ""
    notice: str = ""


class LowStockExplainer(WorkflowService):
    """[O1] owner-requested explanation; reuses BAKE-8's single AI retry budget."""

    def __init__(self, store: ValidatedRecordStore, client: ILLMClient):
        super().__init__()
        self.inventory = InventoryService(store)
        self.agent = InventoryProductionAgent(client)

    def explain(self, *, actor_id: uuid.UUID) -> LowStockOutcome:
        """Explain the current low-stock alerts for the owner; only its ``ai_requests`` row is saved."""
        levels = self.inventory.stock_levels()
        lines = tuple(level for level in levels if level.low)
        if not lines:
            return LowStockOutcome(LowStockStatus.NO_ALERTS, ())
        others = [level.ingredient for level in levels if not level.low]
        # The alerts are derived, never saved, so each request names its own.
        subject = RecordRef("LowStockAlerts", uuid.uuid4(), 1)
        request = _task_request(subject, lines)
        problems: list[str] = []

        def attempt() -> LowStockExplanation:
            try:
                return self.agent.explain_low_stock(request)
            except (LLMCallFailed, ValidationError):
                problems[:] = ["the assistant gave no readable explanation"]
                raise

        def usable(explanation: LowStockExplanation) -> bool:
            problems[:] = check_low_stock_explanation(explanation.text, lines, others)
            return not problems

        try:
            # BAKE-8's failure audit needs an order stage; like plans, alerts
            # concern confirmed work (the stock its released plans commit).
            explanation = self.run_ai_task(
                subject, OrderStage.CONFIRMED, attempt, usable, actor=str(actor_id),
                event_type=EVENT_TYPE, prompt_version=PROMPT_VERSION,
            )
        except AIOperationFailed:
            _log.warning("No usable low-stock explanation: %s", "; ".join(problems))
            explanation = None
        current = tuple(self.inventory.low_stock())
        if explanation is None:
            return LowStockOutcome(LowStockStatus.UNAVAILABLE, current, notice=UNAVAILABLE_NOTICE)
        if current != lines:
            return LowStockOutcome(LowStockStatus.CHANGED, current, notice=CHANGED_NOTICE)
        return LowStockOutcome(LowStockStatus.EXPLAINED, lines, text=explanation.text)


def check_low_stock_explanation(
    text: str, lines: Sequence[StockLevel], others: Iterable[str]
) -> list[str]:
    """Why ``text`` does not fit the low ``lines``; empty means usable.

    ``others`` are the names of inventory ingredients that are not low.
    """
    facts = [
        IngredientFacts(line.ingredient, line.unit, frozenset(_figures(line)), line.suggested_reorder)
        for line in lines
    ]
    return check_facts(text, facts, others, flagged="low", key="suggested reorder", by_ingredient=True)


def _figures(line: StockLevel) -> list[Decimal]:
    """The numbers an explanation may give for ``line``, none of them negative."""
    figures = [line.on_hand, line.committed, line.reorder_level, line.suggested_reorder]
    if line.available >= 0:
        figures.append(line.available)
    return figures


def _task_request(subject: RecordRef, lines: Iterable[StockLevel]) -> dict:
    """The server-built TaskRequest (architecture §5.2); only the alerts' figures."""
    lines = list(lines)
    return {
        "taskId": str(uuid.uuid4()),
        "taskKind": TASK_KIND,
        "promptVersion": PROMPT_VERSION,
        "subjectRef": subject.to_json(),
        "inputRefs": [RecordRef("InventoryItem", line.item_id, line.version).to_json() for line in lines],
        "inputData": {
            "ingredients": [
                {
                    "ingredient": line.ingredient,
                    "unit": line.unit,
                    "on_hand": _plain(line.on_hand),
                    "committed": _plain(line.committed),
                    # Left out when plans commit more than is on hand: never negative.
                    "available": _plain(line.available) if line.available >= 0 else None,
                    "reorder_level": _plain(line.reorder_level),
                    "suggested_reorder": _plain(line.suggested_reorder),
                }
                for line in lines
            ],
        },
    }


def _plain(quantity: Decimal) -> str:
    """``1200.000`` as ``1200``, ``0.500`` as ``0.5``."""
    return f"{quantity.normalize():f}"
