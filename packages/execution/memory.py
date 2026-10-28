"""Attempt telemetry keeps storage estimates distinct from sampled process RSS."""
import os
from packages.contracts.memory import job_memory
from packages.execution.resources import resident_bytes

def telemetry(evaluation,memory_mb,io_slots=2,*,policy=None):
    return dict(schema_version='openpine.memory-telemetry.1',policy=policy or job_memory(memory_mb,io_slots),
        runtime=evaluation.memory_status(),sampled_resident_bytes=resident_bytes(os.getpid()),
        note='Charged bytes reserve storage; sampled RSS includes interpreter, native caches and transient buffers.')
