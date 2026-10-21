"""Bounded public HTTP with shared IP-weight admission and cancellable retries."""

import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from packages.contracts.wire import loads

BASE = 'https://data-api.binance.vision'
WEIGHTS = {'/api/v3/time':1, '/api/v3/klines':2, '/api/v3/exchangeInfo':20}


class RequestControl:
    def __init__(self, *, cancelled=lambda:False, timeout=300, clock=time.monotonic, wait=None):
        self.cancelled, self.clock = cancelled, clock
        self.deadline = clock()+timeout
        self.waiter = wait or (lambda seconds: time.sleep(seconds))

    def check(self):
        if self.cancelled(): raise InterruptedError('Acquisition cancelled.')
        if self.clock() >= self.deadline: raise TimeoutError('Acquisition time budget exhausted; durable pages remain resumable.')

    def wait(self, seconds):
        stop = self.clock()+seconds
        while True:
            self.check()
            remaining=stop-self.clock()
            if remaining<=0:break
            self.waiter(min(.1,remaining,max(0,self.deadline-self.clock())))
        self.check()


class RateGovernor:
    def __init__(self, *, limit=1200, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self.lock, self.window, self.used, self.cooldown = threading.Lock(), clock(), 0, 0

    def acquire(self, weight, control):
        while True:
            control.check()
            with self.lock:
                now = self.clock()
                if now-self.window>=60: self.window,self.used=now,0
                delay = max(0,self.cooldown-now)
                if not delay and self.used+weight<=self.limit:
                    self.used+=weight
                    return
                if not delay: delay=max(.01,self.window+60-now)
            control.wait(delay)

    def observe(self, headers, retry_after=None):
        with self.lock:
            for key,value in headers.items():
                if key.lower()=='x-mbx-used-weight-1m':
                    try: self.used=max(self.used,int(value))
                    except (ValueError,TypeError): raise ValueError('Invalid exchange weight header.')
            if retry_after is not None: self.cooldown=max(self.cooldown,self.clock()+retry_after)


class HTTPTransport:
    def __init__(self, *, governor=None, opener=urllib.request.urlopen, max_retries=3):
        self.governor, self.opener, self.max_retries = governor or RateGovernor(), opener, max_retries

    def __call__(self, path, params, *, control=None):
        if path not in WEIGHTS: raise ValueError('Unsupported public exchange endpoint.')
        control = control or RequestControl(timeout=60)
        url = BASE+path+'?'+urllib.parse.urlencode(params)
        request = urllib.request.Request(url,headers={'User-Agent':'OpenPine-R6/2','Accept':'application/json'})
        for attempt in range(self.max_retries+1):
            self.governor.acquire(WEIGHTS[path],control)
            try:
                max_bytes=16*1024*1024 if path=='/api/v3/exchangeInfo' else 2*1024*1024
                with self.opener(request,timeout=min(10,max(.1,control.deadline-control.clock()))) as response:
                    self.governor.observe(response.headers)
                    raw=response.read(max_bytes+1)
                control.check()
                return loads(raw,max_bytes=max_bytes)
            except urllib.error.HTTPError as error:
                retry = error.code in (418,429) or 500<=error.code<600
                delay = min(8,.25*2**attempt)
                if error.code in (418,429):
                    raw=error.headers.get('Retry-After')
                    try: delay=float(raw) if raw is not None else delay
                    except ValueError: raise ValueError('Invalid exchange Retry-After header.') from error
                    if not 0<=delay<=86400: raise ValueError('Invalid exchange cooldown.') from error
                    self.governor.observe(error.headers,delay)
                error.close()
                if not retry or attempt==self.max_retries: raise OSError(f'Exchange HTTP {error.code}; no complete result published.') from error
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt==self.max_retries: raise OSError('Public exchange transport unavailable.') from error
                delay=min(8,.25*2**attempt)
            control.wait(delay)
        raise RuntimeError('Unreachable retry state.')


DEFAULT_TRANSPORT = HTTPTransport()
