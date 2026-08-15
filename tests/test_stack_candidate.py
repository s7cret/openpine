from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "stack-candidate-5.0.0-rc.1.json"
PIN = "51e32ebaaf02eecb81443e8ca7e89b2543cb25a3"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = {
    "openpine-contracts",
    "marketdata-provider",
    "pinelib",
    "backtest_engine",
    "pine2ast",
    "ast2python",
    "optimizer",
    "openpine",
}


def test_candidate_manifest_pins_eight_repos_and_is_not_a_release() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["schema"] == "openpine.stack-candidate.v1"
    assert payload["id"] == "5.0.0-rc.1"
    assert payload["not_a_release"] is True
    assert payload["contracts_pin"] == PIN
    components = payload["components"]
    assert set(components) == REQUIRED
    assert components["openpine-contracts"]["sha"] == PIN
    for name, row in components.items():
        if name == "openpine" and row["sha"] == "THIS_CHECKOUT":
            continue
        assert SHA40.fullmatch(row["sha"]), name
    lock = json.loads(
        (ROOT / "openpine" / "stack-lock.json").read_text(encoding="utf-8")
    )
    assert lock != payload
