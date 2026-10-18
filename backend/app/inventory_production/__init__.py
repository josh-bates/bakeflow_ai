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

__all__ = ["InventoryService", "Problem", "ProblemKind", "StockCheckError", "StockCheckResult"]
