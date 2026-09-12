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
from .hf_source import HuggingFaceSource, hf_snapshot_target
from .journal import OperationJournal
from .lic_readiness import LIC_CRITERIA, lic_model_policy, run_probe
from .model_resources import ensure_model, load_model, verify_snapshot
from .process_lock import process_lock
from .probe_guard import isolated_environment
from .recipe import load_recipe
from .release_channel import validate_channel
from .recovery import bootstrap_plan_digest
from .staging import stage_package
from .storage import default_model_root, validate_model_root
from .validation import validate_environment
from .verification import verify_package

HOSTS = ('www.python.org', 'files.pythonhosted.org', 'download-r2.pytorch.org')
STEPS = ('acquire_runtime', 'acquire_dependencies', 'extract_runtime', 'create_venv',
         'install_dependencies', 'stage_lic', 'ensure_model', 'validate', 'caption', 'readiness')
STEP_LABELS = {
    'acquire_runtime': 'Preparing Python from python.org',
    'acquire_dependencies': 'Preparing approved packages from PyPI and PyTorch',
    'extract_runtime': 'Extracting Python',
    'create_venv': 'Creating environment',
    'install_dependencies': 'Installing AI dependencies',
    'stage_lic': 'Staging LIC Lite',
    'ensure_model': 'Downloading and verifying Florence-2 from Hugging Face',
    'validate': 'Testing GPU and environment',
    'caption': 'Testing Florence-2',
    'readiness': 'Finalizing LIC readiness',
}


class BootstrapIdentityMismatch(ValueError):
    """Resume was requested with inputs different from the authoritative journal."""


def profile(delivery: Path):
    recipes = delivery / 'recipes'
    recipe = load_recipe(recipes / 'lic.json')
    if not recipe.install_execution_allowed:
        raise ValueError('LIC recipe does not authorize installation execution')
    if not recipe.activation_allowed:
        raise ValueError('LIC recipe does not authorize activation')
    runtime = load_artifact_descriptor(recipes / 'python-3.14.6-windows-x64.json')
    lock = load_dependency_lock(recipes / 'base-windows-nvidia-cu130.json')
    model = load_model(recipes / 'florence2-large-ft.json')
    channel = validate_channel(json.loads((recipes / 'lic-candidate.json').read_text()), qualification=True,
                               activation_capable=True)
    actual = {'application_sha256': recipe.expected_sha256, 'runtime_sha256': runtime.expected_sha256,
              'model_digest': model.digest(), 'dependency_digest': lock.digest()}
    if any(channel[k] != v for k, v in actual.items()) or channel['profile'] != lock.profile:
        raise ValueError('channel/profile identity mismatch')
    lic_model_policy(model)
    return recipe, runtime, lock, model, channel


