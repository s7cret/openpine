"""Actual HTTP scalar-target pending/resolved launch lifecycle."""
import unittest
from unittest.mock import patch
import test_primary_targets_http as http
import test_primary_targets_pipeline as primary
from tools.scalar_targets_spec import source

class ScalarTargetHTTPTests(http.PrimaryTargetHTTPTests):
    def setUp(self):
        super().setUp();guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
