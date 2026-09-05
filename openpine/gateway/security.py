"""Gateway authentication, access auditing, and response hardening."""

from __future__ import annotations

import base64
import hmac
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import structlog

log = structlog.get_logger(__name__)

SAFE_WEBSOCKET_SUBPROTOCOL = "openpine.events.v1"

class _ConnectionHTTPBearer(HTTPBearer):
    """HTTP bearer parser that also accepts FastAPI WebSocket connections."""

    async def __call__(
        self, connection: HTTPConnection
    ) -> HTTPAuthorizationCredentials | None:
        if connection.scope.get("type") == "websocket":
            offered_protocols = tuple(
                protocol.strip()
                for protocol in connection.headers.get(
                    "sec-websocket-protocol", ""
                ).split(",")
                if protocol.strip()
            )
            if SAFE_WEBSOCKET_SUBPROTOCOL not in offered_protocols:
                return None
            for value in offered_protocols:
                prefix = "openpine.bearer.b64."
                if not value.startswith(prefix):
                    continue
                encoded = value.removeprefix(prefix)
                try:
                    padding = "=" * (-len(encoded) % 4)
                    token = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return None
                if token:
                    connection.state.selected_websocket_subprotocol = (
                        SAFE_WEBSOCKET_SUBPROTOCOL
                    )
                    return HTTPAuthorizationCredentials(
                        scheme="Bearer", credentials=token
                    )
            return None
        authorization = connection.headers.get("Authorization")
        scheme, credentials = get_authorization_scheme_param(authorization)
        if authorization and scheme.lower() == "bearer":
            return HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)
        return None


_BEARER = _ConnectionHTTPBearer(auto_error=False, scheme_name="BearerAuth")
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


def api_auth_dependency(
    configured_token: str | None, principal: str = "lan-operator"
):
    """Build an optional bearer check while retaining its OpenAPI scheme."""

    async def authenticate_api(
        connection: HTTPConnection,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(_BEARER)
        ],
    ) -> None:
        if configured_token is None:
            connection.state.auth_outcome = "not_required"
            return
        if credentials is None:
            connection.state.auth_outcome = "missing"
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not hmac.compare_digest(credentials.credentials, configured_token):
            connection.state.auth_outcome = "invalid"
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        connection.state.authenticated = True
        connection.state.auth_outcome = "authenticated"
        connection.state.principal = principal

    return authenticate_api


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and len(supplied) <= 128 and supplied.isprintable():
        return str(supplied)
    return uuid.uuid4().hex


def resolve_client_ip(
    socket_ip: str, forwarded_for: str | None, trusted_proxy_cidrs: tuple[str, ...]
) -> str:
    """Resolve XFF only when every step starts at an explicitly trusted peer."""

    try:
        peer = ip_address(socket_ip)
        networks = tuple(ip_network(value, strict=False) for value in trusted_proxy_cidrs)
    except ValueError:
        return socket_ip
    if not any(peer in network for network in networks) or not forwarded_for:
        return socket_ip
    try:
        hops = [ip_address(value.strip()) for value in forwarded_for.split(",")]
    except ValueError:
        return socket_ip
    if not hops:
        return socket_ip
    candidate = peer
    for hop in reversed(hops):
        if not any(candidate in network for network in networks):
            break
        candidate = hop
    return str(candidate)


class WebSocketAuditMiddleware:
    """Emit the same bounded access metadata for WebSocket handshakes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        state = scope.setdefault("state", {})
        supplied_request_id = headers.get("x-request-id", "").strip()
        request_id = (
            supplied_request_id
            if supplied_request_id
            and len(supplied_request_id) <= 128
            and supplied_request_id.isprintable()
            else uuid.uuid4().hex
        )
        state["request_id"] = request_id
        state.setdefault("authenticated", False)
        state.setdefault("auth_outcome", "not_required")
        state.setdefault("principal", None)
        started = time.perf_counter()
        status = 500

        async def audited_send(message: Message) -> None:
            nonlocal status
            message_type = message["type"]
            if message_type == "websocket.accept":
                status = 101
            elif message_type == "websocket.http.response.start":
                status = int(message.get("status", 500))
            elif message_type == "websocket.close" and status == 500:
                status = 403
            await send(message)

        try:
            await self.app(scope, receive, audited_send)
        finally:
            client = scope.get("client")
            socket_ip = str(client[0]) if client else "unknown"
            app = scope.get("app")
            config = getattr(getattr(app, "state", None), "gateway_config", None)
            trusted_proxy_cidrs = getattr(config, "trusted_proxy_cidrs", ())
            client_ip = resolve_client_ip(
                socket_ip,
                headers.get("x-forwarded-for"),
                trusted_proxy_cidrs,
            )
            route = getattr(scope.get("route"), "path", None) or "<unmatched>"
            user_agent = (
                headers.get("user-agent", "")
                .replace("\n", " ")
                .replace("\r", " ")[:256]
            )
            log.info(
                "gateway_access",
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                socket_ip=socket_ip,
                client_ip=client_ip,
                method="WEBSOCKET",
                route=route,
                path=route,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                request_id=request_id,
                user_agent=user_agent,
                authenticated=bool(state.get("authenticated", False)),
                principal=state.get("principal"),
                auth_outcome=state.get("auth_outcome", "unknown"),
            )


async def audit_and_secure_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Audit direct-client request metadata and add baseline security headers."""

    request_id = _request_id(request)
    request.state.request_id = request_id
    request.state.authenticated = False
    request.state.auth_outcome = "not_required"
    request.state.principal = None
    started = time.perf_counter()
    status = 500
    try:
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - outer HTTP error boundary
            log.error(
                "gateway_unhandled_error",
                request_id=request_id,
                error_type=exc.__class__.__name__,
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error", "request_id": request_id},
            )
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response
    finally:
        socket_ip = request.client.host if request.client is not None else "unknown"
        config = getattr(request.app.state, "gateway_config", None)
        trusted_proxy_cidrs = getattr(config, "trusted_proxy_cidrs", ())
        client_ip = resolve_client_ip(
            socket_ip,
            request.headers.get("X-Forwarded-For"),
            trusted_proxy_cidrs,
        )
        route = getattr(request.scope.get("route"), "path", None) or "<unmatched>"
        user_agent = request.headers.get("User-Agent", "").replace("\n", " ").replace("\r", " ")[:256]
        log.info(
            "gateway_access",
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            socket_ip=socket_ip,
            client_ip=client_ip,
            method=request.method,
            route=route,
            path=route,
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            request_id=request_id,
            user_agent=user_agent,
            authenticated=bool(getattr(request.state, "authenticated", False)),
            principal=getattr(request.state, "principal", None),
            auth_outcome=getattr(request.state, "auth_outcome", "unknown"),
        )
