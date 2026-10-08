"""Portable, bounded archives for the existing verification evidence owners.

Archives preserve bytes and provenance, including unsuccessful runs. They do
not establish product acceptance. Only explicitly declared private trees may be
excluded; originals and previous archives are never replaced or removed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import tarfile
from pathlib import Path

from openpine.verification.execution_identity import (
    ensure_external_output, evidence_path, hash_file, write_once_json,
)
from openpine.verification.identity import canonical, read_json, seal, verify

ARCHIVE_SCHEMA = 'openpine.evidence_archive.v1'
MAX_FILES = 100_000
MANIFEST_LIMIT = 32 * 1024 * 1024
HASH = re.compile(r'sha256:[0-9a-f]{64}')
CANDIDATE = re.compile(r'(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})')


def _directory(path: Path) -> Path:
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('unsafe evidence directory')
    if not path.is_dir():
        raise ValueError('missing evidence directory')
    return path.resolve()


def _private_paths(root: Path, private: tuple[str, ...]) -> list[str]:
    if len(set(private)) != len(private):
        raise ValueError('duplicate declared private tree')
    for relative in private:
        path = evidence_path(root, relative, must_exist=False)
        if path.name != 'private' or (path.exists() and not path.is_dir()):
            raise ValueError('only explicit private directories may be excluded')
    ordered = sorted(private)
    if any(b.startswith(a + '/') for a in ordered for b in ordered if a != b):
        raise ValueError('overlapping declared private trees')
    return ordered


def _inventory(root: Path, private: list[str], max_bytes: int) -> dict:
    files: dict[str, dict] = {}
    total = 0
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        kept = []
        for name in sorted(subdirs):
            path = base / name
            if path.relative_to(root).as_posix() in private:
                continue
            if path.is_symlink():
                raise ValueError('evidence inventory contains a symlink')
            kept.append(name)
        subdirs[:] = kept
        for name in sorted(filenames):
            path = base / name
            relative = path.relative_to(root).as_posix()
            path = evidence_path(root, relative)
            size = path.stat().st_size
            total += size
            if total > max_bytes or len(files) >= MAX_FILES:
                raise ValueError('evidence inventory exceeds archive budget')
            files[relative] = {'size': size, 'sha256': hash_file(path)}
    if not files:
        raise ValueError('no primary evidence to archive')
    return dict(sorted(files.items()))


def _primary_inventory(root: Path, artifacts: dict, max_bytes: int) -> dict:
    """Read an owner's frozen regular-file descriptors, never loose selectors."""
    if not isinstance(artifacts, dict) or not 0 < len(artifacts) <= MAX_FILES:
        raise ValueError('invalid primary artifact inventory')
    files, total = {}, 0
    for relative, record in sorted(artifacts.items()):
        path = evidence_path(root, relative)
        if (not isinstance(record, dict) or set(record) != {'size', 'sha256'}
                or type(record['size']) is not int or record['size'] < 0
                or not isinstance(record['sha256'], str) or not HASH.fullmatch(record['sha256'])):
            raise ValueError('invalid primary artifact descriptor')
        total += record['size']
        if total > max_bytes:
            raise ValueError('primary artifact inventory exceeds archive budget')
        if path.stat().st_size != record['size'] or hash_file(path) != record['sha256']:
            raise ValueError('primary artifact differs from owner inventory')
        files[relative] = dict(record)
    return files


def _check_primary_exclusions(root: Path, files: dict, private: list[str]) -> None:
    """An explicit private label cannot hide an existing owner descriptor."""
    if not private:
        return

    def descriptors(value):
        if isinstance(value, dict):
            if isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str) and HASH.fullmatch(value['sha256']):
                yield value['path']
            for child in value.values():
                yield from descriptors(child)
        elif isinstance(value, list):
            for child in value:
                yield from descriptors(child)

    for relative, record in files.items():
        if not relative.endswith('.json') or record['size'] > MANIFEST_LIMIT:
            continue
        receipt = evidence_path(root, relative)
        try:
            value = read_json(receipt)
        except (ValueError, UnicodeError, RecursionError):
            continue  # Incomplete failure diagnostics remain archived verbatim.
        for described in descriptors(value):
            parent = receipt.parent
            while parent.is_relative_to(root):
                try:
                    target = evidence_path(parent, described, must_exist=False)
                except ValueError:
                    break
                if target.is_file():
                    path = target.relative_to(root).as_posix()
                    if any(path == p or path.startswith(p + '/') for p in private):
                        raise ValueError('declared private exclusion overlaps a primary artifact')
                if parent == root:
                    break
                parent = parent.parent


