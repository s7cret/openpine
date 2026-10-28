"""Real workers, research and live subscriptions with changing-window state."""
from unittest.mock import patch
import unittest
import test_primary_targets_pipeline as primary
from tools.dynamic_signals_spec import source

class DynamicSignalPipelineTests(primary.PrimaryTargetPipelineTests):
    def setUp(self):
        guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

def live_source(v):
    p='' if v<5 else 'ta.'
    return primary.relative_live(v).replace(',close)',f',close+{p}highestbars(close,bar_index%3+1))')

class DynamicSignalLiveTests(primary.PrimaryTargetLiveTests):
    def setUp(self):
        guard=patch.object(primary.live,'code',live_source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
