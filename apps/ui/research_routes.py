"""Reports and reviewed deletion over the common research services."""
import re
from apps.openpine.research_reports import ResearchReports
from apps.openpine.research_maintenance import ResearchMaintenance


def post(handler, path, payload):
    match = re.fullmatch('/api/research/(search|experiment|walkforward)/([0-9a-f]{32})/(preview|delete)', path)
    if not match:return False
    kind, identifier, action = match.groups()
    service = ResearchMaintenance(handler.server.jobs)
    if action == 'preview':
        if payload != {}:raise ValueError('Deletion preview uses the saved composition.')
        value = service.preview(kind, identifier)
    else:
        if type(payload) is not dict or payload.keys() != {'preview_id'}:raise ValueError('Deletion requires its current preview_id.')
        value = service.apply(kind, identifier, payload['preview_id'])
    handler.send(200, value)
    return True
