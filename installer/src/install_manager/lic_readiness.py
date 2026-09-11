"""LIC-specific M1.3 policy layered on model, package, environment and journal primitives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from .acquisition import sha256_file
from .activation import evaluate_criteria
from .artifacts import ArtifactDescriptor
from .dependency_lock import DependencyLock
from .hf_source import HuggingFaceSource, hf_snapshot_target
from .journal import OperationJournal
from .model_resources import ModelSnapshot, ensure_model
from .probe_guard import isolated_environment
from .recipe import LicRecipe
from .staging import stage_package
from .validation import validate_environment
from .delivery_paths import probe_file
from .child_process import run as child_run


FLORENCE_REPOSITORY = 'florence-community/Florence-2-large-ft'
FLORENCE_REVISION = '26b734a54fdfbf9c398351eedfabb7f27fc470b7'
LIC_CRITERIA = ('model_verified', 'model_revision_pinned', 'provider_smoke', 'offline', 'application_ready')
# Core readiness is deliberately provider-neutral.  LIC's process/UI/catalog
# must work while every optional provider is absent.
CORE_READINESS_CRITERIA = ('offline', 'application_ready')
FLORENCE_FILES = ('added_tokens.json', 'config.json', 'generation_config.json', 'merges.txt',
                 'model.safetensors', 'preprocessor_config.json', 'processor_config.json',
                 'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json')


def lic_model_policy(model: ModelSnapshot) -> None:
    if (model.source != 'huggingface' or model.repository != FLORENCE_REPOSITORY
            or model.revision != FLORENCE_REVISION or model.family != 'florence2'
            or {f.path for f in model.files} != set(FLORENCE_FILES)
            or model.compatibility.get('transformers') != '4.56.2'
            or model.compatibility.get('trust_remote_code') is not False
            or model.compatibility.get('weights') != 'safetensors'):
        raise ValueError('model does not match the reviewed LIC Florence policy')


def plan_readiness(model: ModelSnapshot, recipe: LicRecipe, *, operation_id: str,
                   operation_root: Path, shared_root: Path, base_journal: Path,
                   candidate: Path | None, allow_download: bool) -> dict:
    lic_model_policy(model)
    root = operation_root.resolve()
    shared = shared_root.resolve()
    target = hf_snapshot_target(shared, model).resolve()
    if candidate is not None:
        candidate = candidate.resolve()
        if candidate == root or candidate in root.parents or root in candidate.parents:
            raise ValueError('operation root overlaps existing model candidate')
        if candidate == shared or candidate in shared.parents or shared in candidate.parents:
            raise ValueError('shared root overlaps existing model candidate')
        if candidate.name != model.revision:
            raise ValueError('candidate cache names a different revision')
    if (root / 'application').exists():
        raise FileExistsError('application stage already populated')
    plan = {'schema_version': 1, 'operation_id': operation_id, 'operation_root': root.as_posix(),
            'shared_root': shared.as_posix(), 'model_target': target.as_posix(),
            'application_sha256': recipe.expected_sha256, 'model_manifest_sha256': model.digest(),
            'model_repository': model.repository, 'model_revision': model.revision,
            'base_journal': base_journal.resolve().as_posix(),
            'candidate': candidate.as_posix() if candidate else None,
            'allow_download': allow_download, 'activation_authorized': False,
            'steps': ['verify_base', 'stage_application', 'ensure_model', 'caption', 'readiness']}
    plan['sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return plan


def revalidate_base(base_journal: Path, lock: DependencyLock, runtime: ArtifactDescriptor) -> dict:
    journal = OperationJournal.load(base_journal).data
    if journal['status'] != 'succeeded' or not journal['final_validation']['passed']:
        raise ValueError('M1.2 base proof did not succeed')
    steps = {item['name']: item for item in journal['steps']}
    prior_runtime = steps['acquire_runtime']['evidence']
    if prior_runtime['descriptor']['expected_sha256'] != runtime.expected_sha256:
        raise ValueError('runtime descriptor differs from accepted M1.2 proof')
    archive_path = Path(prior_runtime['cache_path'])
    if sha256_file(archive_path) != runtime.expected_sha256:
        raise ValueError('runtime archive changed')
    runtime_root = Path(steps['extract_runtime']['runtime_path']).resolve()
    checked = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = (runtime_root / info.filename).resolve()
            if runtime_root not in path.parents or not path.is_file():
                raise ValueError('runtime payload missing or unsafe')
            if sha256_file(path) != hashlib.sha256(archive.read(info)).hexdigest():
                raise ValueError(f'runtime payload changed: {info.filename}')
            checked += 1
    python = Path(steps['create_final_path_venv']['interpreter']).resolve()
    venv = Path(journal['target_path']).resolve()
    validation = validate_environment(python, venv, runtime_root, lock, require_cuda=True)
    if not validation['passed']:
        raise ValueError('M1.2 environment no longer validates')
    return {'passed': True, 'runtime_files_verified': checked, 'runtime_sha256': runtime.expected_sha256,
            'python': str(python), 'venv': str(venv), 'validation': validation}


def run_probe(action: str, python: Path, source: Path, snapshot: Path | None, state: Path,
              hub: Path, *, runner=child_run) -> dict:
    if action not in ('caption', 'readiness', 'core-readiness'):
        raise ValueError('unknown LIC probe')
    state.mkdir(parents=True, exist_ok=False)
    output = state / 'result.json'
    environment = isolated_environment(state, hub)
    command = [str(python), '-I', '-B', str(probe_file('lic_model_probe.py')),
               action, '--source', str(source),
               '--state', str(state), '--output', str(output)]
    if snapshot is not None:
        command.extend(('--snapshot', str(snapshot)))
    with (state / 'probe.log').open('w', encoding='utf-8') as log:
        process = runner(command, env=environment, cwd=state, stdout=log, stderr=subprocess.STDOUT,
                         check=False, timeout=300)
    if not output.is_file():
        raise RuntimeError(f'{action} probe did not produce a report; inspect {state / "probe.log"}')
    report = json.loads(output.read_text(encoding='utf-8'))
    if (process.returncode or report.get('passed') is not True or report.get('network_attempts') != []
            or report.get('denied_writes') != []):
        raise RuntimeError(f'{action} failed: {report}')
    return report


def execute_readiness(plan: dict, model: ModelSnapshot, recipe: LicRecipe, package: Path,
                      lock: DependencyLock, runtime: ArtifactDescriptor,
                      *, ca_bundle: Path | None = None) -> dict:
    current_digest = hashlib.sha256(json.dumps({k: v for k, v in plan.items() if k != 'sha256'},
                                              sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if current_digest != plan.get('sha256'):
        raise ValueError('plan changed after review')
    if plan['model_manifest_sha256'] != model.digest() or plan['application_sha256'] != recipe.expected_sha256:
        raise ValueError('stale plan identity')
    root = Path(plan['operation_root'])
    journal = OperationJournal.create(root / 'state' / 'operations', plan['operation_id'],
                                      target_path=root / 'application', plan_digest=plan['sha256'],
                                      artifacts=[json.loads(model.canonical_json())], steps=tuple(plan['steps']))
    current = ''
    results = {}
    try:
        journal.set_status('running')
        for current in plan['steps']:
            journal.set_step(current, 'running')
            if current == 'verify_base':
                result = revalidate_base(Path(plan['base_journal']), lock, runtime)
            elif current == 'stage_application':
                staged = stage_package(package, root / 'application', recipe)
                result = {'passed': True, 'source': staged.extracted_directory,
                          'package_sha256': staged.package.package_sha256}
            elif current == 'ensure_model':
                if plan['allow_download'] and ca_bundle is None:
                    raise ValueError('Model download requires an explicit delivered CA bundle')
                result = ensure_model(model, Path(plan['model_target']), approved_root=Path(plan['shared_root']),
                                      candidate=Path(plan['candidate']) if plan['candidate'] else None,
                                      candidate_revision=model.revision,
                                      fetch_file=(HuggingFaceSource(ca_bundle=ca_bundle).fetch_file
                                                  if plan['allow_download'] else None))
            elif current in ('caption', 'readiness'):
                result = run_probe(current, Path(results['verify_base']['python']),
                                   Path(results['stage_application']['source']), Path(plan['model_target']),
                                   root / 'probes' / current, Path(plan['shared_root']) / 'huggingface' / 'hub')
            else:
                raise ValueError('unrecognized plan step')
            results[current] = result
            journal.set_step(current, 'completed', evidence=result)
        checks = {'application_verified': results['stage_application']['passed'],
                  'runtime_verified': results['verify_base']['passed'],
                  'final_venv_valid': results['verify_base']['validation']['path_consistency'],
                  'dependencies_exact': results['verify_base']['validation']['passed'],
                  'pip_check': results['verify_base']['validation']['pip_check']['exit_code'] == 0,
                  'required_imports': True, 'target_safe': True, 'recovery_available': True,
                  'journal_complete': all(s['status'] == 'completed' for s in journal.data['steps']),
                  'model_verified': results['ensure_model']['verified'],
                  'model_revision_pinned': results['ensure_model']['revision'] == FLORENCE_REVISION,
                  'provider_smoke': results['caption']['passed'],
                  'offline': results['caption']['network_attempts'] == [] and results['readiness']['network_attempts'] == [],
                  'application_ready': results['readiness']['passed']}
        criteria = evaluate_criteria(checks, provider_criteria=LIC_CRITERIA)
        if not criteria['criteria_met']:
            raise RuntimeError(f'readiness criteria failed: {criteria["blockers"]}')
        report = {'passed': True, 'model': results['ensure_model'], 'caption': results['caption'],
                  'readiness': results['readiness'], 'criteria': criteria,
                  'activation_performed': False, 'journal': str(journal.path)}
        journal.set_validation(report)
        journal.set_status('succeeded')
        return report
    except BaseException as error:
        if current:
            journal.set_step(current, 'failed', error=f'{type(error).__name__}: {error}')
        journal.add_cleanup_action('Preserve verified model, partial diagnostics and existing M1.2 environment', performed=True)
        journal.add_cleanup_action('Review failed step; never automatically repeat caption inference', performed=False)
        journal.set_status('failed', failure=f'{type(error).__name__}: {error}')
        raise
