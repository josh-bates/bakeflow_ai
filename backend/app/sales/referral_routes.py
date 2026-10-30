"""Authenticated HTTP boundary for the owner exception queue."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import authenticated, require_owner
from app.db.session import get_sessionmaker
from app.sales.intake import IntakeError
from app.sales.referrals import ReferralService, Resolution, view
from app.sales.routes import check_csrf, current_customer
from app.shared.records import InventoryItem, OwnerReferral, Product, Recipe
from app.shared.store import StaleVersionError, StoreError, ValidatedRecordStore


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1)


class TextBody(VersionBody):
    text: str = Field(min_length=1, max_length=2000)


class ResolveBody(VersionBody):
    resolution: Resolution


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["Refund", "InProductionChange"]
    orderId: uuid.UUID
    message: str = Field(min_length=1, max_length=4000)


def make_router(settings, *, store=None):
    router = APIRouter()

    def service():
        if store is not None:
            return ReferralService(store)
        if not settings.database_url:
            raise HTTPException(503, "Database not configured.")
        return ReferralService(ValidatedRecordStore(get_sessionmaker(settings.database_url)))

    def call(method, *args):
        try:
            return method(*args)
        except IntakeError as exc:
            raise HTTPException(404 if exc.code == "NOT_FOUND" else 409, exc.message) from None
        except StaleVersionError:
            raise HTTPException(409, "This request changed. Refresh before continuing.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "The review could not be saved or loaded. Refresh before retrying.") from None

    @router.get("/api/exception-requests")
    def listing(identity=Depends(authenticated)):
        def read():
            with service().store.unit_of_work() as db:
                query = select(OwnerReferral).order_by(OwnerReferral.created_at.desc())
                if identity.role != "Owner":
                    query = query.where(OwnerReferral.customer_id == identity.customer_id)
                return {"requests": [view(r) for r in db.scalars(query)]}
        return call(read)

    @router.get("/api/owner/exception-catalogue")
    def catalogue(identity=Depends(require_owner)):
        def read():
            with service().store.unit_of_work() as db:
                names = {i.item_id: i.ingredient_name for i in db.scalars(select(InventoryItem))}
                recipes = {r.product_id: r for r in db.scalars(select(Recipe))}
                return {"products": [{"productId": str(p.product_id), "name": p.name,
                                      "recipeId": str(recipes[p.product_id].recipe_id),
                                      "recipeVersion": recipes[p.product_id].version,
                                      "ingredients": [names[i.inventory_item_id] for i in recipes[p.product_id].ingredients]}
                                     for p in db.scalars(select(Product)) if p.product_id in recipes]}
        return call(read)

    @router.post("/api/exception-requests", dependencies=[Depends(check_csrf)])
    def create(body: CreateBody, customer=Depends(current_customer)):
        return call(lambda: service().create(customer, body.message, kind=body.kind, order_id=body.orderId))

    @router.post("/api/owner/exception-requests/{request_id}/question", dependencies=[Depends(check_csrf)])
    def ask(request_id: uuid.UUID, body: TextBody, owner=Depends(require_owner)):
        return call(service().ask, request_id, body.version, owner.user_id, body.text)

    @router.post("/api/owner/exception-requests/{request_id}/resolve", dependencies=[Depends(check_csrf)])
    def resolve(request_id: uuid.UUID, body: ResolveBody, owner=Depends(require_owner)):
        return call(service().resolve, request_id, body.version, owner.user_id, body.resolution)

    @router.post("/api/exception-requests/{request_id}/reply", dependencies=[Depends(check_csrf)])
    def reply(request_id: uuid.UUID, body: TextBody, customer=Depends(current_customer)):
        return call(service().reply, request_id, body.version, customer, body.text)

    @router.post("/api/exception-requests/{request_id}/confirm", dependencies=[Depends(check_csrf)])
    def confirm(request_id: uuid.UUID, body: VersionBody, customer=Depends(current_customer)):
        return call(service().confirm, request_id, body.version, customer)

    return router
