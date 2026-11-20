"""Persistent opaque login sessions, shared by HTTP and the media proxy."""

import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool

from vn_stream.api.errors import failure
from vn_stream.api.schemas.envelopes import Success, Timestamp
from vn_stream.api.schemas.serialization import success
from vn_stream.auth.constants import COOKIE, CSRF_COOKIE
from vn_stream.auth.policy import login_url
from vn_stream.auth.repository import AuthStore
from vn_stream.i18n import text
from vn_stream.ui.installation import INSTALL_PATHS


class LoginState(BaseModel):
    authenticated: bool
    username: str
    expires_at: Timestamp
    csrf_token: str


class LogoutState(BaseModel):
    authenticated: bool


NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
SAFE = {"GET", "HEAD", "OPTIONS"}


def install_auth(
    app, state, username, password, *, prefix="", allow_insecure_local=False, web=None
):
    # The generated list grants only immutable assets needed by anonymous login.
    public_assets = None
    if web is not None and (web / "public-assets.json").is_file():
        public_assets = set()
        for path in json.loads((web / "public-assets.json").read_text()):
            if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
                continue
            relative = path.lstrip("/")
            if (
                (
                    path in {"/theme.css", "/ui.css"}
                    or path.startswith(("/chunks/", "/fonts/", "/icons/"))
                )
                and Path(path).suffix in {".css", ".js", ".woff2", ".svg"}
                and (web / relative).is_file()
                and (web / relative).resolve().is_relative_to(web.resolve())
            ):
                public_assets.add(path)
    auth = AuthStore(state, username, password)
    app.state.auth = auth
    router = APIRouter(prefix="/api/v2/auth")

    def secure(request):
        # Only the explicit local HTTP endpoint can set cookies without Secure.
        # Uvicorn trusts the request scheme supplied by its loopback Nginx peer.
        return not (
            allow_insecure_local
            and request.url.scheme == "http"
            and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
        )

    def cookies(response, request, token, session):
        options = dict(secure=secure(request), samesite="lax", path="/")
        if session["remembered"]:
            options["max_age"] = max(0, int(session["expires_at"] - time.time() + 0.999))
        response.set_cookie(COOKIE, token, httponly=True, **options)
        response.set_cookie(CSRF_COOKIE, session["csrf_token"], **options)

    def describe(session):
        return {
            "authenticated": True,
            "username": username,
            "expires_at": session["expires_at"],
            "csrf_token": session["csrf_token"],
        }

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        path = request.url.path
        if path == "/api/v1" or path.startswith("/api/v1/"):
            return failure(request, 404, text("common.requestFailed"), headers=NO_CACHE)
        if request.method not in SAFE:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if (origin is not None and origin != expected) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return failure(
                    request, 403, text("errors.crossSiteRequestsAreNotAllowed"), headers=NO_CACHE
                )
        if path == "/api/v2/auth/login":
            if (
                request.method == "POST"
                and request.headers.get("content-type", "").split(";", 1)[0].lower()
                != "application/json"
            ):
                return failure(
                    request, 415, text("errors.signInUsingAJsonRequest"), headers=NO_CACHE
                )
            response = await call_next(request)
            response.headers.update(NO_CACHE)
            return response
        # Only resources required by the login page are public. No directory or
        # application API is exempted by a broad file-extension test.
        public = (
            path
            in {
                "/login",
                "/login.html",
                "/login.js",
                "/ui.css",
                "/icons/moon.svg",
                "/icons/sun.svg",
            }
            or path in INSTALL_PATHS
            or (path in public_assets if public_assets is not None else False)
        )
        if public and request.method in {"GET", "HEAD"}:
            if path == "/login":
                request.scope["path"] = "/login.html"
            response = await call_next(request)
            response.headers.update(NO_CACHE)
            return response
        token = request.cookies.get(COOKIE)
        session = await run_in_threadpool(auth.lookup, token)
        if not session:
            target = request.headers.get("x-original-uri") or prefix + str(request.url.path) + (
                "?" + request.url.query if request.url.query else ""
            )
            redirect = login_url(prefix, target)
            headers = {**NO_CACHE, "X-Login-URL": redirect}
            if path.startswith("/api/v2/"):
                return failure(request, 401, text("ui.pleaseSignIn"), headers=headers)
            return RedirectResponse(redirect, 302, headers=headers)
        request.state.auth_session = session
        if request.method not in SAFE:
            supplied = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(supplied.encode(), session["csrf_token"].encode()):
                return failure(
                    request, 403, text("errors.refreshThePageAndRetry"), headers=NO_CACHE
                )
        response = await call_next(request)
        if path.startswith("/api/v2/auth/"):
            response.headers.update(NO_CACHE)
        return response

    class Login(BaseModel):
        model_config = ConfigDict(extra="forbid")
        username: str = Field(max_length=256)
        password: str = Field(max_length=4096, repr=False)
        remember: StrictBool = True

    @router.post("/login", response_model=Success[LoginState])
    def login(body: Login, request: Request):
        if not auth.valid_credentials(body.username, body.password):
            return failure(request, 401, text("ui.incorrectUsernameOrPassword"), headers=NO_CACHE)
        auth.revoke(request.cookies.get(COOKIE))
        token, session = auth.issue(body.remember)
        response = JSONResponse(
            Success[LoginState].model_validate(success(describe(session))).model_dump(mode="json"),
            headers=NO_CACHE,
        )
        cookies(response, request, token, session)
        return response

    @router.get("/session", response_model=Success[LoginState])
    def session(request: Request):
        session = request.state.auth_session
        response = JSONResponse(
            Success[LoginState].model_validate(success(describe(session))).model_dump(mode="json"),
            headers=NO_CACHE,
        )
        if request.cookies.get(CSRF_COOKIE) != session["csrf_token"]:
            cookies(response, request, request.cookies[COOKIE], session)
        return response

    @router.get("/verify", status_code=204, include_in_schema=False)
    def verify():
        return Response(status_code=204, headers=NO_CACHE)

    @router.post("/logout", response_model=Success[LogoutState])
    def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE))
        response = JSONResponse(success({"authenticated": False}), headers=NO_CACHE)
        for name in [COOKIE, CSRF_COOKIE]:
            response.delete_cookie(
                name, path="/", secure=secure(request), httponly=name == COOKIE, samesite="lax"
            )
        return response

    app.include_router(router)
