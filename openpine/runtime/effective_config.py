"""Immutable evidence for the one resolved RC6 configuration.

The engine keeps its established mutable execution object. This snapshot is the
immutable authority at admission; a new mapping is returned to every caller.
Legacy callers cannot reconstruct pre-boundary provenance: it is marked submitted,
never falsely attributed to a Pine declaration or a user setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from openpine.verification.identity import canonical, seal, verify

SCHEMA = "openpine.effective_strategy_config.v1"


@dataclass(frozen=True, slots=True)
class EffectiveStrategyConfig:
    _encoded: bytes

    @classmethod
    def capture(
        cls, resolved: Mapping[str, Any], submitted: Mapping[str, Any], context: Mapping[str, Any]
    ) -> "EffectiveStrategyConfig":
        from openpine.runtime.rc6_config import effective_config_hash

        settings = {k: v for k, v in resolved.items() if k != "effective_config_hash"}
        provenance = {}
        for name, value in settings.items():
            chain = [{"source": "submitted", "value": submitted[name]}] if name in submitted else []
            if name == "mintick" and "mintick" in context:
                chain.append({"source": "admitted_instrument", "value": context[name]})
            if not chain:
                chain.append({"source": "engine_default", "value": value})
            elif canonical(chain[-1]["value"]) != canonical(value):
                chain.append({"source": "engine_normalization", "value": value})
            provenance[name] = chain
        value = seal(
            {
                "schema_id": SCHEMA,
                "settings": settings,
                "settings_hash": effective_config_hash(settings),
                "provenance": provenance,
            }
        )
        return cls(canonical(value))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._encoded)

    def evidence(self) -> dict[str, Any]:
        """Compact receipt; large request datasets are not duplicated in results."""
        value = self.to_dict()
        return seal(
            {
                "schema_id": "openpine.effective_config_receipt.v1",
                "snapshot_hash": value["content_hash"],
                "settings_hash": value["settings_hash"],
                "provenance": {
                    name: [row["source"] for row in chain]
                    for name, chain in value["provenance"].items()
                },
                "upstream_provenance": "unresolved_before_admission",
            }
        )

    @property
    def settings_hash(self) -> str:
        return self.to_dict()["settings_hash"]

    def assert_matches(self, config: Any) -> None:
        from openpine.runtime.rc6_config import serialize_engine_config

        actual = serialize_engine_config(config, config.semantic_profile)
        if actual["effective_config_hash"] != self.settings_hash:
            raise ValueError("resolved strategy config changed after admission")

    @classmethod
    def parse(cls, value: dict) -> "EffectiveStrategyConfig":
        from openpine.runtime.rc6_config import effective_config_hash

        body = verify(value, SCHEMA)
        if set(body) != {"schema_id", "settings", "settings_hash", "provenance"}:
            raise ValueError("effective configuration fields mismatch")
        if body["settings_hash"] != effective_config_hash(body["settings"]):
            raise ValueError("effective settings hash mismatch")
        if set(body["settings"]) != set(body["provenance"]):
            raise ValueError("effective provenance is incomplete")
        for name, chain in body["provenance"].items():
            if (
                not isinstance(chain, list)
                or not chain
                or any(
                    not isinstance(row, dict)
                    or set(row) != {"source", "value"}
                    or row["source"]
                    not in {
                        "submitted",
                        "admitted_instrument",
                        "engine_default",
                        "engine_normalization",
                    }
                    for row in chain
                )
            ):
                raise ValueError("effective provenance chain is invalid")
            if canonical(chain[-1]["value"]) != canonical(body["settings"][name]):
                raise ValueError("effective provenance terminal value differs from settings")
        return cls(canonical(value))
