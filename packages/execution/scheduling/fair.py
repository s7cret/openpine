"""Bounded demand-aware round robin with aging; running work is never preempted."""
import time

LANES=('live','history','research')
class FairScheduler:
    def __init__(self,*,clock=time.monotonic,aging_seconds=5,retry_window=2,capacity=1024):
        self.clock,self.aging,self.window,self.capacity=clock,aging_seconds,retry_window,capacity
        self.pending={};self.cursor=0

    def offer(self,key,lane,*,persistent=False):
        if lane not in LANES:raise ValueError('Unknown worker lane.')
        self.expire();now=self.clock()
        if key not in self.pending:
            if len(self.pending)>=self.capacity:return False
            self.pending[key]=[lane,now,None if persistent else now+self.window]
        elif not persistent:self.pending[key][2]=now+self.window
        return True

    def expire(self):
        now=self.clock()
        for key,(_,_,expiry) in list(self.pending.items()):
            if expiry is not None and expiry<=now:del self.pending[key]

    def choose(self,*,live_reserved=False):
        self.expire()
        if not self.pending:return None
        if live_reserved:
            live=[k for k,v in self.pending.items() if v[0]=='live']
            if live:return min(live,key=lambda k:(self.pending[k][1],k))
        aged={v[0] for v in self.pending.values() if self.clock()-v[1]>=self.aging}
        for offset in range(len(LANES)):
            lane=LANES[(self.cursor+offset)%len(LANES)]
            keys=[k for k,v in self.pending.items() if v[0]==lane and (not aged or lane in aged)]
            if keys:return min(keys,key=lambda k:(self.pending[k][1],k))

    def started(self,key):
        lane=self.pending.pop(key)[0];self.cursor=(LANES.index(lane)+1)%len(LANES)

    def discard(self,key):self.pending.pop(key,None)
