#!/usr/bin/env python3
"""Audit the bounded preparation artifact allowlist before any public upload.

This does not grant publication permission. Original primary bytes remain
unchanged; unexpected files or sensitive contents stop public transport.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from openpine.verification.execution_identity import evidence_path, hash_file, write_once_json
from openpine.verification.identity import seal

TOP_LEVEL = frozenset({
    'runner.json', 'summary.json', 'bundle.json', 'collection.json', 'junit.xml',
    'installed-origins.json', 'package-trees.json', 'installed-package-trees.json',
    'coverage.xml', 'coverage.json', 'transport-scope.json',
})
OWNER = r'(?:openpine|openpine-contracts|pine2ast|ast2python|pinelib|backtest_engine|marketdata-provider|optimizer)'
ALLOWED = (
    re.compile(r'commands/[0-9]{4}/(?:command\.json|stdout\.log|stderr\.log)'),
    re.compile(r'collection\.evidence/py313/probe\.stderr\.log'),
    re.compile(r'collection\.evidence/py313/' + OWNER + r'/(?:inventory\.json|collection\.log)'),
)
SENSITIVE = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'access-key': re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'url-credentials': re.compile(r'https?://[^\s/@:]+:[^\s/@]+@'),
    'credential-value': re.compile(r'''(?i)(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)["']?\s*[:=]\s*["']?[A-Za-z0-9_+/=-]{8,}'''),
    'bearer-token': re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}'),
    'personal-email': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}

ZIP_RESERVE_BYTES = 1024 * 1024
ZIP_ENTRY_RESERVE_BYTES = 1024


def check_upload_payload(archive_root: Path, audit_report: Path, *, max_bytes: int) -> dict:
    """Bound every uploaded byte plus a conservative ZIP framing reserve.

    Three fixed regular files are uploaded with compression-level 0. Reserve
    1 MiB plus 1 KiB per entry for ZIP headers, names and deflate framing.
    A size failure preserves all required primaries and stops publication.
    """
    archive_root, audit_report = archive_root.absolute(), audit_report.absolute()
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('positive strict integer upload budget required')
    if (not archive_root.is_dir() or any(p.is_symlink() for p in (archive_root, *archive_root.parents))
            or any(p.is_symlink() for p in (audit_report, *audit_report.parents))):
        raise ValueError('unsafe public upload path')
    expected = {'evidence.tar.gz', 'manifest.json'}
    if {p.relative_to(archive_root).as_posix() for p in archive_root.rglob('*')} != expected:
        raise ValueError('public archive upload scope differs from required files')
    paths = [archive_root / name for name in sorted(expected)] + [audit_report]
    if not all(path.is_file() and not path.is_symlink() for path in paths):
        raise ValueError('missing or unsafe required upload file')
    if json.loads(audit_report.read_text())['ok'] is not True:
        raise ValueError('public content audit did not pass')
    files = {('preparation-archive/' + path.name if path.parent == archive_root else path.name):
             {'size': path.stat().st_size, 'sha256': hash_file(path)} for path in paths}
    payload_bytes = sum(row['size'] for row in files.values())
    zip_reserve = ZIP_RESERVE_BYTES + ZIP_ENTRY_RESERVE_BYTES * len(files)
    upper_bound = payload_bytes + zip_reserve
    if upper_bound > max_bytes:
        raise ValueError('complete required upload payload plus ZIP reserve exceeds byte ceiling')
    return {'files': files, 'payload_bytes': payload_bytes, 'zip_reserve_bytes': zip_reserve,
            'upload_upper_bound_bytes': upper_bound, 'max_upload_bytes': max_bytes}


def audit(root: Path, *, candidate: str, run_id: str, max_bytes: int) -> dict:
    root = root.absolute()
    if root.is_symlink() or not root.is_dir() or max_bytes <= 0:
        raise ValueError('invalid public evidence root or budget')
    if not re.fullmatch('[0-9a-f]{40}', candidate) or not run_id:
        raise ValueError('public evidence requires exact candidate and run identity')
    files, errors, total = {}, [], 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append({'path': relative, 'reason': 'symlink'})
            continue
        if path.is_dir():
            continue
        path = evidence_path(root, relative)
        if relative not in TOP_LEVEL and not any(p.fullmatch(relative) for p in ALLOWED):
            errors.append({'path': relative, 'reason': 'outside-public-allowlist'})
            continue
        size = path.stat().st_size
        total += size
        if total > max_bytes:
            raise ValueError('public evidence exceeds byte ceiling')
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeError:
            errors.append({'path': relative, 'reason': 'non-text-content'})
            continue
        for kind, pattern in SENSITIVE.items():
            matches = list(pattern.finditer(content))
            if kind == 'personal-email':
                matches = [m for m in matches if not m.group().endswith(('.invalid', '@users.noreply.github.com'))]
            if matches:
                errors.append({'path': relative, 'reason': kind})
        files[relative] = {'size': size, 'sha256': hash_file(path)}
    if not files:
        errors.append({'reason': 'empty-public-evidence'})
    return seal({'schema_id': 'openpine.public_evidence_audit.v1', 'candidate': candidate,
                 'run_id': run_id, 'max_bytes': max_bytes, 'total_bytes': total,
                 'files': files, 'errors': errors, 'ok': not errors,
                 'permission_granted': False, 'original_primaries_changed': False})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--max-bytes', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        raise ValueError('audit output must be outside the scanned primary tree')
    report = audit(args.root, candidate=args.candidate, run_id=args.run_id, max_bytes=args.max_bytes)
    write_once_json(args.output, report)
    print(json.dumps({'ok': report['ok'], 'content_hash': report['content_hash'],
                      'errors': report['errors']}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
