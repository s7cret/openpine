"""Actual prerequisite probes; failures remain environment blockers, not skips."""
from __future__ import annotations
import os
import sys

def catalog_source_preflight() -> dict:
    """Read current packaged catalog authority before expensive execution."""
    from pine2ast.catalog import CatalogRepository
    from openpine.verification.stage2_catalog import VERSIONS, _rc6_catalog_source_check
    try:
        packs = {version: CatalogRepository.default().pack(version) for version in VERSIONS}
        return _rc6_catalog_source_check(packs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'error': str(exc)}


def optimizer_process_preflight() -> dict:
    """Use the existing optimizer owner; do not add a containment fallback.

    The current Linux success path requires readable per-thread procfs children
    even if cgroup v2 exists. An absent interface must not be interpreted as an
    empty process tree. pidfd signal 0 checks access without sending a signal.
    """
    from optimizer.core import process_containment as owner
    checks, errors = ({}, [])
    checks['platform'] = sys.platform
    if not sys.platform.startswith('linux'):
        return {'ok': False, 'checks': checks, 'errors': ['coordinated optimizer containment requires the reviewed Linux execution profile']}
    children = owner._process_children(os.getpid())
    checks['procfs_children_readable'] = children is not None
    if children is None:
        errors.append('optimizer containment unavailable: per-thread procfs children cannot be read; use a Linux runner exposing /proc/<pid>/task/<tid>/children')
    fd = owner._open_pidfd(os.getpid())
    checks['pidfd_open'] = fd is not None
    try:
        checks['pidfd_access'] = fd is not None and owner._signal_process_handle(fd, 0)
        if not checks['pidfd_access']:
            errors.append('optimizer containment unavailable: pidfd open/signal access check failed')
    finally:
        if fd is not None:
            owner.close_process_handles([(os.getpid(), fd)])
    checks['subreaper_enabled_in_probe_process'] = owner.enable_child_subreaper()
    if not checks['subreaper_enabled_in_probe_process']:
        errors.append('optimizer containment unavailable: child subreaper could not be enabled')
    return {'ok': not errors, 'checks': checks, 'errors': errors, 'scope': 'environment prerequisites only; does not prove trial containment or protected-worker acceptance'}
