"""Bind event indices to immutable plans and runtime call sites."""
from packages.contracts.corporate import ActionIndex, BoundActions, requests, requirements, fail
from dataclasses import replace
from packages.contracts.model import OpenPineError
from packages.contracts.calendar import next_open
from packages.contracts.contexts import MarketContext


def attach(evaluation, value, symbol, timeframe, chart_currency=None):
    nodes = requests(evaluation.program)
    if not nodes:
        if value is not None:fail('ACTION_UNUSED','The program has no corporate-action requests.')
        return
    if value is None:
        fail('ACTION_SNAPSHOT', 'Select a pinned corporate-action snapshot.')
    index = value if isinstance(value, ActionIndex) else ActionIndex(value)
    primary = MarketContext('primary',symbol,timeframe,'corporate-plan')
    try:
        evaluation.corporate = BoundActions(index,timeframe,requirements(evaluation.program,evaluation.input_settings,primary),chart_currency)
    except OpenPineError as error:
        raise OpenPineError(replace(error.diagnostic,language_version=evaluation.program.identity.language_version)) from error


def admit_snapshot(program, request, manifest, objects):
    identity = manifest["identity"]
    ref = request.get('corporate_snapshot_id')
    if not ref:
        fail('ACTION_SNAPSHOT','Select a pinned corporate-action snapshot.')
    index = ActionIndex(objects.get(ref))
    primary = MarketContext('primary',identity['symbol'],identity['timeframe'],'corporate-plan')
    for req in requirements(program,request.get('settings'),primary):
        instrument = manifest['provenance'].get('environment',{}).get('instrument',{})
        currency = req['currency'] or (instrument.get('currency') if req['default_chart_currency'] else '')
        stream = index.select(req['symbol'],req['family'],req['ignore_invalid_symbol'],currency)
        if stream and (identity['first_ms'] < stream[0]['start_ms'] or
                       next_open(identity['last_ms'],identity['timeframe']) > stream[0]['end_ms']):
            fail('ACTION_COVERAGE','Snapshot coverage does not include the requested candle interval.')
