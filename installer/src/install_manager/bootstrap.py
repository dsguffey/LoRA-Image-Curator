"""Delivered LIC Lite bootstrap workflow; activation remains a separate gated operation."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import zipfile

from .acquisition import (AcquisitionCancelled, AcquisitionPolicy, acquire_artifact,
                          admit_local_artifact, sha256_file)
from .activation import evaluate_criteria
from .artifacts import load_artifact_descriptor
from .bootstrap_layout import layout, validate_root, reject_reparse_entries
from .dependencies import install_locked_wheels
from .dependency_lock import load_dependency_lock
from .environment import create_final_path_venv, extract_runtime
from .journal import OperationJournal
from .lic_readiness import CORE_READINESS_CRITERIA, run_probe
from .model_resources import ensure_model  # compatibility seam for historical fixture patching
from .process_lock import process_lock
from .probe_guard import isolated_environment
from .recipe import load_recipe
from .release_channel import validate_channel
from .recovery import core_bootstrap_plan_digest
from .staging import stage_package
from .validation import validate_environment
from .verification import verify_package

HOSTS = ('www.python.org', 'files.pythonhosted.org')
STEPS = ('acquire_runtime', 'acquire_dependencies', 'extract_runtime', 'create_venv',
         'install_dependencies', 'stage_lic', 'validate', 'readiness')
STEP_LABELS = {
    'acquire_runtime': 'Preparing Python from python.org',
    'acquire_dependencies': 'Preparing approved required packages from PyPI',
    'extract_runtime': 'Extracting Python',
    'create_venv': 'Creating environment',
    'install_dependencies': 'Installing required dependencies',
    'stage_lic': 'Staging LIC Lite',
    'validate': 'Testing the private environment',
    'readiness': 'Testing baseline LIC readiness',
}


class BootstrapIdentityMismatch(ValueError):
    """Resume was requested with inputs different from the authoritative journal."""


def ensure_selected_model(*args, **kwargs):
    """Compatibility export for M1.8 callers; new Core execution never calls it."""
    from .legacy_bootstrap import ensure_selected_model as legacy_ensure_selected_model
    return legacy_ensure_selected_model(*args, **kwargs)


def profile(delivery: Path):
    recipes = delivery / 'recipes'
    recipe = load_recipe(recipes / 'lic.json')
    if not recipe.install_execution_allowed:
        raise ValueError('LIC recipe does not authorize installation execution')
    if not recipe.activation_allowed:
        raise ValueError('LIC recipe does not authorize activation')
    runtime = load_artifact_descriptor(recipes / 'python-3.14.6-windows-x64.json')
    lock = load_dependency_lock(recipes / 'core-windows-x64-v2.json')
    channel = validate_channel(json.loads((recipes / 'lic-core-m111a-candidate.json').read_text()), qualification=True,
                               activation_capable=True)
    actual = {'application_sha256': recipe.expected_sha256, 'runtime_sha256': runtime.expected_sha256,
              'dependency_digest': lock.digest()}
    if any(channel[k] != v for k, v in actual.items()) or channel['profile'] != lock.profile:
        raise ValueError('channel/profile identity mismatch')
    if channel['readiness_contract'] != 'lic-core-readiness-v2':
        raise ValueError('channel readiness contract mismatch')
    catalog_digest = hashlib.sha256((recipes / 'lic-components.json').read_bytes()).hexdigest()
    if channel['component_catalog_digest'] != catalog_digest:
        raise ValueError('channel component-catalog identity mismatch')
    return recipe, runtime, lock, None, channel


def disclosure(delivery: Path, root: Path, *, model_root: Path | None = None) -> dict:
    recipe, runtime, lock, _model, channel = profile(delivery)
    places = layout(root)
    descriptors = (runtime, *(wheel.artifact for wheel in lock.wheels))
    offline = delivery / 'offline-artifacts'
    bundled = all((offline / 'verified' / item.artifact_id / item.version / item.filename).is_file()
                  for item in descriptors)
    return {'title': 'LIC Install Manager', 'release': channel['release_id'],
            'install_location': str(root), 'administrator_required': False,
            'download_bytes_without_reuse': 0 if bundled else 100_000_000,
            'download_size_note': ('No Core downloads are needed; this Lite package contains the approved Core artifacts.'
                                   if bundled else 'Approximately 100 MB. Some upstream wheel sizes are not recorded in the lock.'),
            'free_space_required_gib': 2,
            'summary': 'Install LoRA Image Curator, a private Python 3.14.6 runtime, and the exact packages '
                       'required for catalog, review, editing, readiness and export. Pressing Install Core '
                       'authorizes only the listed Core setup work. '
                       'Optional AI providers are installed separately. LIC can operate offline afterward. '
                       'No existing LIC data is used.',
            'layout': {k: str(v) for k, v in places.items()}, 'activated': False,
            'details': {
                'LIC Lite': {'version': recipe.version, 'artifact': recipe.artifact_id,
                             'sha256': recipe.expected_sha256, 'source': recipe.source},
                'Python': {'version': runtime.version, 'artifact': runtime.artifact_id,
                           'sha256': runtime.expected_sha256, 'source': runtime.source_name},
                'Required dependencies': {'profile': lock.profile, 'digest': lock.digest(),
                                          'packages': [f'{wheel.name}=={wheel.version}' for wheel in lock.wheels],
                                          'sources': ['files.pythonhosted.org']},
                'notices': ['LIC Lite/LICENSE', 'LIC Lite/THIRD_PARTY_NOTICE.md',
                            'LIC Install Manager third-party inventory'],
            },
            'shortcuts': {'start_menu_default': True, 'desktop_default': False}}


def verify_extracted(archive: Path, target: Path) -> int:
    with zipfile.ZipFile(archive) as source:
        files = [i for i in source.infolist() if not i.is_dir()]
        expected = {i.filename for i in files}
        actual = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
        # Bytecode may be generated by CPython itself; it never substitutes for source verification.
        actual = {p for p in actual if p in expected or ('/__pycache__/' not in '/' + p and not p.endswith('.pyc'))}
        if actual != expected:
            raise ValueError('extracted immutable payload coverage changed')
        for item in files:
            path = target / item.filename
            if target.resolve() not in path.resolve().parents or sha256_file(path) != hashlib.sha256(source.read(item)).hexdigest():
                raise ValueError('extracted immutable payload changed: ' + item.filename)
    return len(files)


def execute(delivery: Path, root: Path, *, cache_source: Path | None = None,
            model_source: Path | None = None, model_root: Path | None = None,
            resume=False, status=lambda text: None,
            boundary=lambda step: None, cancel_requested=lambda: False) -> dict:
    if resume:
        journal_path = root.resolve() / 'State/operations/bootstrap.json'
        if journal_path.is_file():
            recorded = json.loads(journal_path.read_text(encoding='utf-8'))
            recorded_steps = tuple(step.get('name') for step in recorded.get('steps', ()))
            from .legacy_bootstrap import STEPS as LEGACY_STEPS
            if recorded_steps == LEGACY_STEPS:
                from .legacy_bootstrap import (BootstrapIdentityMismatch as LegacyIdentityMismatch,
                                               execute as execute_legacy)
                try:
                    return execute_legacy(delivery, root, cache_source=cache_source,
                                          model_source=model_source, model_root=model_root,
                                          resume=True, status=status, boundary=boundary,
                                          cancel_requested=cancel_requested)
                except LegacyIdentityMismatch as error:
                    raise BootstrapIdentityMismatch(str(error)) from error
    root = validate_root(root, delivery=delivery, resume=resume)
    paths = layout(root)
    recipe, runtime, lock, _model, channel = profile(delivery)
    package = delivery / 'artifacts/lic-lite.zip'
    if not verify_package(package, recipe).verified:
        raise ValueError('LIC Lite package verification failed')
    for source in (cache_source,):
        if source is not None and (source.resolve() == root or source.resolve() in root.parents or root in source.resolve().parents):
            raise ValueError('reuse source overlaps installation target')
    identity = core_bootstrap_plan_digest(channel, root)
    with process_lock(root):
        journal_path = paths['state'] / 'operations/bootstrap.json'
        if resume:
            reject_reparse_entries(root)
            journal = OperationJournal.load(journal_path)
            if (journal.data['plan_digest'] != identity or journal.data['target_path'] != root.as_posix()
                    or tuple(s['name'] for s in journal.data['steps']) != STEPS):
                raise BootstrapIdentityMismatch('journal does not belong to this exact target/profile')
            if journal.data['status'] == 'succeeded':
                raise ValueError('Qualification already finished; inspect its report. Activation remains unavailable.')
        else:
            # Recheck under the operation lock, before taking ownership.
            validate_root(root, delivery=delivery)
            root.mkdir(parents=True, exist_ok=True)
            paths['logs'].mkdir(parents=True, exist_ok=True)
            journal = OperationJournal.create(paths['state'] / 'operations', 'bootstrap', target_path=root,
                                              plan_digest=identity, artifacts=[channel], steps=STEPS,
                                              inputs={'install_root': str(root),
                                                      'identity_contract': 'lic-core-v2'})
        paths['logs'].mkdir(parents=True, exist_ok=True)
        log_path = paths['logs'] / 'bootstrap.log'

        current_number = 0

        def emit(message, *, terminal=None):
            with log_path.open('a', encoding='utf-8') as stream:
                stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                stream.write(f'{stamp} {message}\n')
                stream.flush()
                os.fsync(stream.fileno())
            status({'kind': 'status', 'message': message, 'step': current_number,
                    'total_steps': len(STEPS), 'terminal': terminal})

        current_source = ['the approved source']

        def progress_event(event):
            if cancel_requested():
                raise AcquisitionCancelled('Cancellation requested')
            downloaded = event['downloaded_bytes']
            total = event.get('total_bytes')
            amount = f'{downloaded / (1024 * 1024):,.1f} MiB'
            if total:
                amount += f' / {total / (1024 * 1024):,.1f} MiB ({downloaded * 100 / total:.1f}%)'
            status({**event, 'message': f"Downloading from {current_source[0]} — {event['artifact']}: {amount}",
                    'step': current_number, 'total_steps': len(STEPS), 'terminal': None})
        if resume:
            emit('Resuming recorded work. Rechecking cached artifacts and immutable files before reuse.')
        steps = {s['name']: s.copy() for s in journal.data['steps']}
        journal.set_status('running')
        results = {}
        current = ''
        policy = AcquisitionPolicy(HOSTS)
        previous_environment = dict(os.environ)
        context = isolated_environment(paths['state'] / 'Process', paths['models'] / 'huggingface/hub')
        os.environ.clear()
        os.environ.update(context)

        def acquire(descriptor):
            current_source[0] = descriptor.source_name
            if cache_source:
                candidate = cache_source / 'verified' / descriptor.artifact_id / descriptor.version / descriptor.filename
                if candidate.is_file():
                    return admit_local_artifact(descriptor, candidate, paths['cache'], policy)
            bundled = delivery / 'offline-artifacts/verified' / descriptor.artifact_id / descriptor.version / descriptor.filename
            if bundled.is_file():
                current_source[0] = 'this LIC Lite package'
                return admit_local_artifact(descriptor, bundled, paths['cache'], policy)
            return acquire_artifact(descriptor, paths['cache'], policy,
                                    ca_bundle=delivery / 'trust/cacert.pem', progress=progress_event,
                                    partial_observer=lambda path, state: (
                                        journal.record_unvalidated_acquisition(path)
                                        if state in {'created', 'unvalidated'}
                                        else journal.clear_unvalidated_acquisition(path)),
                                    keep_partial_on_cancel=True)

        try:
            for current_number, current in enumerate(STEPS, 1):
                if cancel_requested():
                    raise AcquisitionCancelled('Cancellation requested')
                prior = steps[current]
                emit(f'[{current_number}/{len(STEPS)}] {STEP_LABELS[current]}')
                journal.set_step(current, 'running')
                if current == 'acquire_runtime':
                    runtime_artifact = acquire(runtime)
                    result = runtime_artifact.as_dict()
                elif current == 'acquire_dependencies':
                    wheels = tuple(acquire(w.artifact) for w in lock.wheels)
                    result = {'verified': True, 'count': len(wheels), 'reused': sum(w.reused for w in wheels)}
                elif current == 'extract_runtime':
                    if paths['runtime'].exists():
                        if prior['status'] != 'completed':
                            raise ValueError('Unconfirmed runtime publication needs review; no files removed')
                    else:
                        extract_runtime(runtime_artifact, paths['runtime'])
                    result = {'verified': True, 'files': verify_extracted(Path(runtime_artifact.cache_path), paths['runtime'])}
                elif current == 'create_venv':
                    if paths['venv'].exists():
                        if prior['status'] != 'completed':
                            raise ValueError('Interrupted or unknown venv requires review; choose a fresh target')
                        python = paths['venv'] / 'Scripts/python.exe'
                        if not python.is_file():
                            raise ValueError('Previously completed venv is missing its interpreter')
                    else:
                        python = create_final_path_venv(paths['runtime'] / 'python.exe', paths['venv'],
                                                       approved_root=root, log_path=paths['logs'] / 'venv.log')
                    result = {'python': str(python), 'final_path': str(paths['venv'])}
                elif current == 'install_dependencies':
                    if prior['status'] in ('running', 'failed'):
                        raise ValueError('Dependency installation was interrupted; retain diagnostics and use a fresh target')
                    if prior['status'] == 'completed':
                        revalidation = validate_environment(python, paths['venv'], paths['runtime'], lock,
                                                            require_cuda=False)
                        if not revalidation['passed']:
                            raise RuntimeError('Previously installed dependencies failed resume validation')
                        result = {**prior['evidence'], 'resume_revalidation': revalidation}
                    else:
                        result = install_locked_wheels(python, lock, wheels, paths['logs'] / 'dependencies.log')
                elif current == 'stage_lic':
                    if paths['application'].exists():
                        if prior['status'] != 'completed':
                            raise ValueError('Incomplete application staging requires review')
                    else:
                        stage_package(package, paths['application'], recipe)
                    source = paths['application'] / 'extracted'
                    result = {'verified': True, 'files': verify_extracted(package, source)}
                elif current == 'validate':
                    result = validate_environment(python, paths['venv'], paths['runtime'], lock, require_cuda=False,
                                                  lic_source_root=source)
                    if not result['passed']:
                        raise RuntimeError('Core validation failed; see diagnostics')
                elif current == 'readiness':
                    if prior['status'] != 'planned':
                        raise ValueError('Prior readiness evidence requires review; the probe is never automatically repeated')
                    result = run_probe('core-readiness', python, source, None, paths['probes'] / current,
                                       paths['models'] / 'huggingface/hub')
                results[current] = result
                journal.set_step(current, 'completed', evidence=result)
                boundary(current)  # Acceptance harness may terminate at a durable boundary; no production skip.
                current = ''
                if cancel_requested():
                    raise AcquisitionCancelled('Cancellation requested at a safe boundary')
            validation = results['validate']
            checks = {'application_verified': results['stage_lic']['verified'],
                      'runtime_verified': results['extract_runtime']['verified'],
                      'final_venv_valid': validation['path_consistency'],
                      'dependencies_exact': validation['passed'], 'pip_check': validation['pip_check']['exit_code'] == 0,
                      'required_imports': validation['passed'], 'target_safe': True, 'recovery_available': True,
                      'journal_complete': all(s['status'] == 'completed' for s in journal.data['steps']),
                      'application_ready': results['readiness']['passed'],
                      'offline': not results['readiness']['network_attempts']}
            criteria = evaluate_criteria(checks, provider_criteria=CORE_READINESS_CRITERIA)
            if cancel_requested():
                raise AcquisitionCancelled('Cancellation requested at the final safe boundary')
            if not criteria['criteria_met']:
                raise RuntimeError('Mandatory activation criteria failed')
            report = {'state': criteria['state'], 'activation_preflight_passed': criteria['criteria_met'],
                      'activated': False, 'optional_capabilities': {'florence': 'not-required'},
                      'criteria': criteria, 'results': results}
            journal.set_validation(report)
            journal.set_status('succeeded')
            emit('Testing-ready. All activation preflight checks passed. LIC has not been activated.',
                 terminal='success')
            return report
        except AcquisitionCancelled as error:
            if current:
                journal.set_step(current, 'cancelled', error=f'{type(error).__name__}: {error}')
            journal.set_status('cancelled', failure=f'{type(error).__name__}: {error}')
            emit('Paused safely. Verified completed work and operation state were retained for Resume.',
                 terminal='cancelled')
            raise
        except BaseException as error:
            if current:
                journal.set_step(current, 'failed', error=f'{type(error).__name__}: {error}')
            journal.set_status('failed', failure=f'{type(error).__name__}: {error}')
            emit(f'Stopped safely: {type(error).__name__}: {error}. Existing files retained. '
                 'Review State/operations/bootstrap.json and Logs/bootstrap.log.', terminal='error')
            raise
        finally:
            os.environ.clear()
            os.environ.update(previous_environment)
