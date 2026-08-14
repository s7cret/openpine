from __future__ import annotations

import hmac
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from openpine.gateway.config import GatewayConfig
from openpine.gateway.deps import get_state
from openpine.gateway.schemas import (
    BacktestRunRequest,
    CompareTvRequest,
    DataBackfillRequest,
    LiveStartRequest,
    OptimizerDryRunRequest,
    PaperStartRequest,
    PineSourceUpdate,
    ReplayRequest,
    StrategyCreate,
    StrategyUpdate,
)
from openpine.gateway.server import create_app
from openpine.gateway.security import audit_and_secure_request


def _client(config: GatewayConfig | None = None) -> TestClient:
    app = create_app(config or GatewayConfig())
    app.dependency_overrides[get_state] = lambda: SimpleNamespace()
    return TestClient(app)


def test_gateway_auth_token_is_optional_and_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENPINE_API_TOKEN", raising=False)
    assert GatewayConfig().auth_token is None

    monkeypatch.setenv("OPENPINE_API_TOKEN", "production-token")
    assert GatewayConfig().auth_token == "production-token"
    assert GatewayConfig(auth_token="config-token").auth_token == "config-token"


def test_production_gateway_config_requires_bearer_token() -> None:
    with pytest.raises(ValueError, match="OPENPINE_API_TOKEN"):
        GatewayConfig(environment="production", auth_token=None)
    with pytest.raises(ValueError, match="must not be blank"):
        GatewayConfig(environment="production", auth_token="")
    with pytest.raises(ValueError, match="must not be blank"):
        GatewayConfig(auth_token="   ")

    config = GatewayConfig(environment="production", auth_token="configured-secret")
    assert config.auth_token is not None


def test_unhandled_errors_keep_request_id_and_security_headers() -> None:
    app = FastAPI()
    app.state.gateway_config = GatewayConfig()
    app.middleware("http")(audit_and_secure_request)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal failure")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"]


def test_health_stays_public_when_api_auth_is_configured() -> None:
    response = _client(GatewayConfig(auth_token="test-token")).get("/health")

    assert response.status_code == 200


def test_production_public_health_omits_runtime_details() -> None:
    response = _client(
        GatewayConfig(environment="production", auth_token="configured-secret")
    ).get("/health")

    assert response.status_code == 200
    assert "runtime" not in response.json()


def test_production_disables_public_openapi_and_docs() -> None:
    client = _client(
        GatewayConfig(environment="production", auth_token="configured-secret")
    )

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong-token"}, 401),
        ({"Authorization": "Basic test-token"}, 401),
        ({"Authorization": "Bearer test-token"}, 200),
    ],
)
def test_configured_bearer_token_protects_api_operations(
    headers: dict[str, str], expected_status: int
) -> None:
    response = _client(GatewayConfig(auth_token="test-token")).get(
        "/api/version", headers=headers
    )

    assert response.status_code == expected_status
    if expected_status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_token_comparison_uses_constant_time_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    calls: list[tuple[str | bytes, str | bytes]] = []
    real_compare_digest = hmac.compare_digest

    def watched_compare_digest(
        supplied: str | bytes, configured: str | bytes
    ) -> bool:
        calls.append((supplied, configured))
        return real_compare_digest(supplied, configured)

    monkeypatch.setattr(security.hmac, "compare_digest", watched_compare_digest)

    response = _client(GatewayConfig(auth_token="test-token")).get(
        "/api/version", headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401
    assert calls == [("wrong-token", "test-token")]


def test_openapi_declares_bearer_security_for_api_operations_only() -> None:
    schema = create_app(GatewayConfig(auth_token="test-token")).openapi()

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert schema["x-openpine-websocket-paths"] == ["/api/ws/events"]
    assert schema["paths"]["/api/version"]["get"]["security"] == [
        {"BearerAuth": []}
    ]
    assert "security" not in schema["paths"]["/health"]["get"]


def test_security_and_request_id_headers_are_added_to_denied_responses() -> None:
    response = _client(GatewayConfig(auth_token="test-token")).get(
        "/api/version", headers={"X-Request-ID": "request-123"}
    )

    assert response.status_code == 401
    assert response.headers["x-request-id"] == "request-123"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_access_log_is_structured_and_does_not_trust_forwarded_client_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        security,
        "log",
        SimpleNamespace(info=lambda event, **fields: events.append((event, fields))),
    )

    response = _client().get(
        "/health",
        headers={
            "X-Forwarded-For": "203.0.113.99",
            "X-Request-ID": "audit-request-1",
            "User-Agent": "security-pack-test",
        },
    )

    assert response.status_code == 200
    event, fields = events[-1]
    assert event == "gateway_access"
    assert fields["client_ip"] == "testclient"
    assert fields["client_ip"] != "203.0.113.99"
    assert fields["method"] == "GET"
    assert fields["path"] == "/health"
    assert fields["status"] == 200
    assert isinstance(fields["duration_ms"], float)
    assert fields["duration_ms"] >= 0
    assert fields["request_id"] == "audit-request-1"
    assert fields["user_agent"] == "security-pack-test"
    assert fields["authenticated"] is False


