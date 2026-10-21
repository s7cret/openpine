import copy
import tempfile
import unittest
from pathlib import Path

from packages.compiler.semantic.specification import ROOT
from packages.contracts import OpenPineError
from packages.contracts.marketdata import DataQuery
from packages.marketdata import data_identity
from packages.marketdata.providers import FileProvider, SnapshotStore, snapshot_payload, snapshot_from_payload


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = FileProvider(ROOT / "examples/pipeline/bars.json")
        self.query = DataQuery("TEST:PIPELINE", "1", 0, 240000)

    def test_immutable_snapshot_roundtrip_and_half_open_range(self):
        snapshot = self.provider.fetch(self.query)
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            path = store.save(snapshot)
            before = path.read_bytes()
            self.assertEqual(store.load(snapshot.sha256), snapshot)
            store.save(snapshot)
            self.assertEqual(path.read_bytes(), before)
        selected = self.provider.fetch(DataQuery("TEST:PIPELINE", "1", 60000, 180000))
        self.assertEqual(tuple(b.time for b in selected.data.bars), (60000, 120000))
        self.assertNotEqual(data_identity(snapshot.data).sha256, data_identity(selected.data).sha256)

    def test_reject_mismatch_empty_or_invalid_range(self):
        for query in (DataQuery("wrong", "1", 0, 1), DataQuery("TEST:PIPELINE", "1", 1, 2),
                      DataQuery("TEST:PIPELINE", "1", True, 4), DataQuery("TEST:PIPELINE", "1", 4, 4)):
            with self.subTest(query=query), self.assertRaises(OpenPineError):
                self.provider.fetch(query)

    def test_snapshot_tamper_is_rejected(self):
        payload = snapshot_payload(self.provider.fetch(self.query))
        for mutate in (lambda p: p["data"]["bars"][0].update(close=10.5),
                       lambda p: p["provenance"].update(provider="different"),
                       lambda p: p.update(extra=True)):
            forged = copy.deepcopy(payload)
            mutate(forged)
            with self.assertRaises(OpenPineError):
                snapshot_from_payload(forged)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OpenPineError):
                SnapshotStore(directory).load("../outside")
