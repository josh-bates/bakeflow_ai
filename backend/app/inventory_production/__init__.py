"""Inventory and Production module (AR-02, IP-01–IP-05: UC-03, UC-04).

Home of ``InventoryProductionAgent``, ``InventoryService`` and
``ProductionService`` (architecture §6.1), and the schedule release (BAKE-21).
"""

from app.inventory_production.inventory import (
    InventoryService,
    Problem,
    ProblemKind,
    ReceiptConflict,
    ReceiptInput,
    ReceiptRejected,
    StockCheckError,
    StockCheckResult,
    StockLevel,
    StockReceipt,
)
from app.inventory_production.production import (
    OrderDemand,
    PlannedTask,
    PlanWindow,
    ProductionService,
    Violation,
    ViolationKind,
)
from app.inventory_production.release import (
    ReleaseOutcome,
    ReleaseStatus,
    ScheduleRelease,
    ScheduleState,
    ScheduleView,
)
from app.inventory_production.scheduling import (
    ExcludedOrder,
    InventoryProductionAgent,
    PlanOutcome,
    PlanStatus,
    ProductionPlanner,
    ScheduleProposal,
    ShortageExplanation,
)
from app.inventory_production.shortages import (
    ExplanationOutcome,
    ExplanationStatus,
    ListLine,
    ShortageExplainer,
    check_explanation,
)

__all__ = [
    "ExcludedOrder",
    "ExplanationOutcome",
    "ExplanationStatus",
    "InventoryProductionAgent",
    "InventoryService",
    "ListLine",
    "OrderDemand",
    "PlanOutcome",
    "PlanStatus",
    "PlanWindow",
    "PlannedTask",
    "Problem",
    "ProblemKind",
    "ProductionPlanner",
    "ProductionService",
    "ReceiptConflict",
    "ReceiptInput",
    "ReceiptRejected",
    "ReleaseOutcome",
    "ReleaseStatus",
    "ScheduleProposal",
    "ScheduleRelease",
    "ScheduleState",
    "ScheduleView",
    "ShortageExplainer",
    "ShortageExplanation",
    "StockCheckError",
    "StockCheckResult",
    "StockLevel",
    "StockReceipt",
    "Violation",
    "ViolationKind",
    "check_explanation",
]
