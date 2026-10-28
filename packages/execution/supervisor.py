"""Common finite-process lifecycle; publication remains the caller's transaction."""
import math,os,subprocess,time
from pathlib import Path
from packages.contracts.wire import read_json
from .resources import resident_bytes

class WorkerFault(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code

class Supervisor:
    def __init__(self,process,path,*,timeout,heartbeat_timeout,memory_mb,attempt_id=None,clock=time.monotonic,probe=resident_bytes):
        self.process,self.path=process,Path(path)
        self.clock,self.probe=clock,probe;self.born=clock();self.deadline=self.born+timeout
        self.heartbeat_timeout,self.memory_mb=heartbeat_timeout,memory_mb
        self.attempt_id=attempt_id;self.rss=self.peak=0;self.closed=False

    @classmethod
    def start(cls,command,path,log,*,threads=1,**policy):
        process=subprocess.Popen(command,cwd=path,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            env={**os.environ,'OMP_NUM_THREADS':str(threads),'OPENBLAS_NUM_THREADS':str(threads)},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:return cls(process,path,**policy)
        except BaseException:
            process.kill();process.wait(timeout=10);raise

    def poll(self,*,cancelled=False):
        if cancelled:raise WorkerFault('CANCELLED','Worker cancelled; committed publications remain resumable.')
        now=self.clock()
        if now>=self.deadline:raise WorkerFault('JOB_TIMEOUT','Worker exceeded its timeout.')
        if self.process.poll() is not None:return None
        self.rss=self.probe(self.process.pid);self.peak=max(self.peak,self.rss)
        if self.rss>self.memory_mb*1024**2:raise WorkerFault('WORKER_MEMORY','Worker exceeded its resident memory budget.')
        try:
            pulse=read_json(self.path/'heartbeat.json',max_bytes=4096)
            tick=pulse['tick']
            if type(tick) in (int,float) and math.isfinite(tick) and now-tick>self.heartbeat_timeout:
                raise WorkerFault('WORKER_HEARTBEAT','Worker heartbeat expired.')
            if (type(tick) not in (int,float) or not math.isfinite(tick) or tick<self.born-1 or tick>now+1
                    or self.attempt_id is not None and pulse.get('attempt_id')!=self.attempt_id):
                raise ValueError('Invalid or stale worker heartbeat.')
        except WorkerFault:raise
        except (OSError,ValueError,KeyError,TypeError):
            if now-self.born>self.heartbeat_timeout:raise WorkerFault('WORKER_HEARTBEAT','Worker heartbeat is absent or invalid.')
            return None
        if now-tick>self.heartbeat_timeout:raise WorkerFault('WORKER_HEARTBEAT','Worker heartbeat expired.')
        return pulse

    def close(self):
        if self.closed:return
        if self.process.poll() is None:self.process.kill()
        self.process.wait(timeout=10);self.closed=True

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