class _BoundedOutput:
    def __init__(self, stream, limit: int):
        self.stream, self.limit, self.count = stream, limit, 0

    def write(self, data):
        if self.count + len(data) > self.limit:
            raise ValueError('compressed evidence exceeds archive budget')
        size = self.stream.write(data)
        self.count += size
        return size

    def flush(self):
        self.stream.flush()


class _BoundedInput(io.RawIOBase):
    def __init__(self, stream, limit: int):
        self.stream, self.limit, self.count = stream, limit, 0

    def read(self, size: int = -1) -> bytes:
        remaining = self.limit - self.count
        data: bytes = self.stream.read(remaining + 1 if size < 0 else min(size, remaining + 1))
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError('expanded evidence archive framing exceeds budget')
        return data


def _tar_budget(files: dict) -> int:
    # Bound headers (including the exact path's PAX encoding), content padding
    # and both EOF blocks before rounding to a full tar record. A member ending
    # one block before a record boundary needs padding into the following record.
    # Even malformed PAX metadata is read boundedly.
    total = 2 * tarfile.BLOCKSIZE
    for relative, record in files.items():
        member = tarfile.TarInfo(relative)
        member.size, member.mode, member.mtime = record['size'], 0o600, 0
        total += len(member.tobuf(format=tarfile.PAX_FORMAT))
        total += (record['size'] + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE * tarfile.BLOCKSIZE
    return (total + tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE * tarfile.RECORDSIZE


def _validate_manifest(manifest: dict) -> None:
    body = verify(manifest, ARCHIVE_SCHEMA)
    if set(body) != {'schema_id', 'candidate', 'source_hash', 'run_id', 'retention_days',
                     'max_bytes', 'private_exclusions', 'files', 'total_bytes',
                     'file_count', 'archive', 'full_product_accepted'}:
        raise ValueError('invalid evidence archive manifest fields')
    if (not isinstance(body['candidate'], str) or not CANDIDATE.fullmatch(body['candidate'])
            or not isinstance(body['run_id'], str) or not body['run_id'] or len(body['run_id']) > 200
            or (body['source_hash'] is not None and (not isinstance(body['source_hash'], str)
                or not HASH.fullmatch(body['source_hash'])))
            or type(body['retention_days']) is not int or not 1 <= body['retention_days'] <= 90
            or type(body['max_bytes']) is not int or body['max_bytes'] <= 0
            or body['full_product_accepted'] is not False):
        raise ValueError('invalid evidence archive identity or budget')
    files = body['files']
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES:
        raise ValueError('invalid evidence archive inventory')
    exclusions = body['private_exclusions']
    if not isinstance(exclusions, list) or any(not isinstance(p, str) for p in exclusions):
        raise ValueError('invalid private exclusions')
    # Reuse the evidence path grammar without reading local source files.
    scope = Path('/__openpine_archive_path_validation__')
    if _private_paths(scope, tuple(exclusions)) != exclusions:
        raise ValueError('noncanonical private exclusions')
    total = 0
    for relative, record in files.items():
        evidence_path(scope, relative, must_exist=False)
        if (not isinstance(record, dict) or set(record) != {'size', 'sha256'}
                or type(record['size']) is not int or record['size'] < 0
                or not isinstance(record['sha256'], str) or not HASH.fullmatch(record['sha256'])
                or any(relative == p or relative.startswith(p + '/') for p in exclusions)):
            raise ValueError('invalid archived file descriptor')
        total += record['size']
    descriptor = body['archive']
    if (type(body['total_bytes']) is not int or body['total_bytes'] != total
            or total > body['max_bytes'] or type(body['file_count']) is not int
            or body['file_count'] != len(files)
            or not isinstance(descriptor, dict) or set(descriptor) != {'path', 'sha256', 'size'}
            or descriptor['path'] != 'evidence.tar.gz'
            or not isinstance(descriptor['sha256'], str) or not HASH.fullmatch(descriptor['sha256'])
            or type(descriptor['size']) is not int or not 0 < descriptor['size'] <= body['max_bytes']):
        raise ValueError('evidence archive inventory or size mismatch')


def verify_archive(archive: Path, manifest: dict, *, expected_manifest_hash: str,
                   expected_candidate: str, expected_run_id: str,
                   expected_source_hash: str | None = None) -> dict:
    """Authenticate all compressed bytes and every bounded, regular tar member.

    No extraction occurs. The expected manifest identity must come from trusted
    small run metadata, rather than being inferred from the downloaded archive.
    """
    _validate_manifest(manifest)
    if (manifest['content_hash'] != expected_manifest_hash
            or manifest['candidate'] != expected_candidate
            or manifest['run_id'] != expected_run_id
            or (expected_source_hash is not None and manifest['source_hash'] != expected_source_hash)):
        raise ValueError('evidence archive candidate/source/run/manifest identity mismatch')
    archive = evidence_path(archive.parent, archive.name)
    before = archive.stat()
    if before.st_size != manifest['archive']['size'] or hash_file(archive) != manifest['archive']['sha256']:
        raise ValueError('evidence archive checksum or size mismatch')
    seen = set()
    total = 0
    try:
        with gzip.open(archive, 'rb') as expanded:
            bounded = _BoundedInput(expanded, _tar_budget(manifest['files']))
            # A larger tar read buffer can swallow payload after the end marker,
            # hiding it from the footer/tail check below.
            with tarfile.open(fileobj=bounded, mode='r|', bufsize=tarfile.BLOCKSIZE) as stream:
                for member in stream:
                    record = manifest['files'].get(member.name)
                    if (not member.isfile() or member.name in seen or record is None
                            or member.size != record['size'] or member.uid != 0 or member.gid != 0
                            or member.mtime != 0 or member.mode != 0o600):
                        raise ValueError('unsafe, duplicate or unexpected evidence archive member')
                    seen.add(member.name)
                    total += member.size
                    if total > manifest['max_bytes']:
                        raise ValueError('expanded evidence exceeds archive budget')
                    content = stream.extractfile(member)
                    if content is None:
                        raise ValueError('missing evidence archive member data')
                    digest = hashlib.sha256()
                    count = 0
                    with content:
                        for chunk in iter(lambda: content.read(1024 * 1024), b''):
                            count += len(chunk)
                            digest.update(chunk)
                    if count != record['size'] or 'sha256:' + digest.hexdigest() != record['sha256']:
                        raise ValueError('archived evidence file checksum mismatch')
            # Exhaust gzip to validate its footer even after tar's end marker.
            # Normal tar record padding is bounded; appended payload is rejected.
            tail = bounded.read(10_241)
            if len(tail) > 10_240 or tail.strip(b'\0'):
                raise ValueError('unexpected evidence archive trailing payload')
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError('incomplete or malformed evidence archive') from error
    if seen != set(manifest['files']) or total != manifest['total_bytes']:
        raise ValueError('evidence archive omitted required primary files')
    after = archive.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or hash_file(archive) != manifest['archive']['sha256']):
        raise ValueError('evidence archive changed during readback')
    return {'file_count': len(seen), 'total_bytes': total, 'archive_bytes': after.st_size,
            'manifest_hash': manifest['content_hash'], 'candidate': expected_candidate,
            'run_id': expected_run_id, 'verified': True, 'full_product_accepted': False}


def archive_evidence(evidence: Path, output: Path, *, roots: dict[str, Path],
                     candidate: str, run_id: str, retention_days: int,
                     max_bytes: int, private: tuple[str, ...] = (),
                     source_hash: str | None = None,
                     primary_artifacts: dict | None = None) -> dict:
    """Create once, read back, and retain original evidence after any outcome.

    A mixed owner tree may supply its exact frozen primary descriptors. This
    API has no CLI selector, and selected primaries cannot use private exclusions.
    Completeness and referenced-primary closure remain that owner's obligation.
    """
    evidence = _directory(evidence)
    output = Path(output).absolute()
    ensure_external_output(output, roots)
    if '..' in output.parts or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('unsafe archive output directory')
    if output.resolve().is_relative_to(evidence) or evidence.is_relative_to(output.resolve()):
        raise ValueError('archive output overlaps primary evidence')
    for root in roots.values():
        root = Path(root).resolve()
        if evidence.is_relative_to(root) or root.is_relative_to(evidence):
            raise ValueError('archive evidence overlaps source roots')
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('positive archive byte budget is required')
    if (not isinstance(candidate, str) or not CANDIDATE.fullmatch(candidate)
            or not isinstance(run_id, str) or not run_id or len(run_id) > 200
            or type(retention_days) is not int or not 1 <= retention_days <= 90
            or (source_hash is not None and (not isinstance(source_hash, str) or not HASH.fullmatch(source_hash)))):
        raise ValueError('invalid evidence archive identity or retention')
    exclusions = _private_paths(evidence, private)
    if primary_artifacts is not None and exclusions:
        raise ValueError('primary artifact archives cannot exclude private trees')
    def inventory():
        return (_inventory(evidence, exclusions, max_bytes) if primary_artifacts is None
                else _primary_inventory(evidence, primary_artifacts, max_bytes))
    files = inventory()
    _check_primary_exclusions(evidence, files, exclusions)
    total = sum(record['size'] for record in files.values())
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = _directory(output)
    archive = evidence_path(output, 'evidence.tar.gz', must_exist=False)
    partial = evidence_path(output, 'evidence.tar.gz.partial', must_exist=False)
    manifest_path = evidence_path(output, 'manifest.json', must_exist=False)
    if any(p.exists() for p in (archive, partial, manifest_path)):
        raise ValueError('evidence archive attempt already exists')
    if shutil.disk_usage(output).free < min(max_bytes, total + 1024 * 1024) + 16 * 1024 * 1024:
        raise ValueError('insufficient free disk for bounded evidence archive')
    # Failed writes intentionally retain .partial diagnostics; no broad cleanup.
    with partial.open('xb') as raw:
        os.fchmod(raw.fileno(), 0o600)
        bounded = _BoundedOutput(raw, max_bytes)
        with gzip.GzipFile(filename='', fileobj=bounded, mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.PAX_FORMAT) as stream:
                for relative, record in files.items():
                    path = evidence_path(evidence, relative)
                    if path.stat().st_size != record['size'] or hash_file(path) != record['sha256']:
                        raise ValueError('primary evidence changed during archive creation')
                    member = tarfile.TarInfo(relative)
                    member.size, member.mode, member.mtime = record['size'], 0o600, 0
                    with path.open('rb') as data:
                        stream.addfile(member, data)
        raw.flush()
        os.fsync(raw.fileno())
    manifest: dict = seal({'schema_id': ARCHIVE_SCHEMA, 'candidate': candidate,
                     'source_hash': source_hash, 'run_id': run_id,
                     'retention_days': retention_days, 'max_bytes': max_bytes,
                     'private_exclusions': exclusions, 'files': files,
                     'total_bytes': total, 'file_count': len(files),
                     'archive': {'path': archive.name, 'sha256': hash_file(partial),
                                 'size': partial.stat().st_size},
                     'full_product_accepted': False})
    if len(canonical(manifest)) + 1 > MANIFEST_LIMIT:
        raise ValueError('evidence archive manifest exceeds size limit')
    verify_archive(partial, manifest, expected_manifest_hash=manifest['content_hash'],
                   expected_candidate=candidate, expected_run_id=run_id,
                   expected_source_hash=source_hash)
    if inventory() != files:
        raise ValueError('primary evidence inventory changed during archive readback')
    os.link(partial, archive)
    write_once_json(manifest_path, manifest)
    verify_archive(archive, read_json(manifest_path), expected_manifest_hash=manifest['content_hash'],
                   expected_candidate=candidate, expected_run_id=run_id,
                   expected_source_hash=source_hash)
    partial.unlink()  # Only this helper's successfully published temporary file.
    return manifest


def cleanup_archived_private(plan: dict, evidence: Path, attempt: str,
                             archive: Path, manifest: dict, *,
                             expected_manifest_hash: str, expected_candidate: str,
                             run_id: str) -> None:
    """Invoke the existing cleanup owner only for an authenticated success.

    Every primary reference must be both present locally and in the verified
    archive. Failure, timeout, cancellation, missing upload and foreign run
    identities preserve the whole private tree.
    """
    from openpine.verification.execution_campaign import _clear_owned_private, _validate_primary_artifacts
    from openpine.verification.execution_plan import validate_plan

    validate_plan(plan)
    evidence = _directory(evidence)
    ensure_external_output(evidence, {n: Path(p) for n, p in plan['roots'].items()})
    verify_archive(archive, manifest, expected_manifest_hash=expected_manifest_hash,
                   expected_candidate=expected_candidate, expected_run_id=run_id,
                   expected_source_hash=plan['source']['content_hash'])
    receipt_path = evidence_path(evidence, attempt + '/execution.json')
    receipt = read_json(receipt_path)
    if (receipt.get('status') != 'completed' or receipt.get('returncode') != 0
            or receipt.get('run_id') != run_id or receipt.get('attempt_id') != 'a001'):
        raise ValueError('private cleanup requires the exact successful attempt')
    task = next((t for t in plan['tasks'] if t['id'] == receipt.get('task')), None)
    shard = next((s for s in task['shards'] if s['id'] == receipt.get('shard')), None) if task else None
    if task is None or shard is None or attempt != task['id'] + '/' + shard['id'] + '/a001':
        raise ValueError('private cleanup attempt differs from plan')
    if task.get('private_retention', 'preserve') != 'delete-on-success':
        raise ValueError('private cleanup was not declared in the plan')
    for desc in [*receipt['artifacts'].values(), {'path': attempt + '/execution.json', 'sha256': hash_file(receipt_path)}]:
        record = manifest['files'].get(desc['path'])
        if record is None or record['sha256'] != desc['sha256']:
            raise ValueError('primary artifact is absent from the verified archive')
    _validate_primary_artifacts(plan, evidence, task, shard, receipt, receipt.get('binding_hash'))
    _clear_owned_private(evidence, receipt_path.parent, receipt['artifacts'])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m openpine.verification.execution_evidence')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('archive')
    create.add_argument('--evidence', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--source-root', type=Path, action='append', required=True)
    create.add_argument('--candidate', '--candidate-sha', dest='candidate', required=True)
    create.add_argument('--source-hash')
    create.add_argument('--run-id', required=True)
    create.add_argument('--retention-days', type=int, required=True)
    create.add_argument('--max-bytes', type=int, required=True)
    create.add_argument('--private', action='append', default=[])
    check = commands.add_parser('verify')
    check.add_argument('--archive', type=Path, required=True)
    check.add_argument('--manifest', type=Path, required=True)
    check.add_argument('--expected-manifest-hash', required=True)
    check.add_argument('--candidate', '--candidate-sha', dest='candidate', required=True)
    check.add_argument('--source-hash')
    check.add_argument('--run-id', required=True)
    cleanup = commands.add_parser('cleanup-private')
    cleanup.add_argument('--plan', type=Path, required=True)
    cleanup.add_argument('--evidence', type=Path, required=True)
    cleanup.add_argument('--attempt', required=True)
    cleanup.add_argument('--archive', type=Path, required=True)
    cleanup.add_argument('--manifest', type=Path, required=True)
    cleanup.add_argument('--expected-manifest-hash', required=True)
    cleanup.add_argument('--candidate', '--candidate-sha', dest='candidate', required=True)
    cleanup.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'archive':
            manifest = archive_evidence(args.evidence, args.output,
                roots={str(i): p for i, p in enumerate(args.source_root)}, candidate=args.candidate,
                source_hash=args.source_hash, run_id=args.run_id, retention_days=args.retention_days,
                max_bytes=args.max_bytes, private=tuple(args.private))
            print(canonical({'manifest_hash': manifest['content_hash'], 'archive': manifest['archive'],
                             'file_count': manifest['file_count'], 'total_bytes': manifest['total_bytes'],
                             'full_product_accepted': False}).decode())
        elif args.command == 'verify':
            print(canonical(verify_archive(args.archive, read_json(args.manifest),
                expected_manifest_hash=args.expected_manifest_hash, expected_candidate=args.candidate,
                expected_run_id=args.run_id, expected_source_hash=args.source_hash)).decode())
        else:
            cleanup_archived_private(read_json(args.plan), args.evidence, args.attempt,
                args.archive, read_json(args.manifest), expected_manifest_hash=args.expected_manifest_hash,
                expected_candidate=args.candidate, run_id=args.run_id)
            print(canonical({'cleaned_private': args.attempt + '/private', 'run_id': args.run_id,
                             'manifest_hash': args.expected_manifest_hash,
                             'full_product_accepted': False}).decode())
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(canonical({'verified': False, 'error': str(error), 'full_product_accepted': False}).decode())
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
