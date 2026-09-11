"""Provider-neutral model identities, content verification and owned snapshot publication."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Callable
import uuid

from .acquisition import _artifact_lock


def safe_relative(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or name == '.' or path.is_absolute() or '..' in path.parts or path.as_posix() != name
            or any(c in name for c in '\\:') or any(ord(c) < 32 for c in name)
            or any(p.endswith((' ', '.')) for p in path.parts)
            or any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                   *('COM'+str(i) for i in range(1, 10)), *('LPT'+str(i) for i in range(1, 10))}
                   for p in path.parts)):
        raise ValueError(f'unsafe model filename: {name!r}')


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    digest_kind: str
    digest: str

    def validate(self) -> None:
        safe_relative(self.path)
        length = {'sha256': 64, 'git-blob-sha1': 40}.get(self.digest_kind)
        if self.size <= 0 or length is None or not re.fullmatch(r'[0-9a-f]{'+str(length)+'}', self.digest):
            raise ValueError('invalid model file identity')


@dataclass(frozen=True)
class ModelSnapshot:
    schema_version: int
    resource_id: str
    source: str
    repository: str
    revision: str
    family: str
    files: tuple[ModelFile, ...]
    provenance: dict
    license: dict
    compatibility: dict

    @classmethod
    def from_dict(cls, data: dict) -> 'ModelSnapshot':
        model = cls(int(data['schema_version']), str(data['resource_id']), str(data['source']),
                    str(data['repository']), str(data['revision']), str(data['family']),
                    tuple(ModelFile(**item) for item in data['files']),
                    data['provenance'], data['license'], data['compatibility'])
        if model.schema_version != 1 or not model.files or not model.revision:
            raise ValueError('incomplete model identity')
        names = [item.path.casefold() for item in model.files]
        if len(names) != len(set(names)):
            raise ValueError('duplicate/colliding model filenames')
        for item in model.files:
            item.validate()
        return model

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def load_model(path: Path) -> ModelSnapshot:
    return ModelSnapshot.from_dict(json.loads(path.read_text(encoding='utf-8')))


def verify_file(path: Path, expected: ModelFile) -> dict:
    expected.validate()
    if not path.is_file() or path.stat().st_size != expected.size:
        raise ValueError(f'missing or wrong-sized model file: {expected.path}')
    sha256 = hashlib.sha256()
    identity = hashlib.sha1(f'blob {expected.size}\0'.encode()) if expected.digest_kind == 'git-blob-sha1' else hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            sha256.update(chunk)
            identity.update(chunk)
    if identity.hexdigest() != expected.digest:
        raise ValueError(f'model content mismatch: {expected.path}')
    return {'path': expected.path, 'size': expected.size, 'sha256': sha256.hexdigest(),
            'upstream_digest_kind': expected.digest_kind, 'upstream_digest': expected.digest}


def verify_snapshot(model: ModelSnapshot, path: Path, *, revision: str, strict: bool = False) -> dict:
    if revision != model.revision:
        raise ValueError('wrong model revision')
    if not path.is_dir():
        raise ValueError('model snapshot is absent')
    # External HF snapshots may legitimately reference blobs by symlink. Read them only;
    # managed content is copied to independent regular files and must contain no links.
    if strict:
        entries = tuple(path.rglob('*'))
        if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in entries):
            raise ValueError('managed snapshot contains a link')
        actual = {p.relative_to(path).as_posix() for p in entries if p.is_file()}
        if actual != {item.path for item in model.files}:
            raise ValueError('managed snapshot coverage differs from model manifest')
    files = [verify_file(path / item.path, item) for item in model.files]
    return {'verified': True, 'resource_id': model.resource_id, 'source': model.source,
            'repository': model.repository, 'revision': model.revision,
            'manifest_sha256': model.digest(), 'local_path': path.resolve().as_posix(),
            'bytes': sum(item.size for item in model.files), 'files': files}


def ensure_model(model: ModelSnapshot, target: Path, *, approved_root: Path,
                 candidate: Path | None = None, candidate_revision: str | None = None,
                 fetch_file: Callable[[ModelSnapshot, ModelFile, Path], None] | None = None) -> dict:
    with _artifact_lock(target.resolve()):
        return _ensure_model(model, target, approved_root=approved_root, candidate=candidate,
                             candidate_revision=candidate_revision, fetch_file=fetch_file)


def _ensure_model(model: ModelSnapshot, target: Path, *, approved_root: Path,
                  candidate: Path | None, candidate_revision: str | None,
                  fetch_file: Callable[[ModelSnapshot, ModelFile, Path], None] | None) -> dict:
    """Verify reused content or publish a completely verified owned copy; never edit candidates."""
    target = target.resolve()
    approved_root = approved_root.resolve()
    if approved_root not in target.parents:
        raise ValueError('model target escapes approved shared root')
    if candidate is not None:
        candidate = candidate.resolve()
        if target == candidate or candidate in target.parents or target in candidate.parents:
            raise ValueError('managed target overlaps external candidate')
    record_path = target.with_name(target.name + '.resource.json')
    if target.exists():
        record = json.loads(record_path.read_text())
        if record.get('manifest_sha256') != model.digest() or record.get('revision') != model.revision:
            raise ValueError('managed model record conflicts with requested identity')
        evidence = verify_snapshot(model, target, revision=record['revision'], strict=True)
        return {**evidence, 'state': 'verified', 'reuse': 'managed', 'acquired_files': 0}
    if record_path.exists():
        raise ValueError('orphan model record requires review')
    if candidate is not None:
        verify_snapshot(model, candidate, revision=candidate_revision or '')
    elif fetch_file is None:
        raise ValueError('model absent; explicit acquisition permission is required')
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name('.' + target.name + '.partial-' + uuid.uuid4().hex)
    partial.mkdir()
    try:
        for item in model.files:
            destination = partial / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if candidate is not None:
                with (candidate / item.path).open('rb') as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            else:
                fetch_file(model, item, destination)
            verify_file(destination, item)
        evidence = verify_snapshot(model, partial, revision=model.revision, strict=True)
        evidence.update(local_path=target.as_posix(), state='verified',
                        reuse='external-copy' if candidate else 'acquired',
                        acquired_files=0 if candidate else len(model.files))
        os.replace(partial, target)
        temporary_record = record_path.with_suffix('.json.tmp')
        with temporary_record.open('x', encoding='utf-8') as stream:
            json.dump(evidence, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_record, record_path)
        return evidence
    except BaseException:
        # Only the uniquely created partial directory is owned by this attempt.
        if partial.is_dir() and partial.parent == target.parent:
            shutil.rmtree(partial)
        raise
