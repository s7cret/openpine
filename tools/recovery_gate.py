"""Source-bound long proofs run independently of the native/installed matrices."""
import argparse,json,subprocess,sys
from pathlib import Path
from packages.contracts.wire import atomic_json,read_json
from tools.ci_artifacts import source_identity,sha,validate_wheel

PROOFS={'managed':('tools.managed_recovery_soak',('--bars','100000')),
    'realtime':('tools.realtime_memory_soak',('--bars','256','--updates','64')),
    'unified':('tools.unified_storage_soak',('--bars','333334')),
    'store':('tools.store_recovery_soak',('--bars','100000'))}

def audit_recovery(receipts,source,wheel):
    assert len(receipts)==len(PROOFS) and {r['proof'] for r in receipts}==set(PROOFS),'Missing/duplicate long proof.'
    result={}
    for receipt in receipts:
        assert receipt['schema_version']=='openpine.recovery-receipt.1' and receipt['status']=='passed'
        assert receipt['source_sha256']==source['sha256'] and receipt['wheel_sha256']==wheel['wheel_sha256'],'Mixed recovery source/wheel.'
        report=receipt['report'];proof=receipt['proof']
        assert report['status']=='passed' and report['python']==source['python'] and report['implementation_sha256']==source['implementation_sha256'] and report['build']==source['build']
        from packages.contracts.wire import digest
        assert receipt['report_sha256']==digest(report),'Recovery report checksum differs.'
        if proof=='managed':assert report['bars']==100000 and report['verified_parquet_values']==3000000 and report['attempts']==3 and report['checkpoint_block_objects']>0
        elif proof=='realtime':
            assert report['bars']==256 and report['updates_per_bar']==64 and report['events']==304128
            assert [v['version'] for v in report['versions']]==list(range(1,7)) and all(v['restore_inside_open_bar'] for v in report['versions'])
        elif proof=='unified':assert report['events']==1000002 and report['verified_output_points']==report['persisted_output_points']==10000020 and report['process_restarted']
        else:assert report['bars']==100000 and report['verified_parquet_values']==3000000 and report['replay_bars']==2048 and report['research_variants']==2 and report['backup_restored_gc_continued'] and report['disk_refusal_preserved_prefix']
        result[proof]=receipt['report_sha256']
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--proof',choices=PROOFS,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--wheel-dir',type=Path,required=True);a=p.parse_args()
    source=source_identity();manifest=a.wheel_dir/'manifest.json';wheel=read_json(manifest)
    validate_wheel(a.wheel_dir/wheel['wheel'],manifest)
    module,options=PROOFS[a.proof]
    subprocess.run([sys.executable,'-X','utf8','-m',module,'--directory',str(a.directory),*options],check=True)
    report=read_json(a.directory/'report.json')
    assert source_identity()==source,'Source changed during recovery proof.'
    from packages.contracts.wire import digest
    receipt=dict(schema_version='openpine.recovery-receipt.1',status='passed',proof=a.proof,
        source_sha256=source['sha256'],wheel_sha256=wheel['wheel_sha256'],report_sha256=digest(report),report=report)
    atomic_json(a.directory/'receipt.json',receipt);print(json.dumps({'proof':a.proof,'status':'passed','report_sha256':receipt['report_sha256']}))
if __name__=='__main__':main()
