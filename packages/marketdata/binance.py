"""Bounded public Binance Spot history, using exchange close timestamps."""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.wire import digest, loads
from packages.marketdata import from_payload
from packages.marketdata.providers import make_snapshot, validate_query

BASE_URL = "https://data-api.binance.vision"
INTERVALS = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h",
             "120": "2h", "240": "4h", "360": "6h", "480": "8h", "720": "12h",
             "D": "1d", "W": "1w", "M": "1M"}


def fail(code, message, category="INPUT_UNAVAILABLE"):
    raise OpenPineError(Diagnostic(category, code, message))


def http_json(path, params):
    url = BASE_URL + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "OpenPine-R6/1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        return loads(raw, max_bytes=2 * 1024 * 1024)
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
        if not re.fullmatch(r"BINANCE:[A-Z0-9]{2,30}", query.symbol) or query.timeframe not in INTERVALS:
            fail("PROVIDER_QUERY", "Use BINANCE:SYMBOL and a supported canonical timeframe.", "INPUT_INVALID")
        server = self.transport("/api/v3/time", {})
        if type(server) is not dict or type(server.get("serverTime")) is not int or not 0 <= server["serverTime"] < 2**63:
            fail("PROVIDER_DATA", "Invalid exchange server time.", "INPUT_INVALID")
        as_of = server["serverTime"]
        if query.end_ms > as_of:
            fail("PROVIDER_QUERY", "The requested end is in the exchange's future; select a historical range.", "INPUT_INVALID")
        cursor, previous, pages, bars = query.start_ms, -1, [], []
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
                if len(bars) > 100000:
                    fail("PROVIDER_LIMIT", "Range exceeds 100000 bars; narrow the query.", "PROFILE_UNCOVERED")
            if len(page) < self.page_limit or page[-1][0] + 1 >= query.end_ms:
                break
            cursor = page[-1][0] + 1
        else:
            fail("PROVIDER_LIMIT", "Pagination limit reached; no partial dataset returned.", "PROFILE_UNCOVERED")
        if not bars:
            fail("DATA_EMPTY", "No confirmed bars in the requested range.")
        data = from_payload({"schema_version": "openpine.historical-bars.2", "context_id": "binance.spot." + query.symbol.split(":")[1] + "." + query.timeframe,
                             "symbol": query.symbol, "timeframe": query.timeframe, "bars": bars})
        return make_snapshot(data, {"provider": "binance.spot.rest.1", "endpoint": BASE_URL,
            "query": asdict(query), "server_time_ms": as_of, "page_sha256": pages,
            "gap_policy": "preserve", "range": "[start,end)", "confirmed": "close_time < server_time"})
