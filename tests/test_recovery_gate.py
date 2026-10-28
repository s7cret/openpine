"""The final gate requires every long proof from exactly the qualified source."""
import copy,unittest
from packages.contracts.wire import digest
from tools.recovery_gate import audit_recovery

def receipts():
    source=dict(sha256='source',python='3.14.8',implementation_sha256='impl',build={'sha256':'build'})
    common={k:source[k] for k in ('python','implementation_sha256','build')};common['status']='passed'
    reports={'managed':dict(bars=100000,verified_parquet_values=3000000,attempts=3,checkpoint_block_objects=4),
        'realtime':dict(bars=256,updates_per_bar=64,events=304128,versions=[dict(version=v,restore_inside_open_bar=True) for v in range(1,7)]),
        'unified':dict(events=1000002,verified_output_points=10000020,persisted_output_points=10000020,process_restarted=True),
        'store':dict(bars=100000,verified_parquet_values=3000000,replay_bars=2048,research_variants=2,backup_restored_gc_continued=True,disk_refusal_preserved_prefix=True)}
    values=[]
    for proof,r in reports.items():
        report={**common,**r};values.append(dict(schema_version='openpine.recovery-receipt.1',status='passed',proof=proof,
            source_sha256='source',wheel_sha256='wheel',report_sha256=digest(report),report=report))
    return values,source,dict(wheel_sha256='wheel')

class RecoveryGateTests(unittest.TestCase):
    def test_all_four_source_bound_proofs_are_required(self):
        values,source,wheel=receipts();self.assertEqual(set(audit_recovery(values,source,wheel)),{'managed','realtime','unified','store'})
        for invalid in (values[:-1],values[:-1]+[values[0]]):
            with self.assertRaises(AssertionError):audit_recovery(invalid,source,wheel)
    def test_stale_source_substituted_wheel_and_modified_report_fail(self):
        for field,value in [('source_sha256','old'),('wheel_sha256','other'),('report_sha256','wrong')]:
            values,source,wheel=receipts();values[0][field]=value
            with self.assertRaises(AssertionError):audit_recovery(values,source,wheel)
    def test_missing_values_or_restore_steps_fail_even_with_recomputed_digest(self):
        for proof,key,value in [('unified','persisted_output_points',999),('store','backup_restored_gc_continued',False),('managed','attempts',1)]:
            values,source,wheel=receipts();entry=next(r for r in values if r['proof']==proof)
            entry['report'][key]=value;entry['report_sha256']=digest(entry['report'])
            with self.assertRaises(AssertionError):audit_recovery(values,source,wheel)

if __name__=='__main__':unittest.main()
