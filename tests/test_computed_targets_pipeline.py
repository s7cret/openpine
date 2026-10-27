"""Apply the accepted preparation/research/recovery scenarios to computed targets."""
from unittest.mock import patch
import test_prepared_requests_pipeline as historical
import test_prepared_requests_live as live
original_source=historical.source
original_live=live.code

def computed_source(v):
    api='security' if v<5 else 'request.security'
    return original_source(v).replace(f'{api}(sym,tf,',f'{api}(sym+"",tf=="1" ? "1" : tf+"",')

def computed_live(v):
    api='security' if v<5 else 'request.security'
    return original_live(v).replace(f'{api}(sym,tf,',f'{api}(sym+"",tf=="1" ? "1" : tf+"",')

class ComputedTargetPipelineTests(historical.PreparedRequestPipelineTests):
    def setUp(self):
        guard=patch.object(historical,'source',computed_source);guard.start();self.addCleanup(guard.stop)

class ComputedTargetLiveTests(live.PreparedRequestLiveTests):
    def setUp(self):
        guard=patch.object(live,'code',computed_live);guard.start();self.addCleanup(guard.stop)
