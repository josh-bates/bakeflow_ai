"""Marketing module (AR-04: UC-07, UC-08).

Home of ``MarketingAgent`` and ``MarketingService`` (architecture §6.1).

BAKE-25 provides ``MarketingService.summarise_business``, the validated
business summary that UC-07 step 2 hands to the agent.
"""

from app.marketing.campaigns import (
    LOW_CONFIDENCE_NOTICE,
    MAX_FEATURED_PRODUCTS,
    MIN_CONFIDENCE,
    CampaignPlanner,
    CampaignProposal,
    MarketingAgent,
    PlanOutcome,
    PlanStatus,
)
from app.marketing.summary import (
    MIN_LEDGER_TRANSACTIONS,
    SOLD_STATUSES,
    BusinessSummary,
    CapacityPosition,
    MarketingService,
    ProductDemand,
    StockPosition,
)

__all__ = [
    "LOW_CONFIDENCE_NOTICE",
    "MAX_FEATURED_PRODUCTS",
    "MIN_CONFIDENCE",
    "MIN_LEDGER_TRANSACTIONS",
    "SOLD_STATUSES",
    "BusinessSummary",
    "CampaignPlanner",
    "CampaignProposal",
    "CapacityPosition",
    "MarketingAgent",
    "MarketingService",
    "PlanOutcome",
    "PlanStatus",
    "ProductDemand",
    "StockPosition",
]
