"""Installed worker, saved IR and chart access for a durable moving label."""
import time
import uuid

from packages.contracts.wire import canonical
from tools.specification import ROOT


def verify(http, cli, work):
    fixtures = ROOT / 'conformance/labels'
    bars = fixtures / 'bars.json'
    snapshot = http('/api/snapshots', {'provider': 'upload', 'raw': bars.read_text(encoding='utf-8')})['sha256']

    def finish(job, wanted):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = http('/api/jobs/' + job)
            if state['status'] == wanted:
                return state
            if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)

    anchor = {'id': 2, 'x': 1, 'y': 10., 'text': 'anchor'}
    for version in (4, 5, 6):
        source = fixtures / f'v{version}-moving.pine'
        compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.as_posix()})
        assert compiled['status'] == 'compiled', compiled
        launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
            'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
            'inputs': {}, 'timeout_seconds': 120, 'gap_policy': 'fail', 'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
        assert launch['status'] == 'ready', launch
        job = http(f"/api/launches/{launch['launch_id']}/start", {})['job_id']
        finish(job, 'paused')
        http(f'/api/jobs/{job}/resume', {})
        state = finish(job, 'succeeded')
        result = http(f'/api/jobs/{job}/result')['result']
        assert state['resumed_from_bar'] == 2 and result['outputs'] == [], state
        assert result['labels'] == [{'id': 1, 'x': 3, 'y': 40., 'text': 'late'}, anchor], result
        chart = http(f'/api/jobs/{job}/chart?start=1&limit=1')
        assert chart['labels'] == [anchor], chart
        assert http(f'/api/jobs/{job}/chart?start=2&limit=1')['labels'] == []
        events = http(f'/api/jobs/{job}/label_events?start=4&limit=4')
        assert (events['total'], events['next']) == (12, 8), events
        assert [row['event']['action'] for row in events['events']] == ['update', 'update', 'create', 'delete']
        assert events['events'][-1]['event'] == {'action': 'delete', 'id': 3, 'reason': 'explicit'}
        if version == 6:
            artifact = work / 'labels-v6.json'
            cli('compile', '--source', source, '--profile', 'historical', '--output', artifact)
            contexts = work / 'labels-contexts.json'
            contexts.write_bytes(canonical({'schema_version': 'openpine.context-inputs.1',
                'primary_context': result['primary_context'], 'contexts': []}))
            direct = cli('job', '--artifact', artifact, '--bars', bars, '--contexts', contexts,
                         '--state-dir', work / 'labels-cli')
            assert direct['response']['result'] == result, 'Saved IR and resumed HTTP label results differ.'
    return {'label_versions': [4, 5, 6], 'label_resume_bar': 2, 'label_events': 12,
            'label_window_carries_older_creation': True, 'label_saved_ir_equal': True}
