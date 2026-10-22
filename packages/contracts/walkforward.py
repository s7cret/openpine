"""Closed temporal policy for independent rolling/expanding research folds."""
from packages.contracts.optimization import split_windows

SCHEMA = 'openpine.walkforward.1'
TERMINAL = ('succeeded', 'holdout_failed', 'no_eligible_candidate', 'cancelled')


def fold_windows(value, candidate_budget):
    fields = {'mode', 'start_ms', 'train_ms', 'test_ms', 'step_ms', 'warmup_ms', 'folds', 'max_jobs'}
    if type(value) is not dict or value.keys() != fields:
        raise ValueError('Walk-forward requires mode/start_ms/train_ms/test_ms/step_ms/warmup_ms/folds/max_jobs.')
    if value['mode'] not in ('rolling', 'expanding') or any(type(value[k]) is not int for k in fields - {'mode'}):
        raise ValueError('Use rolling/expanding and integer UTC durations.')
    if (type(candidate_budget) is not int or not 1 <= value['folds'] <= 16 or not 1 <= candidate_budget <= 256
            or not 1 <= value['max_jobs'] <= 4096 or value['folds']*(candidate_budget+1) > value['max_jobs']):
        raise ValueError('At most 16 folds and 4096 total jobs, including one holdout per fold; provide sufficient max_jobs.')
    if not 0 <= value['warmup_ms'] <= value['start_ms'] or value['train_ms'] <= 0 or value['test_ms'] <= 0 or value['step_ms'] < value['test_ms']:
        raise ValueError('Require positive train/test, step >= test and 0 <= warmup <= start.')
    result = []
    for ordinal in range(value['folds']):
        shift = ordinal * value['step_ms']
        train_start = value['start_ms'] + (shift if value['mode'] == 'rolling' else 0)
        train_end = value['start_ms'] + value['train_ms'] + shift
        result.append(split_windows({
            'selection': {'warmup_start_ms': train_start-value['warmup_ms'], 'score_start_ms': train_start, 'end_ms': train_end},
            'holdout': {'warmup_start_ms': train_end-value['warmup_ms'], 'score_start_ms': train_end, 'end_ms': train_end+value['test_ms']}}))
    return result
