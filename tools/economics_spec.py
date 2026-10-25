"""Reproduce pinned sizing/commission decisions and per-version artifact evidence."""
import argparse
from packages.contracts.economics import economics_policy,economics_apis,NAMES,NOTE,source_review
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha
RELEASE='pine-economics-20261025.1'
def prepare():
 lock=read_json(ROOT/'spec/lock.json');versions={}
 for v in range(1,7):
  ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
  versions[str(v)]={'profile_id':f'openpine.economics.v{v}.1','policy':economics_policy(v),'apis':economics_apis(v),
   'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in ref['entries'] if e['name'] in NAMES]}
 data={'schema_version':'openpine.economics-spec.1','base_manifest_sha256':lock['manifest_sha256'],'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],
 'versions':versions,'review':{'date':'2026-10-25','external_pine_execution':False,'basis':NOTE,'sources':[source_review()],
 'decisions':['All six versions have separate evidence. Early signatures and execution decisions are explicit reconstructions.',
 'Explicit qty overrides defaults. Derived cash/percent units floor to the pinned step at confirmed creation close. No future price, close or fill is consulted.',
 'Official equity-at-opening guidance is not claimed as implemented. This profile deliberately freezes creation-time units so pending reservations and OCA have exact quantities; gaps change exposure, not units.',
 'New header defaults to fixed/one. Old headers retain their previous admission and defaults. New settings carry only explicit step/minimum facts; absent limits remain unknown.',
 'Percent fees apply to each fill value, per-contract fees to each actual filled unit, flat per-order fees on the first actual fill of the order ID. Reversal close fill precedes open fill; no future fee redistribution.',
 'Cancelled/unfilled orders cost zero. Replacement gets a new order identity. Only live paid order IDs and cumulative/retired counters survive checkpoint; ledger entry fee residues remain exact.',
 'Only linear quote accounting with pointvalue one. No FX, collateral, margin, leverage enforcement, funding or external execution equivalence.'],
 'test_refs':['tests/test_economics.py','tests/test_economics_pipeline.py','conformance/economics/cases.json']}}
 files={'economics.json':encode(data)};files['manifest.json']=encode({'schema_version':'openpine.economics-manifest.1','release':RELEASE,'files':{n:sha(b) for n,b in files.items()}});return files
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv);target=ROOT/'spec/bundles'/RELEASE;files=prepare()
 if target.exists():
  if file_map(target)!={n:sha(b) for n,b in files.items()}:raise SpecError('SPEC_IMMUTABLE','Economics release differs.')
 elif args.check:raise SpecError('SPEC_IMPORT','Economics release missing.')
 else:
  target.mkdir()
  for n,b in files.items():(target/n).write_bytes(b)
 print('Economic immutable release verified.');return 0
if __name__=='__main__':raise SystemExit(main())
