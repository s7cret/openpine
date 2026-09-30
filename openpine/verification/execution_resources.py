"""Observe CPU affinity and inherited cgroup limits before allocating test jobs.

Reservations and RSS sampling are not kernel isolation. A quota of four CPU
seconds per second must not be mistaken for five CPUs merely from affinity.
"""
from __future__ import annotations
import math
import os
from pathlib import Path, PurePosixPath

def resource_profile(*, cgroup_root: Path=Path('/sys/fs/cgroup'), membership_file: Path=Path('/proc/self/cgroup'), affinity: list[int] | None=None, physical_bytes: int | None=None) -> dict:
    affinity = sorted(os.sched_getaffinity(0)) if affinity is None and hasattr(os, 'sched_getaffinity') else affinity
    if affinity is None:
        affinity = list(range(os.cpu_count() or 1))
    if not affinity or len(set(affinity)) != len(affinity) or any((type(cpu) is not int or cpu < 0 for cpu in affinity)):
        raise ValueError('invalid CPU affinity profile')
    if physical_bytes is None:
        try:
            physical_bytes = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (ValueError, OSError, AttributeError):
            physical_bytes = None
    memberships = []
    try:
        memberships = [line.split(':', 2) for line in membership_file.read_text().splitlines()]
    except FileNotFoundError:
        pass
    directories = []
    for row in memberships:
        if len(row) != 3:
            raise ValueError('malformed cgroup membership')
        hierarchy, controllers, group = row
        pure = PurePosixPath(group)
        if not pure.is_absolute() or '..' in pure.parts:
            raise ValueError('unresolvable cgroup namespace path')
        if hierarchy == '0' and controllers == '':
            mount = cgroup_root
        elif 'cpu' in controllers.split(',') or 'memory' in controllers.split(','):
            options = [cgroup_root / controllers, *(cgroup_root / name for name in controllers.split(','))]
            mount = next((path for path in options if path.is_dir()), None)
            if mount is None:
                continue
        else:
            continue
        current = mount.joinpath(*pure.parts[1:])
        while current.is_relative_to(mount):
            directories.append(current)
            if current == mount:
                break
            current = current.parent
    if not directories and cgroup_root.is_dir():
        directories = [cgroup_root]
    observations, quotas, memory = ([], [], [])
    for directory in dict.fromkeys(directories):
        for filename in ('cpu.max', 'cpu.cfs_quota_us', 'memory.max', 'memory.limit_in_bytes'):
            path = directory / filename
            try:
                text = path.read_text().strip()
            except FileNotFoundError:
                continue
            row = {'path': str(path), 'value': text}
            if filename == 'cpu.max':
                values = text.split()
                if len(values) != 2 or not values[1].isdigit() or int(values[1]) <= 0:
                    raise ValueError('invalid cgroup CPU quota')
                if values[0] != 'max':
                    if not values[0].isdigit() or int(values[0]) <= 0:
                        raise ValueError('invalid cgroup CPU quota')
                    quotas.append(int(values[0]) / int(values[1]))
            elif filename == 'cpu.cfs_quota_us':
                if text != '-1':
                    period = int((directory / 'cpu.cfs_period_us').read_text().strip())
                    quota = int(text)
                    if period <= 0 or quota <= 0:
                        raise ValueError('invalid cgroup v1 CPU quota')
                    row['period_us'] = period
                    quotas.append(quota / period)
            elif text != 'max':
                limit = int(text)
                if limit <= 0:
                    raise ValueError('invalid cgroup memory limit')
                memory.append(limit)
            observations.append(row)
    import resource
    address_space = resource.getrlimit(resource.RLIMIT_AS)[0]
    if address_space == resource.RLIM_INFINITY:
        address_space = None
    frequencies = []
    for cpu in affinity:
        try:
            frequencies.append(int(Path(f'/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_max_freq').read_text().strip()))
        except (OSError, ValueError):
            frequencies = []
            break
    quota = min(quotas) if quotas else None
    cpu_slots = min(len(affinity), max(1, math.floor(quota))) if quota is not None else len(affinity)
    limits = [value for value in [physical_bytes, *memory] if value is not None and value > 0]
    return {'schema_id': 'openpine.execution_resource_profile.v1', 'affinity': affinity, 'affinity_cpus': len(affinity), 'cpu_quota': quota, 'cpu_slots': cpu_slots, 'memory_limit_bytes': min(limits) if limits else None, 'address_space_limit_bytes': address_space, 'cpu_frequency_max_khz': frequencies, 'cgroup_observations': observations, 'cgroup_limits_observed': bool(observations), 'reservation_policy': 'integer CPU slots limited by affinity and inherited quota; minimum one executor', 'memory_policy': 'visible cgroup/physical limit; available headroom is not guaranteed'}
