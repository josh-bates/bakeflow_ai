"""Marketing module (AR-04: UC-07, UC-08).

Home of ``MarketingAgent`` and ``MarketingService`` (architecture §6.1).

BAKE-25 provides ``MarketingService.summarise_business``, the validated
business summary that UC-07 step 2 hands to the agent.
"""

from app.marketing.summary import (
    CLOSED_STATUSES,
    MIN_LEDGER_TRANSACTIONS,
    SOLD_STATUSES,
    BusinessSummary,
    CapacityPosition,
    MarketingService,
    ProductDemand,
    StockPosition,
)

__all__ = [
    "CLOSED_STATUSES",
    "MIN_LEDGER_TRANSACTIONS",
    "SOLD_STATUSES",
    "BusinessSummary",
    "CapacityPosition",
    "MarketingService",
    "ProductDemand",
    "StockPosition",
]
