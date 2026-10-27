"""Installed scalar target v1-v6 CLI/HTTP and recovery qualification."""
from tools.specification import ROOT
from tools.installed_primary_targets_smoke import verify as verify_primary

def verify(http,cli,work):
    return verify_primary(http,cli,work,root=ROOT/'examples/scalar-targets',flow='scalar_targets_flow',
        prefix='scalar_targets',expected_profile='openpine.scalar-targets.1')
