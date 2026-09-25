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
from .artifacts import AcquiredArtifact
from .dependencies import install_locked_wheels
from .environment import create_final_path_venv
from .dependency_lock import load_dependency_lock
from .hf_source import HuggingFaceSource, hf_snapshot_target
from .journal import OperationJournal
from .lic_readiness import lic_model_policy, run_probe
from .model_resources import ensure_model, load_model, verify_snapshot
from .managed_resources import (managed_artifact_path, managed_data_layout, promote_package,
                                synchronize_resource_library)
from .process_lock import process_lock
from .storage import inspect_model_storage, validate_model_root
from .validation import validate_environment
from .active_venv import managed_venv
from .provider_venv import (inventory_for_generation, new_generation,
                            promote_generation, record_candidate,
                            restore_incomplete_promotion)
from .compatibility_profiles import (ResolvedDependencyProfile, dependency_profile_for_components,
                                     recommended_profile)
from .component_state import (ResourceState, component_from_manifest,
                              load_or_project_inventory, replace_component,
                              with_profile)
from .artifacts import ArtifactDescriptor
from .dependency_lock import normalize_distribution

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
    completed = sum(step.get('status') == 'completed' for step in data.get('steps', ()))
    blocked = not identity_matches or not locations_match
    resumable = status in {'planned', 'running', 'cancelled'} and not blocked
    if not identity_matches:
        summary = ('This interrupted Florence setup belongs to a different approved recipe. '
                   'Its journal was preserved for review.')
    elif not locations_match:
        summary = ('This interrupted Florence setup is bound to its recorded application and model '
                   'locations. Restore those selections before resuming.')
    elif status == 'failed':
        summary = ('Florence setup failed. Review its diagnostic, then choose Repair for a fresh '
                   'attempt. Core remains on its previous environment.')
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
    active_path = root / 'State/installations/lic-lite.json'
    if active_path.is_file():
        active = json.loads(active_path.read_text(encoding='utf-8'))
        inventory = load_or_project_inventory(delivery, root, active)
        identity['installed_components'] = sorted(inventory.installed_component_ids)
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
    journal_path = root / 'State/operations/florence.json'
    if journal_path.is_file():
        with process_lock(root):
            restore_incomplete_promotion(root, OperationJournal.load(journal_path))
    active_venv = managed_venv(root)
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
    compatibility = recommended_profile(delivery / 'recipes/compatibility/profiles')
    inventory = with_profile(load_or_project_inventory(delivery, root, active), compatibility)
    core_id = next(item.component_id for item in compatibility.components.values() if item.tier == 'core')
    installed_ids = set(inventory.installed_component_ids) | {core_id}
    prior_lock = dependency_profile_for_components(compatibility, installed_ids)
    final_lock = dependency_profile_for_components(compatibility, installed_ids | {'florence-captioning'})
    prior_names = set(prior_lock.expected_inventory)
    delta = ResolvedDependencyProfile(final_lock.profile + '-florence-delta',
                                      final_lock.python_version, final_lock.platform,
                                      tuple(wheel for wheel in final_lock.wheels
                                            if normalize_distribution(wheel.name) not in prior_names))
    identity = operation_digest(delivery, root, model_root)
    with process_lock(root):
        if resume:
            journal = OperationJournal.load(journal_path)
            if journal.data.get('status') not in {'planned', 'running', 'cancelled'}:
                raise ValueError('This Florence attempt failed or finished; choose Repair for a fresh attempt')
            if (journal.data.get('plan_digest') != identity or
                    tuple(s.get('name') for s in journal.data.get('steps', ())) != STEPS):
                raise ValueError('Florence journal does not match this exact authorized plan')
            reject_reparse_entries(root)
        else:
            if journal_path.exists():
                try:
                    prior = OperationJournal.load(journal_path)
                    recovery = inspect_recovery(delivery, root, model_root)
                except (OSError, ValueError, KeyError, TypeError):
                    prior, recovery = None, None
                if prior is not None and prior.data.get('status') != 'succeeded' and recovery and recovery.resumable:
                    raise ValueError('An interrupted Florence plan exists; use Resume with its recorded location')
                history = journal_path.parent / 'history'
                history.mkdir(parents=True, exist_ok=True)
                archived = history / f'florence-stale-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}.json'
                suffix = 1
                while archived.exists():
                    archived = history / f'florence-stale-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}-{suffix}.json'
                    suffix += 1
                journal_path.replace(archived)
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
        candidate_venv = None
        acquired_wheels = ()

        def emit(message, *, terminal=None):
            with log_path.open('a', encoding='utf-8') as stream:
                stream.write(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} {message}\n')
            status({'kind': 'status', 'message': message, 'step': current_number,
                    'total_steps': len(STEPS), 'terminal': terminal})

        def progress(event):
            if cancel_requested():
                raise AcquisitionCancelled('Cancellation requested')
            status({**event, 'step': current_number, 'total_steps': len(STEPS), 'terminal': None})

        def record_failure_diagnostic(error):
            try:
                process_exit_code = json.loads(str(error)).get('exit_code')
            except (ValueError, TypeError, AttributeError):
                process_exit_code = None
            try:
                report = validate_environment(
                    active_venv / 'Scripts/python.exe', active_venv,
                    paths['runtime'], prior_lock,
                    require_cuda=any(w.name == 'torch' for w in prior_lock.wheels))
                core_status = {'passed': bool(report.get('passed'))}
            except Exception as check_error:
                core_status = {'passed': False, 'error_class': type(check_error).__name__}
            journal.data['provider_diagnostic'] = {
                'component': 'florence-captioning', 'install_root': str(root),
                'step': current, 'old_venv': str(active_venv),
                'candidate_venv': str(candidate_venv or ''),
                'python_executable': str((candidate_venv or active_venv) / 'Scripts/python.exe'),
                'command_category': 'package-install' if current == 'install_dependencies' else current,
                'process_exit_code': process_exit_code,
                'exception_class': type(error).__name__,
                'core_validation_after_failure': core_status,
                'journal_status_after_failure': journal.data.get('status'),
            }
            try:
                journal._write()
            except OSError:
                pass

        def acquire(descriptor, *, allow_network=True):
            managed = managed_artifact_path(delivery, root, descriptor.artifact_id,
                                            descriptor.version, descriptor.expected_sha256)
            if managed is not None and managed.is_file():
                return AcquiredArtifact(descriptor, managed.as_posix(), descriptor.expected_sha256,
                                        managed.stat().st_size, 'verified', True, True, 0)
            downloads = managed_data_layout(root)['downloads']
            for source in (cache_source, root / 'Cache', delivery / 'offline-artifacts'):
                if source is None:
                    continue
                candidate = source / 'verified' / descriptor.artifact_id / descriptor.version / descriptor.filename
                if candidate.is_file():
                    return admit_local_artifact(descriptor, candidate, downloads, policy)
            if not allow_network:
                raise FileNotFoundError(
                    f'Previously installed package {descriptor.artifact_id} {descriptor.version} is absent from verified local storage')
            return acquire_artifact(descriptor, downloads, policy,
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
                    result = validate_environment(active_venv / 'Scripts/python.exe', active_venv,
                                                  paths['runtime'], prior_lock,
                                                  require_cuda=any(w.name == 'torch' for w in prior_lock.wheels))
                    if not result['passed']:
                        raise RuntimeError('Core must be healthy before Florence is installed')
                elif current == 'acquire_dependencies':
                    disclosed_hashes = {w.artifact.expected_sha256 for w in delta.wheels}
                    acquired_wheels = tuple(acquire(ArtifactDescriptor.from_dict(w.artifact.as_dict()),
                                                    allow_network=w.artifact.expected_sha256 in disclosed_hashes)
                                            for w in final_lock.wheels)
                    result = {'verified': True, 'count': len(acquired_wheels),
                              'reused': sum(item.reused for item in acquired_wheels)}
                elif current == 'install_dependencies':
                    for acquired in acquired_wheels:
                        promote_package(delivery, root, Path(acquired.cache_path),
                                        acquired.descriptor.artifact_id,
                                        acquired.descriptor.version, acquired.actual_sha256)
                    candidate_venv = new_generation(root)
                    record_candidate(journal, root, active, inventory, active_venv, candidate_venv)
                    candidate_python = create_final_path_venv(
                        paths['runtime'] / 'python.exe', candidate_venv, approved_root=root,
                        log_path=paths['logs'] / 'florence-create.log')
                    result = install_locked_wheels(candidate_python, final_lock, acquired_wheels,
                                                   paths['logs'] / 'florence-dependencies.log')
                    result['candidate_venv'] = str(candidate_venv)
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
                    if candidate_venv is None:
                        raise RuntimeError('Florence replacement environment was not built')
                    result = validate_environment(candidate_venv / 'Scripts/python.exe', candidate_venv,
                                                  paths['runtime'], final_lock, require_cuda=True)
                    if not result['passed']:
                        raise RuntimeError('Florence runtime validation failed')
                elif current == 'caption':
                    result = run_probe('caption', candidate_venv / 'Scripts/python.exe',
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
            previous_record = target.read_bytes() if target.is_file() else None
            resource = ResourceState(
                'florence-model', 'model', 'huggingface-snapshot-v1', str(snapshot),
                'manager-owned', 'copy-and-verify', {'digest': model.digest()},
                {'kind': 'huggingface', 'repository': 'florence-community/Florence-2-large-ft'},
                {'version': 'florence-caption-v1', 'passed': True})
            component = component_from_manifest(
                compatibility.component_by_id('florence-captioning'),
                state='installed', enabled=True,
                readiness={'version': 'florence-caption-v1', 'passed': True},
                resources=(resource,))
            updated_inventory = inventory_for_generation(
                replace_component(inventory, component), candidate_venv)

            def validate_and_publish():
                report = validate_environment(candidate_venv / 'Scripts/python.exe', candidate_venv,
                                              paths['runtime'], final_lock, require_cuda=True)
                if not report['passed']:
                    raise RuntimeError('Promoted Florence environment did not pass validation')
                temporary = target.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
                os.replace(temporary, target)
                synchronize_resource_library(delivery, root)

            try:
                promote_generation(root, journal, candidate_venv, active,
                                   inventory, updated_inventory, validate_and_publish)
            except BaseException:
                if previous_record is None:
                    target.unlink(missing_ok=True)
                else:
                    temporary = target.with_suffix('.json.tmp')
                    temporary.write_bytes(previous_record)
                    os.replace(temporary, target)
                raise
            record['component_inventory'] = str(root / 'State/components/inventory.json')
            journal.set_validation({'passed': True, 'optional_capability_ready': True})
            journal.set_status('succeeded')
            emit('Florence is installed and verified.', terminal='success')
            return record
        except AcquisitionCancelled as error:
            if current:
                journal.set_step(current, 'cancelled', error=f'{type(error).__name__}: {error}')
            journal.set_status('cancelled', failure=f'{type(error).__name__}: {error}')
            record_failure_diagnostic(error)
            emit('Florence installation paused safely. Verified work was preserved for Resume.', terminal='cancelled')
            raise
        except BaseException as error:
            if current:
                journal.set_step(current, 'failed', error=f'{type(error).__name__}: {error}')
            journal.set_status('failed', failure=f'{type(error).__name__}: {error}')
            record_failure_diagnostic(error)
            emit(f'Florence installation stopped safely: {error}', terminal='error')
            raise
