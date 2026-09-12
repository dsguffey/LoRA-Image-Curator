"""Explicit, journaled installation of the optional Florence capability."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .acquisition import (AcquisitionCancelled, AcquisitionPolicy, acquire_artifact,
                          admit_local_artifact)
from .bootstrap_layout import layout, reject_reparse_entries
from .dependencies import install_locked_wheels
from .dependency_lock import load_dependency_lock
from .hf_source import HuggingFaceSource, hf_snapshot_target
from .journal import OperationJournal
from .lic_readiness import lic_model_policy, run_probe
from .model_resources import ensure_model, load_model, verify_snapshot
from .process_lock import process_lock
from .storage import inspect_model_storage, validate_model_root
from .validation import validate_environment

HOSTS = ('files.pythonhosted.org', 'download-r2.pytorch.org')
STEPS = ('verify_core', 'acquire_dependencies', 'install_dependencies',
         'ensure_model', 'validate', 'caption')


@dataclass(frozen=True, slots=True)
class FlorenceRecovery:
    journal_path: Path
    status: str
    recorded_install_root: Path
    recorded_model_root: Path
    current_install_root: Path
    current_model_root: Path
    completed_steps: int
    total_steps: int
    resumable: bool
    blocked: bool
    summary: str


def inspect_recovery(delivery: Path, root: Path,
                     model_root: Path) -> FlorenceRecovery | None:
    """Inspect an optional-operation journal without changing it or acquiring content."""
    journal_path = root.resolve() / 'State/operations/florence.json'
    if not journal_path.is_file():
        return None
    journal = OperationJournal.load(journal_path)
    data = journal.data
    status = str(data.get('status', ''))
    if status == 'succeeded':
        return None
    inputs = data.get('inputs') or {}
    recorded_root = Path(inputs['install_root']).resolve()
    recorded_models = Path(inputs['model_root']).resolve()
    current_root, current_models = root.resolve(), model_root.resolve()
    names = tuple(step.get('name') for step in data.get('steps', ()))
    identity_matches = (names == STEPS and
                        data.get('plan_digest') == operation_digest(
                            delivery, recorded_root, recorded_models))
    locations_match = recorded_root == current_root and recorded_models == current_models
    unsafe_mutation = any(step.get('name') == 'install_dependencies' and
                          step.get('status') in {'running', 'failed'}
                          for step in data.get('steps', ()))
    completed = sum(step.get('status') == 'completed' for step in data.get('steps', ()))
    blocked = not identity_matches or not locations_match or unsafe_mutation
    resumable = status in {'planned', 'running', 'cancelled', 'failed'} and not blocked
    if not identity_matches:
        summary = ('This interrupted Florence setup belongs to a different approved recipe. '
                   'Its journal was preserved for review.')
    elif not locations_match:
        summary = ('This interrupted Florence setup is bound to its recorded application and model '
                   'locations. Restore those selections before resuming.')
    elif unsafe_mutation:
        summary = ('Florence package installation was interrupted. Files were preserved, but this '
                   'environment requires a reviewed repair before acquisition can continue.')
    else:
        summary = ('Florence setup was interrupted. Resume continues only the previously authorized '
                   'recipe and reuses verified work.')
    return FlorenceRecovery(journal_path.resolve(), status, recorded_root, recorded_models,
                            current_root, current_models, completed, len(names), resumable,
                            blocked, summary)


def profile(delivery: Path):
    recipes = delivery / 'recipes'
    core = load_dependency_lock(recipes / 'core-windows-x64-v2.json')
    delta = load_dependency_lock(recipes / 'florence-windows-nvidia-cu130-v1.json')
    combined = load_dependency_lock(recipes / 'base-windows-nvidia-cu130.json')
    model = load_model(recipes / 'florence2-large-ft.json')
    lic_model_policy(model)
    def wheel_identity(lock):
        return {wheel.name.casefold(): (wheel.version, wheel.artifact.as_dict(), wheel.import_names)
                for wheel in lock.wheels}
    composed = {**wheel_identity(core), **wheel_identity(delta)}
    if composed != wheel_identity(combined):
        raise ValueError('Florence package closure does not compose with Core')
    return core, delta, combined, model


def operation_digest(delivery: Path, root: Path, model_root: Path) -> str:
    core, delta, combined, model = profile(delivery)
    identity = {'operation': 'florence-managed-v1', 'root': str(root.resolve()),
                'model_root': str(model_root.resolve()), 'core': core.digest(),
                'delta': delta.digest(), 'combined': combined.digest(), 'model': model.digest()}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def inspect_installed(delivery: Path, root: Path, model_root: Path, record: dict | None) -> dict:
    """Read local records/model files only; never acquire or update."""
    evidence = inspect_model_storage(delivery, model_root)
    record_path = root / 'State/components/florence.json'
    managed = None
    if record_path.is_file():
        try:
            managed = json.loads(record_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            managed = None
    channel = record.get('channel') if record else None
    legacy = bool(isinstance(channel, dict) and channel.get('schema_version') == 1)
    runtime_ready = bool((managed and managed.get('state') == 'installed') or legacy)
    ready = runtime_ready and evidence.get('status') == 'compatible'
    return {'ready': ready, 'runtime_ready': runtime_ready, 'model': evidence,
            'legacy': legacy, 'record': managed}


def execute(delivery: Path, root: Path, model_root: Path, *, cache_source: Path | None = None,
            resume: bool = False, status=lambda event: None,
            cancel_requested=lambda: False) -> dict:
    """Install only after the caller's explicit Florence Install/Resume action."""
    root = root.resolve()
    paths = layout(root)
    model_root = validate_model_root(model_root, max_length=240)
    core, delta, combined, model = profile(delivery)
    initial_model_state = inspect_model_storage(delivery, model_root)
    if initial_model_state.get('status') in {'incompatible', 'ambiguous'}:
        raise ValueError(initial_model_state.get('message', 'The selected Florence model is incompatible'))
    if initial_model_state.get('status') == 'missing':
        # Download targets need room for the canonical repository/revision suffix.
        validate_model_root(model_root)
    active_path = root / 'State/installations/lic-lite.json'
    if not active_path.is_file():
        raise ValueError('Install LoRA Image Curator Core before adding Florence')
    active = json.loads(active_path.read_text(encoding='utf-8'))
    if active.get('state') != 'active' or Path(active.get('root', '')).resolve() != root:
        raise ValueError('Core activation record is missing or belongs to another location')
    identity = operation_digest(delivery, root, model_root)
    journal_path = root / 'State/operations/florence.json'
    with process_lock(root):
        if resume:
            journal = OperationJournal.load(journal_path)
            if (journal.data.get('plan_digest') != identity or
                    tuple(s.get('name') for s in journal.data.get('steps', ())) != STEPS):
                raise ValueError('Florence journal does not match this exact authorized plan')
            reject_reparse_entries(root)
        else:
            if journal_path.exists():
                prior = OperationJournal.load(journal_path)
                if prior.data.get('status') != 'succeeded':
                    raise ValueError('An interrupted Florence plan exists; use Resume with its recorded location')
            journal = OperationJournal.create(journal_path.parent, 'florence', target_path=root,
                                              plan_digest=identity,
                                              artifacts=[{'component': 'florence-managed-v1',
                                                          'model_digest': model.digest(),
                                                          'dependency_digest': delta.digest()}],
                                              steps=STEPS,
                                              inputs={'install_root': str(root),
                                                      'model_root': str(model_root)})
        initial = {s['name']: dict(s) for s in journal.data['steps']}
        log_path = root / 'Logs/florence.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        policy = AcquisitionPolicy(HOSTS)
        current = ''
        results = {}
        current_number = 0

        def emit(message, *, terminal=None):
            with log_path.open('a', encoding='utf-8') as stream:
                stream.write(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} {message}\n')
            status({'kind': 'status', 'message': message, 'step': current_number,
                    'total_steps': len(STEPS), 'terminal': terminal})

        def progress(event):
            if cancel_requested():
                raise AcquisitionCancelled('Cancellation requested')
            status({**event, 'step': current_number, 'total_steps': len(STEPS), 'terminal': None})

        def acquire(descriptor):
            if cache_source:
                candidate = cache_source / 'verified' / descriptor.artifact_id / descriptor.version / descriptor.filename
                if candidate.is_file():
                    return admit_local_artifact(descriptor, candidate, paths['cache'], policy)
            return acquire_artifact(descriptor, paths['cache'], policy,
                                    ca_bundle=delivery / 'trust/cacert.pem', progress=progress,
                                    partial_observer=lambda path, state: (
                                        journal.record_unvalidated_acquisition(path)
                                        if state in {'created', 'unvalidated'}
                                        else journal.clear_unvalidated_acquisition(path)),
                                    keep_partial_on_cancel=True)

        journal.set_status('running')
        try:
            for current_number, current in enumerate(STEPS, 1):
                if cancel_requested():
                    raise AcquisitionCancelled('Cancellation requested')
                prior = initial[current]
                journal.set_step(current, 'running')
                emit(f'[{current_number}/{len(STEPS)}] {current.replace("_", " ").title()}')
                if current == 'verify_core':
                    result = validate_environment(paths['venv'] / 'Scripts/python.exe', paths['venv'],
                                                  paths['runtime'], core, require_cuda=False)
                    if not result['passed']:
                        raise RuntimeError('Core must be healthy before Florence is installed')
                elif current == 'acquire_dependencies':
                    wheels = tuple(acquire(w.artifact) for w in delta.wheels)
                    result = {'verified': True, 'count': len(wheels),
                              'reused': sum(item.reused for item in wheels)}
                elif current == 'install_dependencies':
                    if prior['status'] in {'running', 'failed'}:
                        raise ValueError('Interrupted package mutation requires reviewed repair; files were preserved')
                    if prior['status'] == 'completed':
                        result = prior['evidence']
                    else:
                        result = install_locked_wheels(paths['venv'] / 'Scripts/python.exe', delta, wheels,
                                                       paths['logs'] / 'florence-dependencies.log')
                elif current == 'ensure_model':
                    model_state = inspect_model_storage(delivery, model_root)
                    if model_state.get('status') == 'compatible':
                        snapshot = Path(model_state['snapshot'])
                        result = {**verify_snapshot(model, snapshot, revision=model.revision, strict=False),
                                  'reuse': 'selected-existing', 'acquired_files': 0}
                    else:
                        snapshot = hf_snapshot_target(model_root, model)
                        provider = HuggingFaceSource(ca_bundle=delivery / 'trust/cacert.pem', progress=progress)
                        result = ensure_model(model, snapshot, approved_root=model_root,
                                              fetch_file=provider.fetch_file)
                elif current == 'validate':
                    result = validate_environment(paths['venv'] / 'Scripts/python.exe', paths['venv'],
                                                  paths['runtime'], combined, require_cuda=True)
                    if not result['passed']:
                        raise RuntimeError('Florence runtime validation failed')
                elif current == 'caption':
                    if prior['status'] != 'planned':
                        raise ValueError('Prior inference evidence is never automatically repeated')
                    result = run_probe('caption', paths['venv'] / 'Scripts/python.exe',
                                       paths['application'] / 'extracted', snapshot,
                                       paths['probes'] / 'florence-caption',
                                       model_root / 'huggingface/hub')
                results[current] = result
                journal.set_step(current, 'completed', evidence=result)
                current = ''
            if cancel_requested():
                raise AcquisitionCancelled('Cancellation requested at the final safe boundary')
            record = {'schema_version': 1, 'component': 'florence-captioning',
                      'state': 'installed', 'model_root': str(model_root),
                      'snapshot': str(snapshot),
                      'hub_root': str(inspect_model_storage(delivery, model_root)['hub_root']),
                      'model_digest': model.digest(),
                      'dependency_digest': delta.digest(), 'validation': results['validate'],
                      'caption': results['caption']}
            target = root / 'State/components/florence.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            os.replace(temporary, target)
            journal.set_validation({'passed': True, 'optional_capability_ready': True})
            journal.set_status('succeeded')
            emit('Florence is installed and verified.', terminal='success')
            return record
        except AcquisitionCancelled as error:
            if current:
                journal.set_step(current, 'cancelled', error=f'{type(error).__name__}: {error}')
            journal.set_status('cancelled', failure=f'{type(error).__name__}: {error}')
            emit('Florence installation canceled safely. Verified work was preserved.', terminal='cancelled')
            raise
        except BaseException as error:
            if current:
                journal.set_step(current, 'failed', error=f'{type(error).__name__}: {error}')
            journal.set_status('failed', failure=f'{type(error).__name__}: {error}')
            emit(f'Florence installation stopped safely: {error}', terminal='error')
            raise
