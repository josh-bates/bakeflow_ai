"""Password verification and persistent, revocable opaque cookie sessions."""
import hashlib
import secrets
from datetime import datetime, UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.auth.access import Identity
from app.auth.models import AuthSession
from app.shared.passwords import hash_password, verify_password
from app.shared.records import UserAccount, Customer

COOKIE_NAME = "bakeflow_session"
SESSION_SECONDS = 8 * 60 * 60


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hashlib.sha256(("bakeflow-csrf:" + token).encode()).hexdigest()


class AuthService:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions
        # Unknown accounts take the same password-verification path as known ones.
        self.dummy_password_hash = hash_password(secrets.token_urlsafe(32))

    @staticmethod
    def identity(db, user: UserAccount) -> Identity | None:
        if user.role == "Owner":
            return Identity(user.user_id, user.role, "Bakery owner", None)
        if user.role == "Customer":
            customer = db.scalar(select(Customer).where(Customer.user_id == user.user_id))
            if customer:
                return Identity(user.user_id, user.role, customer.name, customer.customer_id)
        return None

    def login(self, email: str, password: str, previous_token: str | None = None) -> str | None:
        with self.sessions.begin() as db:
            user = db.scalar(select(UserAccount).where(UserAccount.email == email))
            valid = verify_password(password, user.password_hash if user else self.dummy_password_hash)
            if not valid or user is None or self.identity(db, user) is None:
                return None
            # Rotate on successful login, including switching accounts in one browser.
            if previous_token:
                previous = db.get(AuthSession, digest(previous_token))
                if previous:
                    db.delete(previous)
            token = secrets.token_urlsafe(32)
            db.add(AuthSession(token_hash=digest(token), user_id=user.user_id,
                               expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS)))
        return token

    def resolve(self, token: str | None) -> Identity | None:
        if token is None or len(token) != 43:
            return None
        with self.sessions() as db:
            session = db.get(AuthSession, digest(token))
            if session is None:
                return None
            expires = session.expires_at
            # SQLite tests omit timezone metadata; PostgreSQL stores UTC-aware values.
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                return None
            user = db.get(UserAccount, session.user_id)
            return self.identity(db, user) if user else None

    def logout(self, token: str) -> None:
        with self.sessions.begin() as db:
            session = db.get(AuthSession, digest(token))
            if session:
                db.delete(session)
