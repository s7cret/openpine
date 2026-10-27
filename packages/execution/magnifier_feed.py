"""Bounded merge of immutable primary and lower snapshots. No acquisition here."""
import math
from dataclasses import asdict
from packages.contracts.magnifier import minute_pair
from packages.contracts.calendar import aligned,next_open
from packages.contracts.wire import digest

def provenance(source):return getattr(source,'manifest',{}).get('provenance',{})

def identities(parent,child):
    for source in (parent,child):
        if hasattr(source,'bind_identity'):source.bind_identity()
    ratio=minute_pair(parent.identity.timeframe,child.identity.timeframe)
    a,b=provenance(parent),provenance(child)
    if parent.identity.symbol!=child.identity.symbol or any(a.get(k)!=b.get(k) for k in ('provider','instrument','environment','trading_rules')):
        raise ValueError('Lower candles belong to another symbol, provider or instrument.')
    return ratio,digest({'parent':asdict(parent.identity),'lower':asdict(child.identity)})

def valid(bar,timeframe):
    values=(bar.open,bar.high,bar.low,bar.close,bar.volume)
    if (not aligned(bar.time,timeframe) or any(type(v) not in (int,float) or not math.isfinite(v) for v in values)
            or min(values[:4])<=0 or bar.volume<0 or bar.high<max(bar.open,bar.close) or bar.low>min(bar.open,bar.close) or bar.high<bar.low):
        raise ValueError('Magnifier requires aligned, finite and consistent positive OHLCV candles.')

def path(bar):
    extremes=(bar.high,bar.low) if bar.high-bar.open<=bar.open-bar.low else (bar.low,bar.high)
    return (bar.open,*extremes,bar.close)

class Feed:
    def __init__(self,parent,child,missing,*,offset=0):
        self.parent,self.child,self.missing=parent,child,missing
        self.ratio,self.identity=identities(parent,child)
        self.offset=offset;self.peeked=None;self.previous=None;self.iterator=iter(child.read_from(offset))

    def peek(self):
        if self.peeked is None:
            try:bar=next(self.iterator)
            except StopIteration:return None
            valid(bar,self.child.identity.timeframe)
            if self.previous is not None and bar.time<=self.previous:raise ValueError('Duplicate or unordered lower candles.')
            self.peeked=bar;self.previous=bar.time
        return self.peeked

    def pop(self):
        bar=self.peek();self.peeked=None;self.offset+=1;return bar

    def group(self,parent):
        valid(parent,self.parent.identity.timeframe)
        end=next_open(parent.time,self.parent.identity.timeframe)
        while (bar:=self.peek()) is not None and bar.time<parent.time:self.pop()
        start=self.offset;rows=[]
        while (bar:=self.peek()) is not None and bar.time<end:rows.append(self.pop())
        for row in rows:
            if row.high>parent.high or row.low<parent.low or row.time==parent.time and row.open!=parent.open or next_open(row.time,self.child.identity.timeframe)==end and row.close!=parent.close:
                raise ValueError('Lower candles conflict with the primary price envelope.')
        complete=(len(rows)==self.ratio and all(b.time==parent.time+i*int(self.child.identity.timeframe)*60000 for i,b in enumerate(rows)))
        if complete:
            actual=(rows[0].open,max(b.high for b in rows),min(b.low for b in rows),rows[-1].close,math.fsum(b.volume for b in rows))
            expected=(parent.open,parent.high,parent.low,parent.close,parent.volume)
            if actual[:4]!=expected[:4] or not math.isclose(actual[4],expected[4],rel_tol=1e-12,abs_tol=1e-9):
                raise ValueError('Complete lower candles conflict with the primary OHLCV.')
        elif self.missing=='reject':raise ValueError(f'Incomplete lower candle coverage at {parent.time}: {len(rows)}/{self.ratio}.')
        # A gap never mixes partial lower observations with a full-parent path.
        return rows if complete else [parent],not complete,start,self.offset

    def close(self):
        if hasattr(self.iterator,'close'):self.iterator.close()

def coverage(parent,child,missing,cancelled=None):
    feed=Feed(parent,child,missing);full=fallback=lower=0;iterator=iter(parent.read_from(0));last=None
    try:
        for bar in iterator:
            if cancelled and cancelled():raise ValueError('Magnifier preparation cancelled.')
            if last is not None and bar.time<=last:raise ValueError('Duplicate or unordered primary candles.')
            rows,used,_,_=feed.group(bar);full+=not used;fallback+=used;lower+=0 if used else len(rows);last=bar.time
        if full+fallback!=parent.identity.count:raise ValueError('Primary snapshot count differs from its rows.')
        return {'parent_bars':full+fallback,'covered_bars':full,'fallback_bars':fallback,'lower_bars':lower,'inputs_sha256':feed.identity}
    finally:
        feed.close()
        if hasattr(iterator,'close'):iterator.close()
