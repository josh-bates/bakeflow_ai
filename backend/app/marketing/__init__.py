"""Marketing module (AR-04: UC-07, UC-08).

Home of ``MarketingAgent`` and ``MarketingService`` (architecture §6.1).

BAKE-25 provides ``MarketingService.summarise_business``, the validated
business summary that UC-07 step 2 hands to the agent.
"""

from app.marketing.summary import (
    MIN_LEDGER_TRANSACTIONS,
    BusinessSummary,
    CapacityPosition,
    MarketingService,
    StockPosition,
)

__all__ = [
    "MIN_LEDGER_TRANSACTIONS",
    "BusinessSummary",
    "CapacityPosition",
    "MarketingService",
    "StockPosition",
]
