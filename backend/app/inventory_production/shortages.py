"""UC-03 steps 6 and 6a: the AI explains a saved shopping list (BAKE-31).

``ShortageExplainer.explain`` runs when the owner asks; nothing is saved. It
reads the ``ShoppingList`` that ``InventoryService`` saved (TX-STOCK-CHECK) and
gives ``InventoryProductionAgent.explain_shortages`` only those quantities,
with the ingredient names and stock units, in a server-built ``TaskRequest``
(architecture §5.2). The agent never changes a quantity or stock: the list it
explains is the one returned, whatever the agent says.

``check_explanation`` decides whether an explanation is usable. Every number
in it must be one of the list's quantities, with the unit of a line with that
quantity straight after it (or, for a count such as eggs, the ingredient's
name), never signed (``-``, ``−`` or ``–``, spaced or not) and never written
in words (review of #19). Every short ingredient must be named with its
shortfall. No ingredient outside the list may be named: the check knows the
inventory's ingredients and a short list of common baking ones, so another
ingredient can still slip through. It does not check which ingredient each
number is said of.

``WorkflowService.run_ai_task`` owns the budget: an unreadable reply or one
that fails the check is retried at most twice. If none is usable, the owner
gets the same list with ``UNAVAILABLE_NOTICE`` (UC-03 6a), and the failure is
in the workflow audit log. A list with no shortage (UC-03 4a) needs no
explanation, so the agent is not called. A list for an older order version is
refused before and after the agent runs, whether or not it gave an
explanation [R10].
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory_production.scheduling import InventoryProductionAgent, ShortageExplanation
from app.llm.gateway import ILLMClient, LLMCallFailed
from app.shared import RecordRef, StaleVersionError, ValidatedRecordStore
from app.shared.records import InventoryItem, Order, ShoppingList
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

TASK_KIND = "explain_shortages"
PROMPT_VERSION = "explain_shortages/1"
EVENT_TYPE = "explainShortages"
UNAVAILABLE_NOTICE = "Explanation temporarily unavailable. The shopping list is unchanged."
_log = logging.getLogger(__name__)

# A number, including ".5", with any sign before it, even a spaced one ("- 200").
_NUMBER = re.compile(
    r"(?P<sign>[-−–]\s*)?(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
)
# Numbers in words escape the digit check, so they are not allowed at all.
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundred|thousand|million|dozen|half)\b",
    re.IGNORECASE,
)
# What may follow a quantity in each stock unit; other units are written as stored.
_UNIT_WORDS = {
    "g": r"g|grams?",
    "kg": r"kg|kilograms?",
    "ml": r"ml|millilit(?:re|er)s?",
    "l": r"l|lit(?:re|er)s?",
}
# Common baking ingredients that this bakery may not stock, so they are not in
# the inventory; an explanation cannot name them either unless one is a word of
# a listed name ("sugar" for "Caster sugar"). Any other ingredient is not caught.
_COMMON_INGREDIENTS = (
    "almond", "baking powder", "baking soda", "bicarbonate of soda", "butter", "buttermilk",
    "caramel", "chocolate", "cinnamon", "cocoa", "coconut", "cream", "cream cheese", "egg",
    "flour", "fondant", "food colouring", "food coloring", "gelatin", "gelatine", "honey", "jam",
    "lemon", "margarine", "milk", "nut", "oil", "orange", "raspberry", "salt", "sprinkle",
    "strawberry", "sugar", "syrup", "vanilla", "yeast", "yoghurt", "yogurt",
)


@dataclass(frozen=True)
class ListLine:
    """One ingredient on a saved list, in its stock unit."""

    item_id: uuid.UUID
    ingredient: str
    unit: str
    required: Decimal
    # None when the stock covers this ingredient.
    short_by: Decimal | None = None


class ExplanationStatus(StrEnum):
    # The agent's text passed ``check_explanation``.
    EXPLAINED = "Explained"
    # No usable explanation within the budget (UC-03 6a); the list is unchanged.
    UNAVAILABLE = "Unavailable"
    # Nothing is short (UC-03 4a); the agent was not called.
    NO_SHORTAGES = "NoShortages"


@dataclass(frozen=True)
class ExplanationOutcome:
    status: ExplanationStatus
    shopping_list_ref: RecordRef
    order_ref: RecordRef
    lines: tuple[ListLine, ...]
    text: str = ""
    notice: str = ""


class ShortageExplainer(WorkflowService):
    """UC-03 6/6a workflow extension; reuses BAKE-8's single AI retry budget."""

    def __init__(self, store: ValidatedRecordStore, client: ILLMClient):
        super().__init__()
        self.store = store
        self.agent = InventoryProductionAgent(client)

    def explain(self, list_id: uuid.UUID, *, actor_id: uuid.UUID) -> ExplanationOutcome:
        """Explain shopping list ``list_id`` for the owner; nothing is saved.

        Raises ``RecordNotFound`` for an unknown list, and ``StaleVersionError``
        if its order changed after the list was saved, before or while the
        agent ran: run the stock check again for the current version.
        """
        with self.store.unit_of_work() as session:
            shopping_list = self.store.load(session, ShoppingList, list_id)
            order_id, order_version = shopping_list.order_id, shopping_list.source_order_version
            order = _current_order(self.store, session, order_id, order_version)
            stage = OrderStage(order.status)
            order_ref = self.store.ref(order)
            lines = _lines(session, shopping_list)
            listed = {line.ingredient for line in lines}
            others = [n for n in session.scalars(select(InventoryItem.ingredient_name)) if n not in listed]
        # Shopping lists never change once saved, so version 1 names the list.
        list_ref = RecordRef("ShoppingList", list_id, 1)
        if all(line.short_by is None for line in lines):
            return ExplanationOutcome(ExplanationStatus.NO_SHORTAGES, list_ref, order_ref, lines)

        request = _task_request(list_ref, order_ref, lines)
        problems: list[str] = []

        def attempt() -> ShortageExplanation:
            try:
                return self.agent.explain_shortages(request)
            except (LLMCallFailed, ValidationError):
                problems[:] = ["the assistant gave no readable explanation"]
                raise

        def usable(explanation: ShortageExplanation) -> bool:
            problems[:] = check_explanation(explanation.text, lines, others)
            return not problems

        try:
            explanation = self.run_ai_task(
                list_ref, stage, attempt, usable, actor=str(actor_id), event_type=EVENT_TYPE
            )
        except AIOperationFailed:
            _log.warning("No usable explanation for shopping list %s: %s", list_id, "; ".join(problems))
            explanation = None
        # The order may have changed while the agent ran; the list would be stale.
        with self.store.unit_of_work() as session:
            _current_order(self.store, session, order_id, order_version)
        if explanation is None:
            return ExplanationOutcome(
                ExplanationStatus.UNAVAILABLE, list_ref, order_ref, lines, notice=UNAVAILABLE_NOTICE
            )
        return ExplanationOutcome(
            ExplanationStatus.EXPLAINED, list_ref, order_ref, lines, text=explanation.text
        )


