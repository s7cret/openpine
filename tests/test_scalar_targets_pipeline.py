"""Real primary workers, research variants and live state with scalar targets."""
import unittest
from unittest.mock import patch
import test_primary_targets_pipeline as primary
from tools.scalar_targets_spec import source

class ScalarTargetPipelineTests(primary.PrimaryTargetPipelineTests):
    def setUp(self):
        guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

def live_source(v):
    text=primary.relative_live(v);api='security' if v<5 else 'request.security';symbol='tickerid' if v<4 else 'syminfo.tickerid';name='tostring' if v<5 else 'str.tostring'
    lines=text.splitlines();lines.insert(2,f'identity(value) => value+{name}(0,"#")=="never" ? "2" : value')
    return '\n'.join(lines).replace(f'{api}({symbol},tf,',f'{api}(identity({symbol}),identity(tf),')+'\n'

class ScalarTargetLiveTests(primary.PrimaryTargetLiveTests):
    def setUp(self):
        guard=patch.object(primary.live,'code',live_source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