def test_access_log_records_successful_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        security,
        "log",
        SimpleNamespace(info=lambda event, **fields: events.append((event, fields))),
    )

    response = _client(GatewayConfig(auth_token="test-token")).get(
        "/api/version",
        headers={
            "Authorization": "Bearer test-token",
            "X-Request-ID": "authenticated-request",
        },
    )

    assert response.status_code == 200
    access = [fields for event, fields in events if event == "gateway_access"][-1]
    assert access["status"] == 200
    assert access["request_id"] == "authenticated-request"
    assert access["authenticated"] is True


def test_forwarded_ip_is_used_only_for_explicitly_trusted_proxy_chain() -> None:
    from openpine.gateway.security import resolve_client_ip

    trusted = ("127.0.0.0/8", "10.0.0.0/8")
    assert (
        resolve_client_ip("127.0.0.1", "203.0.113.9, 10.0.0.12", trusted)
        == "203.0.113.9"
    )
    assert resolve_client_ip("192.168.1.20", "203.0.113.9", trusted) == "192.168.1.20"
    assert resolve_client_ip("127.0.0.1", "not-an-ip", trusted) == "127.0.0.1"


def test_access_log_has_normalized_route_principal_outcome_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        security,
        "log",
        SimpleNamespace(info=lambda event, **fields: events.append((event, fields))),
    )
    config = GatewayConfig(auth_token="test-token", auth_principal="operator")
    response = _client(config).get(
        "/api/version?token=query-secret",
        headers={
            "Authorization": "Bearer test-token",
            "Cookie": "session=cookie-secret",
            "User-Agent": "security-pack-test",
        },
    )

    assert response.status_code == 200
    access = [fields for event, fields in events if event == "gateway_access"][-1]
    assert access["socket_ip"] == "testclient"
    assert access["client_ip"] == "testclient"
    assert access["route"] == "/api/version"
    assert access["path"] == "/api/version"
    assert access["principal"] == "operator"
    assert access["auth_outcome"] == "authenticated"
    assert str(access["timestamp"]).endswith("Z")
    serialized = repr(access)
    assert "query-secret" not in serialized
    assert "cookie-secret" not in serialized
    assert "test-token" not in serialized


def test_denied_access_log_records_missing_auth_without_credential_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        security,
        "log",
        SimpleNamespace(info=lambda event, **fields: events.append((event, fields))),
    )
    response = _client(GatewayConfig(auth_token="test-token")).get("/api/version")

    assert response.status_code == 401
    access = [fields for event, fields in events if event == "gateway_access"][-1]
    assert access["principal"] is None
    assert access["auth_outcome"] == "missing"


