"""Reproduce pinned versioned trade statistics and numeric trade readers."""
import argparse
from packages.contracts.performance import policy,apis,NAMES,NOTE
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-performance-20261026.1'
def prepare():
 lock=read_json(ROOT/'spec/lock.json');versions={}
 for v in range(1,7):
  ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
  versions[str(v)]={'profile_id':f'openpine.performance.v{v}.1','policy':policy(v),'apis':apis(v),'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in ref['entries'] if e['name'] in NAMES]}
 data={'schema_version':'openpine.performance-spec.1','base_manifest_sha256':lock['manifest_sha256'],'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
  'review':{'date':'2026-10-26','external_pine_execution':False,'basis':NOTE,'sources':['https://www.tradingview.com/pine-script-docs/concepts/strategies/#individual-trade-information','https://www.tradingview.com/pine-script-reference/v6/'],
   'decisions':['Eight aggregate variables have separate evidence for each v1-v6; numeric trade-reader functions are only admitted in v5/v6. Early semantics are reconstructed.',
    'Closed trades are allocated closed lot slices in existing journal order. They are distinct from fills, order IDs, fully closed lots and flat-position episodes.',
    'All profit observations and win/loss classification use net profit after allocated fees. Grossprofit/grossloss partition closed net results. Remaining entry fees belong to openprofit. Exact ledger equity equals initial capital plus closed net profit plus open profit.',
    'Reference wording and example fee subtraction do not uniquely establish every historical commission convention; the net convention is an explicit local model decision and requires separate external validation.',
    'Closed indices remain stable from zero without trimming. Open indices enumerate surviving lots in entry order. Invalid/missing numeric indices return na; no coercion or future data is used.',
    'The immutable output journal is authoritative. A disposable SQLite lookup with bounded cache is rebuilt from its committed prefix, including the unsealed tail. Checkpoints keep only counters and a hash chain and validate against that prefix before broker assignment.',
    'Observations become visible after current-bar broker fills and before primary script evaluation. Requested indicator contexts have no trading state. Historical execution only; no tick-equivalence, margin or FX is claimed.'],
   'test_refs':['tests/test_performance.py','tests/test_performance_pipeline.py','conformance/performance/cases.json']}}
 files={'performance.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.performance-manifest.1','release':RELEASE,'files':{n:sha(b) for n,b in files.items()}});return files
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
 if target.exists():
  if file_map(target)!={n:sha(b) for n,b in files.items()}:raise SpecError('SPEC_IMMUTABLE','Trade performance release differs.')
 elif a.check:raise SpecError('SPEC_IMPORT','Trade performance release missing.')
 else:
  target.mkdir()
  for n,b in files.items():(target/n).write_bytes(b)
 print('Immutable trade performance release verified.')
if __name__=='__main__':main()