def check_explanation(text: str, lines: Sequence[ListLine], others: Iterable[str]) -> list[str]:
    """Why ``text`` does not fit the list ``lines``; empty means usable.

    ``others`` are the names of inventory ingredients that are not on the list.
    """
    problems: list[str] = []
    mentioned: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        shown = match.group()
        if match["sign"]:
            problems.append(f"{shown!r} is signed; list quantities are never negative")
            continue
        number = Decimal(match["number"].replace(",", ""))
        mentioned.add(number)
        same = [line for line in lines if number in (line.required, line.short_by)]
        if not same:
            problems.append(f"{shown!r} is not a quantity on the list")
        elif not any(_unit(line).match(text, match.end()) for line in same):
            units = ", ".join(sorted({line.unit for line in same}))
            problems.append(f"{shown!r} is not followed by its unit ({units})")
    for match in _NUMBER_WORDS.finditer(text):
        problems.append(f"{match.group()!r} writes a number in words")

    listed_spans: list[tuple[int, int]] = []
    for line in lines:
        spans = [m.span() for m in _name(line.ingredient).finditer(text)]
        listed_spans += spans
        if line.short_by is None:
            continue
        if not spans:
            problems.append(f"the short ingredient {line.ingredient} is not named")
        if line.short_by not in mentioned:
            problems.append(f"the shortfall of {line.ingredient} ({_plain(line.short_by)}) is not given")
    # A name inside one already matched is not another ingredient: "sugar" in a
    # listed "icing sugar", or "cocoa" in an unlisted "cocoa powder".
    named = list(listed_spans)
    for name in others:
        spans = [m.span() for m in _name(name).finditer(text) if not _inside(m.span(), listed_spans)]
        if spans:
            problems.append(f"{name} is not on the list")
            named += spans
    for name in _COMMON_INGREDIENTS:
        if any(_name(name).search(line.ingredient) for line in lines):
            continue
        if any(not _inside(m.span(), named) for m in _name(name).finditer(text)):
            problems.append(f"{name} is not on the list")
    return problems


