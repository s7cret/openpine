"""Workers, research and live with numeric targets and requested math formulas."""
import unittest
from unittest.mock import patch
import test_primary_targets_pipeline as primary
from tools.numeric_targets_spec import source

class NumericTargetPipelineTests(primary.PrimaryTargetPipelineTests):
    def setUp(self):
        guard=patch.object(primary,'source',source);guard.start();self.addCleanup(guard.stop)

def live_source(v):
    text=primary.relative_live(v);req='security' if v<5 else 'request.security';symbol='tickerid' if v<4 else 'syminfo.tickerid';fmt='tostring' if v<5 else 'str.tostring';p='' if v<5 else 'math.'
    lines=text.splitlines();lines.insert(2,f'identity(value) => value+{fmt}({p}round(0.0),"#")=="never" ? "2" : value')
    return '\n'.join(lines).replace(f'{req}({symbol},tf,',f'{req}(identity({symbol}),identity(tf),')+'\n'

class NumericTargetLiveTests(primary.PrimaryTargetLiveTests):
    def setUp(self):
        guard=patch.object(primary.live,'code',live_source);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
