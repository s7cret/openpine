"""Reproduce the immutable artifact-defined global risk companion."""
import argparse
from packages.contracts.risk import risk_policy,risk_apis,NAMES,NOTE,source_review
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-risk-20261025.1'
def prepare():
 lock=read_json(ROOT/'spec/lock.json');versions={}
 for v in range(1,7):
  ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
  versions[str(v)]={'profile_id':f'openpine.risk.v{v}.1','policy':risk_policy(v),'apis':risk_apis(v),'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in ref['entries'] if e['name'] in NAMES]}
 data={'schema_version':'openpine.risk-spec.1','base_manifest_sha256':lock['manifest_sha256'],'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],
 'versions':versions,'review':{'date':'2026-10-25','external_pine_execution':False,'basis':NOTE,'sources':[source_review()],
 'decisions':['Global literal declarations activate for the whole evaluated strategy, regardless of source order. Duplicate kinds and dynamic/conditional declarations are rejected.',
 'One composed RiskController reuses the economic lot broker, fees, OCA, reservations and trailing. Old profiles retain exact schemas and observations.',
 'Observe only known open, ledger-updated fills and confirmed close. Never use a future close to evaluate an open/fill risk decision or invent a tick path.',
 'A stop cancels outstanding orders before another candidate. Liquidation at a known open/fill boundary uses that observed price with normal slippage and fees. Close observations defer liquidation to next open, including a period boundary.',
 'Daily limits use observed UTC days; W/M use one chart bar. Consecutive loss days require D or lower candles, use realized profit less all fees, reset on a zero observed day and do not synthesize missing dates.',
 'Each filled order ID counts once per period; reversal and partial exit fills share one count in that period. Only live seen IDs remain in checkpoints alongside retired counts.',
 'Direction and size caps affect entry, including OCA entries, not strategy.order. Entry sizing/minimum admission occurs before the actual-fill risk decision; allowed reductions floor to step and fill minima still apply. Close-only reversal does not open the forbidden direction.',
 'Thresholds are inclusive; zero stops immediately. Percent rules latch permanently on nonpositive equity. Warmup cannot alter risk state. Alert messages are journal metadata, not external transmissions.',
 'No external execution equivalence, exchange sessions, margin, FX or unconfirmed/live risk execution is claimed.'],
 'test_refs':['tests/test_risk.py','tests/test_risk_pipeline.py','conformance/risk/cases.json']}}
 files={'risk.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.risk-manifest.1','release':RELEASE,'files':{n:sha(b) for n,b in files.items()}});return files
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
 if target.exists():
  if file_map(target)!={n:sha(b) for n,b in files.items()}:raise SpecError('SPEC_IMMUTABLE','Risk release differs.')
 elif a.check:raise SpecError('SPEC_IMPORT','Risk release missing.')
 else:
  target.mkdir()
  for n,b in files.items():(target/n).write_bytes(b)
 print('Immutable global risk release verified.')
if __name__=='__main__':main()
