"""Runtime settings routes for OpenPine Gateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from openpine.config.model import OpenPineConfig, SUPPORTED_MARKETDATA_TIMEFRAMES
from openpine.gateway.deps import GatewayState, get_state
from openpine.timezones import resolve_timezone

router = APIRouter(tags=["settings"])


def _settings_payload(config: OpenPineConfig) -> dict[str, object]:
    tz = resolve_timezone(config.timezone)
    return {
        "timezone": config.timezone,
        "timezone_label": tz.label,
        "marketdata": {
            "stable_quotes_only": bool(config.marketdata_stable_quotes_only),
            "stable_quote_assets": list(config.marketdata_stable_quote_assets),
            "symbol_search_limit": int(config.marketdata_symbol_search_limit),
            "timeframes": list(config.marketdata_timeframes),
            "default_timeframe": config.marketdata_default_timeframe,
            "supported_timeframes": list(SUPPORTED_MARKETDATA_TIMEFRAMES),
        },
    }


def _raw_config_data(current: OpenPineConfig) -> dict[str, Any]:
    """Load file-backed config without env overrides before persisting settings."""
    path = current.config_path()
    if path.exists():
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="config file must contain an object")
        data = dict(raw)
    else:
        data = current.model_dump(mode="json")
    data.setdefault("workspace_root", str(current.workspace_root))
    data.setdefault("config_dir", str(current.config_dir))
    return data


def _updated_config(current: OpenPineConfig, payload: dict[str, Any]) -> OpenPineConfig:
    data = _raw_config_data(current)
    if "timezone" in payload:
        data["timezone"] = payload["timezone"]

    marketdata = payload.get("marketdata") or {}
    if not isinstance(marketdata, dict):
        raise HTTPException(status_code=422, detail="marketdata must be an object")

    mapping = {
        "stable_quotes_only": "marketdata_stable_quotes_only",
        "stable_quote_assets": "marketdata_stable_quote_assets",
        "symbol_search_limit": "marketdata_symbol_search_limit",
        "timeframes": "marketdata_timeframes",
        "default_timeframe": "marketdata_default_timeframe",
    }
    for external, field_name in mapping.items():
        if external in marketdata:
            data[field_name] = marketdata[external]

    try:
        return OpenPineConfig(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings")
async def get_settings(state: GatewayState = Depends(get_state)) -> dict[str, object]:
    """Return editable product settings that are safe to expose in the UI."""
    return _settings_payload(state.config)


@router.patch("/settings")
async def update_settings(
    payload: dict[str, Any],
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Persist safe runtime settings and update the current gateway state."""
    updated = _updated_config(state.config, payload)
    updated.save()
    from openpine.config.loader import load_config

    state.config = load_config(updated.config_path())
    return _settings_payload(state.config)
