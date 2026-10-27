"""Closed list of read-only POST projections; creation/control are never included."""
import re

def read_only_post(path):
    return path in ('/api/launches/requirements','/api/launches/history-spec') or re.fullmatch(r'/api/launches/[0-9a-f]{32}/research-base',path) is not None
