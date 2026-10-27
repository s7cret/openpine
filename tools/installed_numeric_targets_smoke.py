"""Installed numeric targets and formula recovery, using the shared lifecycle."""
from tools.specification import ROOT
from tools.installed_primary_targets_smoke import verify as verify_primary

def verify(http,cli,work):
    return verify_primary(http,cli,work,root=ROOT/'examples/numeric-targets',flow='numeric_targets_flow',
        prefix='numeric_targets',expected_profile='openpine.numeric-targets.1')
