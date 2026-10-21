"""Provider preparation policy; persistence is supplied through the store port."""

from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import loads
from packages.marketdata.providers import validate_query


class HistoryCache:
    def __init__(self, store):
        self.store = store

    def ensure(self, provider, query, *, namespace="binance.spot.rest.1", refresh=False):
        validate_query(query)
        if type(namespace) is not str or not namespace.strip() or len(namespace) > 256:
            raise ValueError("Invalid provider/market namespace.")
        gaps = [(query.start_ms, query.end_ms)] if refresh else self.store.missing(namespace, query)
        for start, end in gaps:
            while start < end:
                stop = min(end, start + 86400000)
                try:
                    snapshot = provider.fetch(DataQuery(query.symbol, query.timeframe, start, stop))
                    actual = snapshot.data
                    if (actual.symbol, actual.timeframe) != (query.symbol, query.timeframe) or any(
                            not start <= bar.time < stop for bar in actual.bars):
                        raise ValueError("Provider returned candles outside the requested identity/range.")
                    covered_end = loads(snapshot.provenance).get("coverage_end_ms", stop)
                    if type(covered_end) is not int or not start < covered_end <= stop or any(bar.time >= covered_end for bar in actual.bars):
                        raise ValueError("Provider supplied invalid confirmed coverage.")
                    snapshot_id = self.store.save(snapshot)
                except ValueError as error:
                    code = getattr(getattr(error, "diagnostic", None), "code", None)
                    if code == "DATA_NOT_CLOSED":
                        start = stop
                        continue
                    if code != "DATA_EMPTY":
                        raise
                    snapshot_id = None
                    covered_end = stop
                self.store.cover(namespace, query, start, covered_end, snapshot_id)
                start = stop
        return self.store.select(query, namespace=namespace)
