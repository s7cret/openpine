"""Strict instrument discovery, persisted by the application through a port."""

from packages.contracts.market import InstrumentKey, InstrumentMetadata, positive_decimal


def normalize_exchange_info(payload, observed_at):
    if type(payload) is not dict or type(payload.get('symbols')) is not list or len(payload['symbols']) > 20000:
        raise ValueError('Invalid Binance instrument catalogue.')
    seen, result = set(), []
    for row in payload['symbols']:
        if type(row) is not dict or type(row.get('filters')) is not list: raise ValueError('Invalid instrument row.')
        key = InstrumentKey('binance', 'spot', row['symbol'])
        if key.key in seen: raise ValueError('Duplicate instrument in catalogue.')
        seen.add(key.key)
        filters = {f['filterType']: f for f in row['filters'] if type(f) is dict and type(f.get('filterType')) is str}
        if 'PRICE_FILTER' not in filters or 'LOT_SIZE' not in filters: raise ValueError('Instrument has no price/quantity step.')
        result.append(InstrumentMetadata(key, row['baseAsset'], row['quoteAsset'], row['status'],
            positive_decimal(filters['PRICE_FILTER']['tickSize']), positive_decimal(filters['LOT_SIZE']['stepSize']), observed_at))
    return result


class InstrumentCatalog:
    def __init__(self, repository, transport): self.repository, self.transport = repository, transport

    def refresh(self, control=None):
        now = self.transport('/api/v3/time', {}, control=control)['serverTime']
        result = normalize_exchange_info(self.transport('/api/v3/exchangeInfo', {'showPermissionSets':'false'}, control=control), now)
        self.repository.replace_instruments(result)
        return {'instruments':len(result), 'observed_at':now}

    def search(self, query='', limit=100): return self.repository.search_instruments(query, limit)
