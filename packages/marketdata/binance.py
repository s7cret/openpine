"""Bounded public Binance Spot history, using exchange close timestamps."""

import re
import time
from dataclasses import asdict

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.wire import digest, loads
from packages.marketdata import from_payload
from packages.marketdata.providers import make_snapshot, validate_query
from packages.marketdata.timeframes import INTERVALS
from packages.marketdata.transport import BASE as BASE_URL, DEFAULT_TRANSPORT
from packages.contracts.market import InstrumentKey



def fail(code, message, category="INPUT_UNAVAILABLE"):
    raise OpenPineError(Diagnostic(category, code, message))


def http_json(path, params):
    try:
        return DEFAULT_TRANSPORT(path, params)
    except (OSError, ValueError) as error:
        fail("PROVIDER_UNAVAILABLE", f"Binance request failed; no partial dataset returned: {error}")


class BinanceProvider:
    def __init__(self, *, transport=None, page_limit=1000):
        if type(page_limit) is not int or not 1 <= page_limit <= 1000:
            fail("PROVIDER_QUERY", "Page limit must be 1..1000.", "INPUT_INVALID")
        self.transport = transport or http_json
        self.page_limit = page_limit

    def fetch(self, query):
        validate_query(query)
        try:InstrumentKey.from_pine(query.symbol)
        except ValueError:fail("PROVIDER_QUERY", "Use BINANCE:SYMBOL for Spot.", "INPUT_INVALID")
        if query.timeframe not in INTERVALS:
            fail("PROVIDER_QUERY", "Use BINANCE:SYMBOL and a supported canonical timeframe.", "INPUT_INVALID")
        server = self.transport("/api/v3/time", {})
        if type(server) is not dict or type(server.get("serverTime")) is not int or not 0 <= server["serverTime"] < 2**63:
            fail("PROVIDER_DATA", "Invalid exchange server time.", "INPUT_INVALID")
        as_of = server["serverTime"]
        if query.end_ms > as_of:
            fail("PROVIDER_QUERY", "The requested end is in the exchange's future; select a historical range.", "INPUT_INVALID")
        cursor, previous, pages, bars = query.start_ms, -1, [], []
        coverage_end = query.end_ms
        deadline = time.monotonic() + 60
        for _ in range(101):
            if time.monotonic() > deadline:
                fail("PROVIDER_TIMEOUT", "Historical fetch exceeded its time budget; no partial dataset returned.")
            page = self.transport("/api/v3/klines", {"symbol": query.symbol.split(":", 1)[1],
                "interval": INTERVALS[query.timeframe], "startTime": cursor, "endTime": query.end_ms - 1,
                "timeZone": "0", "limit": self.page_limit})
            if type(page) is not list or len(page) > self.page_limit:
                fail("PROVIDER_DATA", "Invalid kline page.", "INPUT_INVALID")
            pages.append(digest(page))
            for row in page:
                if type(row) is not list or len(row) != 12 or type(row[0]) is not int or type(row[6]) is not int or not cursor <= row[0] < query.end_ms or not previous < row[0] <= row[6] < 2**63:
                    fail("PROVIDER_DATA", "Out-of-order, repeated, out-of-range or malformed kline.", "INPUT_INVALID")
                previous = row[0]
                try:
                    if any(type(row[i]) is not str for i in range(1, 6)):
                        raise ValueError("OHLCV fields must be decimal strings.")
                    values = [float(row[i]) for i in range(1, 6)]
                except (ValueError, OverflowError) as error:
                    fail("PROVIDER_DATA", f"Invalid kline price/volume: {error}", "INPUT_INVALID")
                if row[6] < as_of:
                    bars.append(dict(zip(("time", "open", "high", "low", "close", "volume"), [row[0], *values], strict=True)))
                else:
                    coverage_end = min(coverage_end, row[0])
                if len(bars) > 100000:
                    fail("PROVIDER_LIMIT", "Range exceeds 100000 bars; narrow the query.", "PROFILE_UNCOVERED")
            if len(page) < self.page_limit or page[-1][0] + 1 >= query.end_ms:
                break
            cursor = page[-1][0] + 1
        else:
            fail("PROVIDER_LIMIT", "Pagination limit reached; no partial dataset returned.", "PROFILE_UNCOVERED")
        if not bars:
            if coverage_end < query.end_ms:
                fail("DATA_NOT_CLOSED", "The requested candles have not closed yet.")
            fail("DATA_EMPTY", "No confirmed bars in the requested range.")
        data = from_payload({"schema_version": "openpine.historical-bars.2", "context_id": "binance.spot." + query.symbol.split(":")[1] + "." + query.timeframe,
                             "symbol": query.symbol, "timeframe": query.timeframe, "bars": bars})
        return make_snapshot(data, {"provider": "binance.spot.rest.1", "endpoint": BASE_URL,
            "query": asdict(query), "server_time_ms": as_of, "coverage_end_ms": coverage_end, "page_sha256": pages,
            "gap_policy": "preserve", "range": "[start,end)", "confirmed": "close_time < server_time"})
