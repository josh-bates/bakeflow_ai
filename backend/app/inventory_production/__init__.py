"""Inventory and Production module (AR-02, IP-01–IP-05: UC-03, UC-04).

Home of ``InventoryProductionAgent``, ``InventoryService`` and
``ProductionService`` (architecture §6.1), the schedule release (BAKE-21),
task progress with its once-only deduction (BAKE-33) and the low-stock alerts
(BAKE-37).
"""

from app.inventory_production.inventory import (
    InventoryService,
    Problem,
    ProblemKind,
    ReceiptConflict,
    ReceiptInput,
    ReceiptRejected,
    ReorderLevelRejected,
    StockCheckError,
    StockCheckResult,
    StockLevel,
    StockReceipt,
)
from app.inventory_production.low_stock import (
    LowStockExplainer,
    LowStockOutcome,
    LowStockStatus,
    check_low_stock_explanation,
)
from app.inventory_production.production import (
    OrderDemand,
    PlannedTask,
    PlanWindow,
    ProductionService,
    Violation,
    ViolationKind,
)
from app.inventory_production.progress import (
    Deduction,
    DeductedLine,
    ProgressOutcome,
    ProgressRefused,
    TaskProgress,
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
    LowStockExplanation,
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
    "DeductedLine",
    "Deduction",
    "ExcludedOrder",
    "ExplanationOutcome",
    "ExplanationStatus",
    "InventoryProductionAgent",
    "InventoryService",
    "ListLine",
    "LowStockExplainer",
    "LowStockExplanation",
    "LowStockOutcome",
    "LowStockStatus",
    "OrderDemand",
    "PlanOutcome",
    "PlanStatus",
    "PlanWindow",
    "PlannedTask",
    "Problem",
    "ProblemKind",
    "ProductionPlanner",
    "ProductionService",
    "ProgressOutcome",
    "ProgressRefused",
    "ReceiptConflict",
    "ReceiptInput",
    "ReceiptRejected",
    "ReorderLevelRejected",
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
    "TaskProgress",
    "Violation",
    "ViolationKind",
    "check_explanation",
    "check_low_stock_explanation",
]
