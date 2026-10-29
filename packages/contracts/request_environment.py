"""Additive context-local counter/time profile; existing request ABI is retained."""
from .wire import read_json,digest

PROFILE='openpine.request-environment.1'

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'bar_index':'zero_based_processed_requested_bars',
        'time':'recorded_requested_open_ms','time_close':'requested_calendar_exclusive_end' if version>=4 else 'unavailable',
        'history':'requested_observations_not_primary_bars','missing_bars':'no_synthetic_increment',
        'warmup':'counts_from_first_pinned_requested_observation','replay':'rollback_preview_commit_on_requested_close',
        'checkpoint':'existing_requested_evaluation_count_and_environment_identity',
        'barstate':'excluded','strings':'excluded','external_execution_verified':False,
        'legacy_counter_evidence':'reviewed_reconstruction_v1_v3' if version<4 else 'wiki_variable_record'}

def admit(root,snapshot,version):
    value=read_json(root/'spec/request-environment-profile.json')
    if (value.get('schema_version')!='openpine.request-environment-spec.1'
            or value['source_lock_sha256']!=digest(read_json(root/'spec/lock.json'))
            or value['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}):
        raise ValueError('Requested environment differs from its pinned version profile.')
