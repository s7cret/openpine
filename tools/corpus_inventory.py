"""Rebuild owned corpus pins and independent expectations; never execute Pine."""
import hashlib
import json
from decimal import Decimal
from tools.specification import ROOT


def inventory():
    def record(name):
        return {'path': name, 'sha256': hashlib.sha256((ROOT/name).read_bytes()).hexdigest()}
    cases = []
    for family in ('pipeline', 'inputs', 'strategy', 'unsupported'):
        for version in range(1, 7):
            directory = 'strategies' if family == 'strategy' else family
            source = (f'conformance/contexts/v{version}-unsupported-expression.pine' if family == 'unsupported'
                      else f'examples/{directory}/v{version}' + ('-filtered' if family == 'strategy' else '') + '.pine')
            files = {'source': record(source)}
            expected = {'status': 'compiled'}
            oracle = None
            if family == 'unsupported':
                expected = {'status': 'rejected', 'category': 'PROFILE_UNCOVERED', 'code': 'REQUEST_SCOPE'}
            else:
                files['bars'] = record(f'examples/{directory}/' + ('wave.json' if family == 'strategy' else 'bars.json'))
                if family == 'pipeline':
                    oracle = {'reason': 'Independent cumulative sums, SMA2 and separate function-call state; examples/pipeline/expected.json.',
                              'outputs': [[10.0,40.0,85.0,135.0], [1.0,3.0,4.0,5.0], [0.0,10.0,20.0,30.0]]}
                elif family == 'inputs':
                    files['input_config'] = record('examples/inputs/overrides.json')
                    oracle = {'reason': 'length=4 times scale=2; selected source=open at enabled=true and label=A.',
                              'outputs': [[8.0]*6, [9.0,10.0,11.0,12.0,13.0,14.0]]}
                else:
                    files['context_config'] = record('examples/strategies/inputs.json')
                    files['higher'] = record('examples/strategies/higher.json')
                    fee = Decimal(12)/100
                    cash = Decimal(1000)-12-fee
                    equity = [1000.0]*8 if version <= 2 else [1000.0]*6 + [float(cash+14),float(cash+10)]
                    oracle = {'reason': 'v1/v2 lookahead-on filters the bar5 cross; v3-v6 lookahead-off admits it, fill at bar6 open=12 with 1% commission; mark-to-market at close.',
                              'outputs': [equity]}
            equivalent = family in ('pipeline', 'inputs')
            cases.append({'id': f'{family}-v{version}', 'version': version, 'family': family, 'files': files,
                'inputs': {'length':4,'scale':2,'src':'open'} if family == 'inputs' else {},
                'expected': expected, 'oracle': oracle,
                'history_replay': {'equivalent': equivalent, 'reason': 'Primary close-only indicator with identical confirmed bars.' if equivalent
                                  else 'Multi-context historical lookahead and replay event/broker models are distinct.' if family == 'strategy'
                                  else 'Known unsupported context expression; compilation only.'}})
    return {'schema_version': 'openpine.corpus.1', 'provenance': 'Owned OpenPine examples and pinned conformance/contexts REQUEST_SCOPE rejections; manual finite oracles, no TradingView capture.', 'cases': cases}


def main():
    path = ROOT/'examples/corpus/manifest.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(inventory(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


if __name__ == '__main__':main()
