"""Stage 1 core records as SQLAlchemy models (BAKE-7; architecture §5.3).

Importing this package registers every table on ``app.db.Base.metadata``.
Stage 2 extension tables (intake requests, change requests, deductions,
transitions, AI requests, notifications and publications) are added by the
stories that need them; BAKE-32 adds ingredient receipts, BAKE-33 order
deductions, BAKE-37 AI requests [R5] and BAKE-39 mock payments.
"""

from app.shared.records._common import (
    ApprovalDecision,
    CustomerDecision,
    OrderStatus,
    PaymentStatus,
    Role,
    TaskStatus,
    TransactionKind,
    Versioning,
)
from app.shared.records.accounts import BakerySettings, Customer, UserAccount
from app.shared.records.ai_requests import AIRequest, AIRequestStatus
from app.shared.records.approvals import ApprovalRequest
from app.shared.records.audit import AuditEntry, ConstraintGuard
from app.shared.records.catalogue import InventoryItem, Product, Recipe, RecipeIngredient
from app.shared.records.deductions import OrderDeduction, OrderDeductionLine
from app.shared.records.finance import MockPayment, Quote, Transaction
from app.shared.records.marketing import Campaign, CampaignProduct, MarketingContent
from app.shared.records.notifications import Notification, WorkflowTransition
from app.shared.records.orders import Order, OrderItem
from app.shared.records.production import (
    ProductionSchedule,
    ProductionScheduleVersion,
    ProductionTask,
    ShoppingList,
)
from app.shared.records.receipts import InventoryReceipt
from app.shared.records.referrals import OwnerReferral

__all__ = [
    "AIRequest",
    "AIRequestStatus",
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
    "InventoryReceipt",
    "MarketingContent",
    "Notification",
    "WorkflowTransition",
    "Order",
    "OwnerReferral",
    "OrderDeduction",
    "OrderDeductionLine",
    "OrderItem",
    "OrderStatus",
    "ProductionSchedule",
    "ProductionScheduleVersion",
    "ProductionTask",
    "Product",
    "MockPayment",
    "PaymentStatus",
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
