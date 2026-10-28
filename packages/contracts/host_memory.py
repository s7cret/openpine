"""Admission headroom is a host policy; saved computation plans stay immutable."""
from dataclasses import dataclass

MIB=1024**2

@dataclass(frozen=True)
class HostMemoryPolicy:
    reserve_min_mb: int = 128
    reserve_max_mb: int = 1024
    total_divisor: int = 16
    resume_margin_mb: int = 64

    def __post_init__(self):
        if (any(type(v) is not int or v<1 for v in vars(self).values())
                or self.reserve_min_mb>self.reserve_max_mb):
            raise ValueError('Invalid host memory admission policy.')

    def reserve_bytes(self,total):
        return max(self.reserve_min_mb*MIB,min(self.reserve_max_mb*MIB,total//self.total_divisor))
