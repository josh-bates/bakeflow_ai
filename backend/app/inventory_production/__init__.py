"""Inventory and Production module (AR-02, IP-01–IP-05: UC-03, UC-04).

Home of ``InventoryProductionAgent``, ``InventoryService`` and
``ProductionService`` (architecture §6.1).
"""

from app.inventory_production.inventory import (
    InventoryService,
    Problem,
    ProblemKind,
    StockCheckError,
    StockCheckResult,
)
from app.inventory_production.production import (
    OrderDemand,
    PlannedTask,
    PlanWindow,
    ProductionService,
    Violation,
    ViolationKind,
)
from app.inventory_production.scheduling import (
    ExcludedOrder,
    InventoryProductionAgent,
    PlanOutcome,
    PlanStatus,
    ProductionPlanner,
    ScheduleProposal,
)

__all__ = [
    "ExcludedOrder",
    "InventoryProductionAgent",
    "InventoryService",
    "OrderDemand",
    "PlanOutcome",
    "PlanStatus",
    "PlanWindow",
    "PlannedTask",
    "Problem",
    "ProblemKind",
    "ProductionPlanner",
    "ProductionService",
    "ScheduleProposal",
    "StockCheckError",
    "StockCheckResult",
    "Violation",
    "ViolationKind",
]
