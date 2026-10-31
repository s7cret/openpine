"""Pinned, owned whole-program corpus. Loading never rewrites a source file."""
import hashlib
import re
from pathlib import Path, PurePosixPath

from packages.contracts.wire import read_json, loads, digest
from packages.compiler.semantic.specification import ROOT

MANIFEST = ROOT / 'examples/corpus/manifest.json'
LIMIT = 128
RUN_LIMIT = 64


def file_bytes(root, record):
    if type(record) is not dict or record.keys() != {'path', 'sha256'}:
        raise ValueError('Invalid corpus file record.')
    name = record['path']
    if type(name) is not str or '\\' in name or ':' in name:
        raise ValueError('Invalid corpus path.')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Corpus paths must stay inside the project.')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Corpus path escapes the project.')
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise ValueError('Corpus file size/hash differs: ' + name)
    return raw


class Corpus:
    def __init__(self, manifest=MANIFEST, *, root=ROOT):
        self.root = Path(root).resolve()
        self.manifest = read_json(manifest, max_bytes=256 * 1024)
        value = self.manifest
        if (type(value) is not dict or value.keys() != {'schema_version', 'provenance', 'cases'}
                or value['schema_version'] not in ('openpine.corpus.1','openpine.corpus.2')
                or not isinstance(value['provenance'], str) or len(value['provenance']) > 2048
                or type(value['cases']) is not list or not 1 <= len(value['cases']) <= LIMIT):
            raise ValueError('Invalid corpus manifest.')
        self.cases = {}
        self.files = {}
        for case in value['cases']:
            names = {'id', 'version', 'family', 'files', 'inputs', 'expected', 'oracle', 'history_replay'}
            if value['schema_version']=='openpine.corpus.2':names.add('execution')
            if (type(case) is not dict or case.keys() != names
                    or type(case['id']) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,63}', case['id'])
                    or case['id'] in self.cases or type(case['version']) is not int or not 1 <= case['version'] <= 6
                    or case['family'] not in ('pipeline','inputs','strategy','unsupported','request_environment','scope','roundtrip','pending','partial','intrabar','lower','semantic-routing','semantic-strategy','overload-routing','overload-strategy','method-routing','method-strategy','object-routing','object-strategy','object-queue-routing','object-queue-strategy','enum-routing','enum-strategy')
                    or type(case['files']) is not dict or 'source' not in case['files']
                    or set(case['files']) - {'source', 'bars', 'context_config', 'higher', 'input_config'}
                    or type(case['inputs']) is not dict or len(case['inputs']) > 64
                    or type(case['history_replay']) is not dict
                    or case['history_replay'].keys() != {'equivalent', 'reason'}
                    or type(case['history_replay']['equivalent']) is not bool
                    or not isinstance(case['history_replay']['reason'], str)
                    or not 1 <= len(case['history_replay']['reason']) <= 1024):
                raise ValueError('Invalid corpus case.')
            expected = case['expected']
            if (type(expected) is not dict or expected.get('status') not in ('compiled', 'rejected')
                    or expected.keys() != ({'status'} if expected['status'] == 'compiled' else {'status', 'category', 'code'})):
                raise ValueError('Invalid expected compilation.')
            if expected['status'] == 'compiled' and ('bars' not in case['files'] or type(case['oracle']) is not dict
                    or case['oracle'].keys() != {'reason', 'outputs'} or type(case['oracle']['outputs']) is not list):
                raise ValueError('Executable cases require data and an independent output oracle.')
            if expected['status'] == 'rejected' and case['oracle'] is not None:
                raise ValueError('Rejected cases cannot claim execution coverage.')
            files = {key: file_bytes(self.root, record) for key, record in case['files'].items()}
            if 'execution' in case:
                config=case['execution']
                if (type(config) is not dict or set(config)-{'pause_after','split_after','tape','replay_options','replay_outputs','checkpoint','statistics'}
                        or config.get('tape','close') not in ('close','ohlc3')
                        or any(type(config[k]) is not int or config[k]<1 for k in ('pause_after','split_after') if k in config)
                        or config.get('checkpoint') not in (None,'pending','position','partial')
                        or type(config.get('replay_options',{})) is not dict
                        or set(config.get('replay_options',{}))-{'calculation_profile','broker_profile','lower_tf'}):
                    raise ValueError('Invalid corpus execution configuration.')
            source = files['source'].decode('utf-8')
            match = re.search(r'^//@version=(\d+)\s*$', source, re.M)
            if match is None or int(match[1]) != case['version']:
                raise ValueError('Corpus language version differs from pinned source.')
            for key in ('bars', 'higher'):
                if key in files:
                    data = loads(files[key])
                    if type(data.get('bars')) is not list or not 2 <= len(data['bars']) <= 4096:
                        raise ValueError('Corpus data must contain 2..4096 bars.')
            self.cases[case['id']] = case
            self.files[case['id']] = files
        self.sha256 = digest(value)

    def select(self, ids=None, *, run=False):
        ids = list(self.cases) if ids is None else ids
        if (type(ids) is not list or not 1 <= len(ids) <= LIMIT or any(type(i) is not str for i in ids)
                or len(set(ids)) != len(ids) or any(i not in self.cases for i in ids)):
            raise ValueError('Select distinct known corpus case IDs.')
        if run and len(ids)>RUN_LIMIT:raise ValueError('Select at most 64 programs per run; use a catalogue batch.')
        return [self.cases[i] for i in ids]

    def batches(self):
        ids=list(self.cases)
        return [ids[i:i+RUN_LIMIT] for i in range(0,len(ids),RUN_LIMIT)]

    def catalogue(self):
        return {'schema_version': 'openpine.corpus-catalogue.1', 'manifest_sha256': self.sha256,
                'provenance': self.manifest['provenance'], 'cases': list(self.cases.values()),
                'run_batch_limit':RUN_LIMIT,'batches':self.batches(),
                'scope': 'Owned application corpus; no TradingView equivalence claim.'}
