import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.contracts.wire import atomic_json, loads, read_json


class WireTests(unittest.TestCase):
    def test_reject_duplicates_nonfinite_and_limits(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff', b'[' * 2000):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                loads(raw)
        with self.assertRaises(ValueError):
            loads(b'"long"', max_bytes=3)

    def test_atomic_status_replacement_with_concurrent_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            atomic_json(path, {"completed": 0})
            stopped, errors = threading.Event(), []
            def reader():
                try:
                    while not stopped.is_set():
                        self.assertIsInstance(read_json(path)["completed"], int)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=reader)
            thread.start()
            try:
                for i in range(80):
                    atomic_json(path, {"completed": i})
            finally:
                stopped.set(); thread.join(timeout=5)
            self.assertEqual(errors, [])
            self.assertEqual(read_json(path)["completed"], 79)

    def test_held_reader_keeps_old_file_after_writer_publishes_new_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "состояние-🌲.json"
            atomic_json(path, {"completed": 0})
            opened, release = threading.Event(), threading.Event()
            observed, errors = [], []
            def blocked_loads(raw, **kwargs):
                opened.set()
                if not release.wait(10):
                    raise TimeoutError("Reader was not released.")
                return loads(raw, **kwargs)
            def reader():
                try:
                    observed.append(read_json(path))
                except Exception as error:
                    errors.append(error)
            with patch("packages.contracts.wire.loads", side_effect=blocked_loads):
                thread = threading.Thread(target=reader)
                thread.start()
                try:
                    self.assertTrue(opened.wait(5))
                    atomic_json(path, {"completed": 1})
                    self.assertEqual(loads(path.read_bytes()), {"completed": 1})
                finally:
                    release.set(); thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(observed, [{"completed": 0}])