def disclosure(delivery: Path, root: Path, *, model_root: Path | None = None) -> dict:
    recipe, runtime, lock, model, channel = profile(delivery)
    model_root = validate_model_root(model_root or default_model_root(root))
    places = layout(root)
    places['models'] = model_root
    return {'title': 'LIC Install Manager', 'release': channel['release_id'],
            'install_location': str(root), 'administrator_required': False,
            'download_bytes_without_reuse': 3_600_000_000,
            'download_size_note': 'Approximate 3.6 GB including Torch; two upstream wheel sizes are not recorded in the lock.',
            'free_space_required_gib': 12,
            'summary': 'Prepare LIC Lite, Python 3.14.6, the pinned NVIDIA base dependencies and Florence-2. '
                       'Internet is needed for missing downloads; verified local copies are reused. '
                       'LIC can operate offline afterward. Requires a compatible NVIDIA GPU and driver. '
                       'One synthetic caption and an empty-catalog launch check will run before activation. '
                       'No existing LIC data is used.',
            'layout': {k: str(v) for k, v in places.items()}, 'activated': False,
            'details': {
                'LIC Lite': {'version': recipe.version, 'artifact': recipe.artifact_id,
                             'sha256': recipe.expected_sha256, 'source': recipe.source},
                'Python': {'version': runtime.version, 'artifact': runtime.artifact_id,
                           'sha256': runtime.expected_sha256, 'source': runtime.source_name},
                'AI dependencies': {'profile': lock.profile, 'digest': lock.digest()},
                'Florence caption model': {'model': model.repository, 'revision': model.revision,
                                           'digest': model.digest(), 'source': 'huggingface.co'},
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


def ensure_selected_model(model, snapshot: Path, model_root: Path, *, model_source: Path | None,
                          fetch_file) -> dict:
    """Reuse a verified selected HF snapshot in place, or publish a manager-owned copy."""
    record = snapshot.with_name(snapshot.name + '.resource.json')
    if snapshot.exists() and not record.exists():
        evidence = verify_snapshot(model, snapshot, revision=model.revision, strict=False)
        return {**evidence, 'state': 'verified', 'reuse': 'selected-existing', 'acquired_files': 0}
    return ensure_model(model, snapshot, approved_root=model_root, candidate=model_source,
                        candidate_revision=model.revision, fetch_file=fetch_file)


def execute(delivery: Path, root: Path, *, cache_source: Path | None = None,
            model_source: Path | None = None, model_root: Path | None = None,
            resume=False, status=lambda text: None,
            boundary=lambda step: None, cancel_requested=lambda: False) -> dict:
    root = validate_root(root, delivery=delivery, resume=resume)
    paths = layout(root)
    selected_model_root = validate_model_root(model_root or paths['models'])
    paths['models'] = selected_model_root
    recipe, runtime, lock, model, channel = profile(delivery)
    package = delivery / 'artifacts/lic-lite.zip'
    if not verify_package(package, recipe).verified:
        raise ValueError('LIC Lite package verification failed')
    for source in (cache_source, model_source):
        if source is not None and (source.resolve() == root or source.resolve() in root.parents or root in source.resolve().parents):
            raise ValueError('reuse source overlaps installation target')
    identity = bootstrap_plan_digest(channel, root, selected_model_root)
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
                                                      'model_root': str(selected_model_root)})
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
            return acquire_artifact(descriptor, paths['cache'], policy,
                                    ca_bundle=delivery / 'trust/cacert.pem', progress=progress_event)

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
                                                            require_cuda=True)
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
                elif current == 'ensure_model':
                    current_source[0] = 'Hugging Face'
                    snapshot = hf_snapshot_target(paths['models'], model)
                    provider = [None]
                    def fetch_model(model_identity, item, destination):
                        if provider[0] is None:
                            provider[0] = HuggingFaceSource(ca_bundle=delivery / 'trust/cacert.pem',
                                                           progress=progress_event)
                        provider[0].fetch_file(model_identity, item, destination)
                    result = ensure_selected_model(model, snapshot, paths['models'], model_source=model_source,
                                                   fetch_file=fetch_model)
                elif current == 'validate':
                    result = validate_environment(python, paths['venv'], paths['runtime'], lock, require_cuda=True)
                    if not result['passed']:
                        raise RuntimeError('Base validation failed; see diagnostics')
                elif current in ('caption', 'readiness'):
                    if prior['status'] != 'planned':
                        raise ValueError('Prior probe evidence requires review; inference is never automatically repeated')
                    result = run_probe(current, python, source, snapshot, paths['probes'] / current,
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
                      'model_verified': results['ensure_model']['verified'],
                      'model_revision_pinned': results['ensure_model']['revision'] == model.revision,
                      'provider_smoke': results['caption']['passed'], 'application_ready': results['readiness']['passed'],
                      'offline': not results['caption']['network_attempts'] and not results['readiness']['network_attempts']}
            criteria = evaluate_criteria(checks, provider_criteria=LIC_CRITERIA)
            if not criteria['criteria_met']:
                raise RuntimeError('Mandatory activation criteria failed')
            report = {'state': criteria['state'], 'activation_preflight_passed': criteria['criteria_met'],
                      'activated': False, 'model_root': str(selected_model_root),
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
            emit('Installation paused safely. Verified completed work was retained for Resume; '
                 'unverified partial transfer bytes were discarded.', terminal='cancelled')
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
