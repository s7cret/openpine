"""Same installed acceptance contract with pure UDF target sources and recovery."""
from tools.installed_primary_targets_smoke import verify as verify_primary
from tools.specification import ROOT

def verify(http,cli,work):
    return verify_primary(http,cli,work,root=ROOT/'examples/function-targets',flow='function_targets_flow',
        prefix='function_targets',expected_profile='openpine.function-targets.1')
