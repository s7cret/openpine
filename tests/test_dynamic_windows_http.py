"""Real HTTP preparation and execution of changing-window programs."""
import unittest
from unittest.mock import patch
import test_primary_targets_http as http
import test_primary_targets_pipeline as primary
from tools.dynamic_windows_spec import source

class DynamicWindowHTTPTests(http.PrimaryTargetHTTPTests):
    def setUp(self):
        super().setUp();guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
