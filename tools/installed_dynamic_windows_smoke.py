"""Installed dynamic windows: all versions, private markets and durable state."""
from tools.installed_primary_targets_smoke import verify as primary_verify
from tools.specification import ROOT

def verify(http,cli,work):
    return primary_verify(http,cli,work,root=ROOT/'examples/dynamic-windows',flow='dynamic_windows_flow',prefix='dynamic_windows')
