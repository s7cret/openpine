"""Opt-in pytest gate: exact collection identity; skip/xfail never counts as pass.

Used explicitly with -p, including when third-party plugin auto-loading is off.
Collect-only can write a proposed inventory but cannot satisfy execution acceptance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def collection_hash(nodeids: list[str]) -> str:
    data = json.dumps(sorted(nodeids), ensure_ascii=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_inventory(nodeids: list[str], expected: dict, deselected: int) -> None:
    if not nodeids or len(set(nodeids)) != len(nodeids):
        raise ValueError("empty or duplicate test inventory")
    if (
        expected.get("count") != len(nodeids)
        or expected.get("sha256") != collection_hash(nodeids)
        or expected.get("deselected", 0) != deselected
    ):
        raise ValueError("test inventory changed; explicit review/rebaseline required")


def pytest_addoption(parser):
    group = parser.getgroup("openpine-verification")
    group.addoption("--verification-lock", default=None)
    group.addoption("--verification-suite", default=None)
    group.addoption("--verification-output", default=None)


def pytest_configure(config):
    if config.getoption("--verification-output"):
        config.pluginmanager.register(InventoryGate(config), "openpine-inventory-gate")


class InventoryGate:
    def __init__(self, config):
        self.config = config
        self.nodes = []
        self.deselected = 0
        self.reports = {}
        self.errors = []

    def pytest_deselected(self, items):
        self.deselected += len(items)

    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]
        lock = self.config.getoption("--verification-lock")
        if lock:
            try:
                expected = json.loads(Path(lock).read_text())[
                    self.config.getoption("--verification-suite")
                ]
                validate_inventory(self.nodes, expected, self.deselected)
            except (KeyError, ValueError) as error:
                self.errors.append(str(error))

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, []).append(
            {
                "when": report.when,
                "outcome": report.outcome,
                "xfail": bool(getattr(report, "wasxfail", False)),
            }
        )

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        collect = bool(self.config.option.collectonly)
        if not collect:
            for node in self.nodes:
                rows = self.reports.get(node, [])
                if (
                    len(rows) != 3
                    or {r["when"] for r in rows} != {"setup", "call", "teardown"}
                    or any(r["outcome"] != "passed" or r["xfail"] for r in rows)
                ):
                    self.errors.append("required test did not pass all phases: " + node)
        result = {
            "schema_id": "openpine.test_inventory.v1",
            "suite": self.config.getoption("--verification-suite"),
            "count": len(self.nodes),
            "sha256": collection_hash(self.nodes),
            "nodeids": sorted(self.nodes),
            "deselected": self.deselected,
            "collect_only": collect,
            "errors": self.errors,
            "ok": not collect and not self.errors and exitstatus == 0,
        }
        path = Path(self.config.getoption("--verification-output"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n")
        if self.errors:
            session.exitstatus = 1