def _inside(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in spans)


def _current_order(store: ValidatedRecordStore, session: Session, order_id: uuid.UUID, version: int) -> Order:
    order = store.load(session, Order, order_id)
    if order.version != version:
        raise StaleVersionError(
            f"Order {order_id} is at version {order.version}; the list is for version {version}"
        )
    return order


def _lines(session: Session, shopping_list: ShoppingList) -> tuple[ListLine, ...]:
    """The list's lines, short ingredients first, each group by name."""
    requirements = {uuid.UUID(k): Decimal(v) for k, v in shopping_list.requirements.items()}
    shortages = {uuid.UUID(k): Decimal(v) for k, v in shopping_list.shortages.items()}
    items = {
        item.item_id: item
        for item in session.scalars(select(InventoryItem).where(InventoryItem.item_id.in_(list(requirements))))
    }
    lines = [
        ListLine(item_id, items[item_id].ingredient_name, items[item_id].unit, quantity, shortages.get(item_id))
        for item_id, quantity in requirements.items()
    ]
    return tuple(sorted(lines, key=lambda line: (line.short_by is None, line.ingredient.lower())))


def _task_request(list_ref: RecordRef, order_ref: RecordRef, lines: Iterable[ListLine]) -> dict:
    """The server-built TaskRequest (architecture §5.2); only the saved list's facts."""
    return {
        "taskId": str(uuid.uuid4()),
        "taskKind": TASK_KIND,
        "promptVersion": PROMPT_VERSION,
        "subjectRef": list_ref.to_json(),
        "inputRefs": [order_ref.to_json()],
        "inputData": {
            "ingredients": [
                {
                    "ingredient": line.ingredient,
                    "unit": line.unit,
                    "required": _plain(line.required),
                    "short_by": None if line.short_by is None else _plain(line.short_by),
                }
                for line in lines
            ],
        },
    }


def _name(ingredient: str) -> re.Pattern[str]:
    """An ingredient's name in running text."""
    return re.compile(rf"\b{_name_source(ingredient)}\b", re.IGNORECASE)


def _name_source(ingredient: str) -> str:
    """Singular or plural: "egg" or "Eggs", "strawberry" or "strawberries"."""
    words = ingredient.split()
    last = words[-1]
    if last.lower().endswith("s"):
        ending = re.escape(last[:-1]) + "(?:s|es)?"
    elif re.search(r"[^aeiou]y$", last, re.IGNORECASE):
        ending = re.escape(last[:-1]) + "(?:y|ies)"
    else:
        ending = re.escape(last) + "(?:s|es)?"
    return r"\s+".join([*(re.escape(word) for word in words[:-1]), ending])


def _unit(line: ListLine) -> re.Pattern[str]:
    """The line's unit straight after a quantity; for a count, its name will do ("12 eggs")."""
    words = _UNIT_WORDS.get(line.unit.lower()) or f"{re.escape(line.unit)}|{_name_source(line.ingredient)}"
    return re.compile(rf"\s*(?:{words})\b", re.IGNORECASE)


def _plain(quantity: Decimal) -> str:
    """``1200.000`` as ``1200``, ``0.500`` as ``0.5``."""
    return f"{quantity.normalize():f}"
