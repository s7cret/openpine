"""Run the primary-context lifecycle suite with pure UDF targets, without copies."""
import unittest
from unittest.mock import patch
import test_primary_targets_pipeline as primary
from tools.function_targets_spec import source

class FunctionTargetPipelineTests(primary.PrimaryTargetPipelineTests):
    def setUp(self):
        guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

def live_source(v):
    text=primary.relative_live(v);api='security' if v<5 else 'request.security';symbol='tickerid' if v<4 else 'syminfo.tickerid'
    lines=text.splitlines();lines.insert(2,'identity(value) => value')
    return '\n'.join(lines).replace(f'{api}({symbol},tf,',f'{api}(identity({symbol}),identity(tf),')+'\n'

class FunctionTargetLiveTests(primary.PrimaryTargetLiveTests):
    def setUp(self):
        guard=patch.object(primary.live,'code',live_source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
