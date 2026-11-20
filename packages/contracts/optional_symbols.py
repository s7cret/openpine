"""Frozen negative symbol evidence for literal and prepared security calls."""

from dataclasses import dataclass
import re

@dataclass(frozen=True)
class SiteResolution:
    call_site_id: str
    symbol: str
    timeframe: str
    ignore_invalid_symbol: bool
    resolution: str
    snapshot_id: str | None
    catalog_sha256: str | None
    calc_bars_count: int | None = None

    source_currency: str | None = None
    target_currency: str | None = None
    fx_snapshot_id: str | None = None

    def payload(self):
        from dataclasses import asdict
        value=asdict(self)
        if self.calc_bars_count is None:value.pop("calc_bars_count")
        for field in ("source_currency","target_currency","fx_snapshot_id"):
            if value[field] is None:value.pop(field)
        return value

    def __post_init__(self):
        from .calendar import timeframe
        timeframe(self.timeframe)
        from .fx import code
        if (any(v is not None and not code(v) for v in (self.source_currency,self.target_currency))
                or self.source_currency and not self.target_currency
                or self.resolution=="unknown_symbol" and self.source_currency
                or self.fx_snapshot_id is not None and (not self.source_currency or not self.target_currency
                    or self.source_currency==self.target_currency or not re.fullmatch("[0-9a-f]{64}",self.fx_snapshot_id))):
            raise ValueError("Invalid currencies in security site resolution.")
        if self.calc_bars_count is not None and (type(self.calc_bars_count) is not int or not 1<=self.calc_bars_count<=10000000):
            raise ValueError("Invalid security count in run plan.")
        if (not isinstance(self.call_site_id,str) or not self.call_site_id
                or type(self.symbol) is not str or ":" not in self.symbol
                or type(self.ignore_invalid_symbol) is not bool
                or self.resolution not in ("bound","unknown_symbol")
                or (self.snapshot_id is None) != (self.resolution == "unknown_symbol")
                or self.snapshot_id is None and (not self.ignore_invalid_symbol or self.catalog_sha256 is None)
                or any(v is not None and (type(v) is not str or not re.fullmatch("[0-9a-f]{64}",v))
                       for v in (self.snapshot_id,self.catalog_sha256))):
            raise ValueError("Invalid security site resolution.")


def catalog_symbols(catalog):
    if catalog is None:
        return frozenset()
    if (type(catalog) is not dict or set(catalog) != {"schema_version","source","unknown_symbols"}
            or catalog["schema_version"] != "openpine.symbol-catalog.1"
            or type(catalog["source"]) is not str or not 0 < len(catalog["source"].strip()) <= 256):
        raise ValueError("Invalid pinned symbol catalog.")
    symbols = catalog["unknown_symbols"]
    if (type(symbols) is not list or len(symbols) > 256
            or any(type(s) is not str or len(s) > 256 or s != s.strip()
                   or len(s.split(":")) != 2 or not all(s.split(":")) for s in symbols)
            or sorted(set(symbols)) != symbols):
        raise ValueError("Expected sorted unique qualified unknown symbols.")
    return frozenset(symbols)


def has_symbol_option(node):
    return "optional_symbol" in node.overload_id.split(".")


def site_options(node):
    if has_symbol_option(node):
        from .context_ir import security_count
        options={"ignore_invalid_symbol":node.arguments[3 if node.operation=="context.lower_tf" else 5].expression.value}
        if (count:=security_count(node)) is not None:options["calc_bars_count"]=count
        return options
    return {}


def resolutions(rows, pairs, catalog):
    unknown = catalog_symbols(catalog)
    pairs = set(pairs)
    if any(symbol in unknown for symbol, _ in pairs):
        raise ValueError("Symbol catalog contradicts a supplied market.")
    receipts = []
    for row in rows:
        pair = (row["symbol"], row["timeframe"])
        if pair in pairs:
            status = "bound"
        elif pair[0] in unknown and row.get("ignore_invalid_symbol") is True:
            status = "unknown_symbol"
        else:
            raise ValueError(f"No pinned context for {pair[0]}, {pair[1]}.")
        receipts.append({**row, "resolution":status})
    return receipts


def resolve_nodes(nodes, pairs, catalog):
    rows = [{"call_site_id":n.call_site_id,"symbol":n.arguments[0].expression.value,
             "timeframe":n.arguments[1].expression.value,**site_options(n)} for n in nodes]
    return resolutions(rows, pairs, catalog)


def pinned_sites(rows, pairs, catalog):
    from .wire import digest
    if not any("ignore_invalid_symbol" in r for r in rows):
        return ()
    catalog_hash = digest(catalog) if catalog is not None else None
    return tuple(SiteResolution(r["call_site_id"],r["symbol"],r["timeframe"],r.get("ignore_invalid_symbol",False),
        r["resolution"],pairs.get((r["symbol"],r["timeframe"])),catalog_hash,r.get("calc_bars_count")) for r in rows)
