"""Install the same-origin cookie API and fail-closed API permission boundary."""
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import authenticated, owner_path
from app.auth.service import AuthService, COOKIE_NAME, SESSION_SECONDS, csrf_token
from app.db.session import get_sessionmaker


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class LoginThrottle:
    """Small per-process guard for the local prototype; no account enumeration."""
    def __init__(self):
        self.attempts = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, peer: str) -> bool:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.attempts.pop(peer, []) if now - t < 60]
            allowed = len(recent) < 20
            if allowed:
                recent.append(now)
            self.attempts[peer] = recent
            if len(self.attempts) > 1000:
                self.attempts.popitem(last=False)
            return allowed


def install_auth(app, settings):
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path == "/api/auth/login":
            return JSONResponse({"detail": "Enter a valid email and password."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    router = APIRouter()
    throttle = LoginThrottle()
    service = AuthService(get_sessionmaker(settings.database_url)) if settings.database_url else None
    # Secure cookies are mandatory for deployment. Local HTTP is only for loopback demos.
    secure_cookie = settings.environment not in {"development", "test"}

    def available() -> AuthService:
        if service is None:
            raise HTTPException(503, "Sign-in service unavailable.")
        return service

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        origin = request.headers.get("origin")
        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        if unsafe and (request.headers.get("sec-fetch-site") == "cross-site" or
                       (origin and origin != str(request.base_url).rstrip("/"))):
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403, headers={"Cache-Control": "no-store"})
        public = path in {"/api/health", "/api/health/ready", "/api/auth/login"}
        try:
            if not public:
                from starlette.concurrency import run_in_threadpool
                token = request.cookies.get(COOKIE_NAME)
                identity = await run_in_threadpool(available().resolve, token)
                if identity is None:
                    return JSONResponse({"detail": "Please sign in."}, status_code=401, headers={"Cache-Control": "no-store"})
                request.state.identity = identity
                request.state.csrf_token = csrf_token(token)
                if owner_path(path) and identity.role != "Owner":
                    return JSONResponse({"detail": "This operation requires the bakery owner."}, status_code=403, headers={"Cache-Control": "no-store"})
                if unsafe and not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(), request.state.csrf_token.encode()):
                    return JSONResponse({"detail": "Please refresh your session."}, status_code=403, headers={"Cache-Control": "no-store"})
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except SQLAlchemyError:
            response = JSONResponse({"detail": "The account service is unavailable. Please try again."}, status_code=503)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/api/auth/login", include_in_schema=False)
    def login_page():
        return RedirectResponse("/#/login", status_code=303)

    @router.post("/api/auth/login", status_code=204)
    def login(body: LoginBody, request: Request, response: Response):
        # Custom header plus JSON makes cross-site browser login require a CORS
        # preflight, which this same-origin app never grants.
        if request.headers.get("x-requested-with") != "BakeFlow":
            raise HTTPException(403, "Please use the sign-in form.")
        peer = request.client.host if request.client else "unknown"
        if not throttle.allow(peer):
            raise HTTPException(429, "Too many sign-in attempts. Please try again in a minute.")
        token = available().login(body.email.strip().casefold(), body.password.get_secret_value(), request.cookies.get(COOKIE_NAME))
        if token is None:
            raise HTTPException(401, "Email or password is incorrect.")
        response.set_cookie(COOKIE_NAME, token, httponly=True, secure=secure_cookie,
                            samesite="lax", max_age=SESSION_SECONDS, path="/")
        response.headers["Cache-Control"] = "no-store"

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        available().logout(request.cookies[COOKIE_NAME])
        response.delete_cookie(COOKIE_NAME, path="/", secure=secure_cookie, httponly=True, samesite="lax")

    @router.get("/api/session")
    def session(request: Request, identity=Depends(authenticated)):
        return {"user": {"displayName": identity.display_name, "role": identity.role},
                "mode": settings.llm_mode, "csrfToken": request.state.csrf_token}

    app.include_router(router)