def test_configured_auth_rejects_unauthorized_websocket() -> None:
    client = _client(GatewayConfig(auth_token="test-token"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/events"):
            pass


def test_websocket_credential_subprotocol_is_never_echoed() -> None:
    client = _client(GatewayConfig(auth_token="test-token"))
    credential_protocol = "openpine.bearer.b64.dGVzdC10b2tlbg"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/ws/events", subprotocols=[credential_protocol]
        ):
            pass


@pytest.mark.parametrize(
    "subprotocols",
    [
        None,
        ["openpine.events.v1"],
        ["openpine.bearer.b64.dGVzdC10b2tlbg"],
    ],
)
def test_websocket_authorization_header_cannot_bypass_transport_contract(
    subprotocols: list[str] | None,
) -> None:
    client = _client(GatewayConfig(auth_token="test-token"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/ws/events",
            headers={"Authorization": "Bearer test-token"},
            subprotocols=subprotocols,
        ):
            pass


def test_websocket_selects_only_safe_credential_free_subprotocol() -> None:
    client = _client(GatewayConfig(auth_token="test-token"))
    credential_protocol = "openpine.bearer.b64.dGVzdC10b2tlbg"
    safe_protocol = "openpine.events.v1"
    with client.websocket_connect(
        "/api/ws/events", subprotocols=[credential_protocol, safe_protocol]
    ) as websocket:
        assert websocket.accepted_subprotocol == safe_protocol
        assert websocket.accepted_subprotocol != credential_protocol


def test_websocket_auth_is_audited_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.gateway import security

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        security,
        "log",
        SimpleNamespace(
            info=lambda event, **fields: events.append((event, fields)),
            error=lambda *_args, **_kwargs: None,
        ),
    )
    client = _client(GatewayConfig(auth_token="websocket-secret"))

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/events"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/ws/events", headers={"Authorization": "Bearer websocket-secret"}
        ):
            pass
    credential_protocol = "openpine.bearer.b64.d2Vic29ja2V0LXNlY3JldA"
    with client.websocket_connect(
        "/api/ws/events",
        subprotocols=[credential_protocol, "openpine.events.v1"],
    ) as websocket:
        assert websocket.accepted_subprotocol == "openpine.events.v1"

    websocket_events = [
        fields
        for event, fields in events
        if event == "gateway_access" and fields.get("method") == "WEBSOCKET"
    ]
    assert [event["status"] for event in websocket_events] == [401, 401, 101]
    assert [event["auth_outcome"] for event in websocket_events] == [
        "missing",
        "missing",
        "authenticated",
    ]
    assert "websocket-secret" not in repr(websocket_events)


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"symbol": ""},
        {"timeframe": "   "},
        {"exchange": ""},
        {"market_type": "   "},
        {"params_json": "not-json"},
        {"params_json": "[]"},
        {"params_json": "null"},
        {"unexpected": "field"},
    ],
)
def test_strategy_update_rejects_empty_or_invalid_fields(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        StrategyUpdate.model_validate(updates)


@pytest.mark.parametrize(
    "params_json",
    ["not-json", "[]", "null", '"scalar"', "1"],
)
def test_strategy_create_rejects_non_object_params_json(params_json: str) -> None:
    with pytest.raises(ValidationError):
        StrategyCreate(
            name="demo",
            pine_id="pine-1",
            artifact_id="artifact-1",
            symbol="BTCUSDT",
            timeframe="1m",
            params_json=params_json,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": None},
        {"source_text": ""},
        {"source_text": "   "},
        {"source_text": None},
        {"source_type": "script"},
        {"unexpected": "field"},
    ],
)
def test_pine_source_update_rejects_empty_or_invalid_fields(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PineSourceUpdate.model_validate(updates)


def test_update_schemas_still_accept_boolean_only_updates() -> None:
    assert StrategyUpdate(enabled=False).enabled is False
    assert PineSourceUpdate(archived=True).archived is True


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (PaperStartRequest, {"strategy_id": ""}),
        (LiveStartRequest, {"strategy_id": "   "}),
        (
            DataBackfillRequest,
            {"symbol": "", "timeframe": "", "from_time": "", "to_time": ""},
        ),
        (OptimizerDryRunRequest, {"strategy_id": "", "trials": 1}),
        (ReplayRequest, {"from_date": ""}),
        (CompareTvRequest, {"openpine_plots_path": "", "tv_chart_path": ""}),
    ],
)
def test_control_plane_request_models_reject_blank_identifiers(
    model: type, payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"warmup_bars": -1},
        {"initial_capital": 0},
        {"initial_capital": -1},
        {"initial_capital": float("nan")},
        {"initial_capital": float("inf")},
        {"initial_capital": float("-inf")},
        {"params_override": []},
        {"unexpected": "field"},
    ],
)
def test_backtest_request_rejects_unsafe_values(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "strategy_id": "strategy-1",
        "from_time": "1",
        "to_time": "2",
        **overrides,
    }

    with pytest.raises(ValidationError):
        BacktestRunRequest.model_validate(payload)


@pytest.mark.parametrize("limit", [0, -1, 501, 1000])
def test_backtest_run_list_rejects_limit_outside_bounds(limit: int) -> None:
    response = _client().get(f"/api/backtest/runs?limit={limit}")

    assert response.status_code == 422


def test_backtest_run_list_accepts_limit_boundaries() -> None:
    seen: list[int] = []
    store = SimpleNamespace(
        list_all_runs=lambda *, limit: seen.append(limit) or [],
        list_runs=lambda strategy_id, *, limit: seen.append(limit) or [],
    )
    app = create_app(GatewayConfig())
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(
        backtest_store=store,
        strategy_registry=SimpleNamespace(),
    )
    client = TestClient(app)

    assert client.get("/api/backtest/runs?limit=1").status_code == 200
    assert client.get("/api/backtest/runs?limit=500").status_code == 200
    assert seen == [1, 500]
