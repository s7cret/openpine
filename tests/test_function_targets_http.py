"""Actual HTTP pending/resolved requirements with UDF target closures."""
import unittest
from unittest.mock import patch
import test_primary_targets_http as http
import test_primary_targets_pipeline as primary
from tools.function_targets_spec import source

class FunctionTargetHTTPTests(http.PrimaryTargetHTTPTests):
    def setUp(self):
        super().setUp();guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
