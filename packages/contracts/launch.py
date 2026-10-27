"""Closed application preparation request; no execution or persistence adapters."""
import re
from .market import HistoryRequest
from .optimization import evaluation_window

SCHEMA='openpine.launch-request.1'


def hex_id(value,size=64):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value):
        raise ValueError('Invalid preparation identifier.')
    return value


def source_spec(value):
    if type(value) is not dict:raise ValueError('A context needs a snapshot or history request.')
    if value.keys()=={'kind','snapshot_id'} and value['kind']=='snapshot':hex_id(value['snapshot_id'])
    elif value.keys()=={'kind','request'} and value['kind']=='history':HistoryRequest.from_payload(value['request'])
    else:raise ValueError('Unknown context source fields.')
    return value


def validate_launch(value):
    required={'schema_version','request_id','input_ref','primary','contexts','inputs','timeout_seconds','gap_policy'}
    optional={'magnifier','window','broker_settings','checkpoint_interval_bars','pause_after_bars'}
    if type(value) is not dict or not required<=value.keys()<=required|optional or value['schema_version']!=SCHEMA:
        raise ValueError('Invalid preparation fields.')
    hex_id(value['request_id'],32);hex_id(value['input_ref']);source_spec(value['primary'])
    if type(value['contexts']) is not list or len(value['contexts'])>7:raise ValueError('At most seven additional contexts.')
    for item in value['contexts']:source_spec(item)
    if type(value['inputs']) is not dict or len(value['inputs'])>64:raise ValueError('Invalid input overrides.')
    if type(value['timeout_seconds']) not in (int,float) or not 0<value['timeout_seconds']<=3600:raise ValueError('Invalid execution timeout.')
    if value['gap_policy'] not in ('fail','allow_with_metadata'):raise ValueError('Choose an explicit gap policy.')
    if value.get('window') is not None:evaluation_window(value['window'])
    interval=value.get('checkpoint_interval_bars',100000);pause=value.get('pause_after_bars')
    if type(interval) is not int or not 1<=interval<=10000000:raise ValueError('Invalid checkpoint interval.')
    if pause is not None and (type(pause) is not int or pause<1):raise ValueError('Invalid pause boundary.')
    if value.get('magnifier') is not None:
        from .magnifier import configuration
        configuration(value['magnifier'])
    if value.get('broker_settings') is not None:
        from .trading import BrokerSettings
        BrokerSettings.from_payload(value['broker_settings'])
    return value
