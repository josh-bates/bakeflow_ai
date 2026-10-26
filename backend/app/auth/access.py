"""Server-owned identity and permissions for current and future API modules."""
from dataclasses import dataclass
import uuid
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Identity:
    user_id: uuid.UUID
    role: str
    display_name: str
    customer_id: uuid.UUID | None


def authenticated(request: Request) -> Identity:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(401, "Please sign in.")
    return identity


def require_owner(request: Request) -> Identity:
    identity = authenticated(request)
    if identity.role != "Owner":
        raise HTTPException(403, "This operation requires the bakery owner.")
    return identity


# Route families reserved for owner-only operations. Future handlers must also
# declare Depends(require_owner), especially when added outside these paths.
OWNER_PREFIXES = ("/api/owner", "/api/approvals", "/api/inventory", "/api/finance", "/api/marketing", "/api/schedules")


def owner_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in OWNER_PREFIXES)
