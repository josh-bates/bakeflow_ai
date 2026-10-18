"""Stage 1 core records as SQLAlchemy models (BAKE-7; architecture §5.3).

Importing this package registers every table on ``app.db.Base.metadata``.
Stage 2 extension tables (intake requests, change requests, deductions,
receipts, transitions, AI requests, notifications, mock payments and
publications) are added by the stories that need them.
"""

from app.shared.records._common import (
    ApprovalDecision,
    CustomerDecision,
    OrderStatus,
    Role,
    TaskStatus,
    TransactionKind,
    Versioning,
)
from app.shared.records.accounts import BakerySettings, Customer, UserAccount
from app.shared.records.approvals import ApprovalRequest
from app.shared.records.audit import AuditEntry, ConstraintGuard
from app.shared.records.catalogue import InventoryItem, Product, Recipe, RecipeIngredient
from app.shared.records.finance import Quote, Transaction
from app.shared.records.marketing import Campaign, CampaignProduct, MarketingContent
from app.shared.records.orders import Order, OrderItem
from app.shared.records.production import (
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    ShoppingList,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "AuditEntry",
    "BakerySettings",
    "Campaign",
    "CampaignProduct",
    "ConstraintGuard",
    "Customer",
    "CustomerDecision",
    "InventoryItem",
    "MarketingContent",
    "Order",
    "OrderItem",
    "OrderStatus",
    "ProductionSchedule",
    "ProductionScheduleVersion",
    "ProductionTask",
    "Product",
    "Quote",
    "Recipe",
    "RecipeIngredient",
    "Role",
    "ShoppingList",
    "TaskStatus",
    "Transaction",
    "TransactionKind",
    "UserAccount",
    "Versioning",
]
