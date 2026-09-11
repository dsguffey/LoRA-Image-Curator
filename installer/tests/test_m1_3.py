"""Local-only model identity, protection, offline/readiness and journal failure tests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

from install_manager.acquisition import AcquisitionPolicy
from install_manager.activation import CORE_CRITERIA, evaluate_criteria
from install_manager.artifacts import load_artifact_descriptor
from install_manager.dependency_lock import load_dependency_lock
from install_manager.hf_source import (HOSTS, MAX_REDIRECTS, HuggingFaceSource, SafeRedirect,
                                       SourcePolicyError, model_from_metadata, validate_url)
from install_manager.lic_readiness import LIC_CRITERIA, execute_readiness, plan_readiness, run_probe
from install_manager.model_resources import ModelSnapshot, ensure_model, load_model, verify_snapshot
from install_manager.recipe import load_recipe

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / 'src' / 'install_manager' / 'recipes'
REV = 'a' * 40
PAYLOADS = {'config.json': b'{"model_type":"fixture"}', 'model.safetensors': b'fixture weights'}


def fixture_model():
    files = []
    for name, data in PAYLOADS.items():
        kind = 'sha256' if name.endswith('safetensors') else 'git-blob-sha1'
        digest = hashlib.sha256(data).hexdigest() if kind == 'sha256' else hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
        files.append(dict(path=name, size=len(data), digest_kind=kind, digest=digest))
    return ModelSnapshot.from_dict(dict(schema_version=1, resource_id='fixture', source='huggingface',
             repository='example/fixture', revision=REV, family='fixture', files=files,
             provenance={'scope':'fixture'}, license={'id':'MIT'}, compatibility={}))


def make_candidate(root):
    path = root / 'external-hf-cache' / REV
    path.mkdir(parents=True)
    for name, data in PAYLOADS.items():
        (path/name).write_bytes(data)
    return path


class ModelTests(unittest.TestCase):
    def test_model_identity_separates_repository_revision_content(self):
        model = fixture_model()
        self.assertEqual(model.revision, REV)
        data = json.loads(model.canonical_json())
        data['revision'] = 'b'*40
        self.assertNotEqual(model.digest(), ModelSnapshot.from_dict(data).digest())
        self.assertEqual(model.digest(), ModelSnapshot.from_dict(json.loads(model.canonical_json())).digest())

    def test_exact_candidate_content_and_git_blob_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = make_candidate(Path(temporary))
            report = verify_snapshot(fixture_model(), path, revision=REV)
            self.assertTrue(report['verified'])
            self.assertEqual(len(report['files']), 2)

    def test_wrong_revision_rejected_even_when_bytes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = make_candidate(Path(temporary))
            with self.assertRaisesRegex(ValueError, 'revision'):
                verify_snapshot(fixture_model(), path, revision='b'*40)

    def test_missing_required_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = make_candidate(Path(temporary))
            (path/'config.json').unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                verify_snapshot(fixture_model(), path, revision=REV)

    def test_changed_same_size_candidate_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = make_candidate(Path(temporary))
            item = path/'model.safetensors'
            item.write_bytes(b'x'*item.stat().st_size)
            with self.assertRaisesRegex(ValueError, 'content mismatch'):
                verify_snapshot(fixture_model(), path, revision=REV)

    def test_external_candidate_is_unchanged_and_managed_reuse_needs_no_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path=make_candidate(root)
            before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in path.iterdir()}
            target=root/'shared'/'model'/REV
            result=ensure_model(fixture_model(),target,approved_root=root/'shared',candidate=path,candidate_revision=REV)
            self.assertEqual(result['reuse'],'external-copy')
            with patch('install_manager.hf_source.HuggingFaceSource.fetch_file',side_effect=AssertionError('network')) as fetch:
                reused=ensure_model(fixture_model(),target,approved_root=root/'shared',fetch_file=fetch)
            self.assertEqual(reused['reuse'],'managed')
            fetch.assert_not_called()
            self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in path.iterdir()})
            self.assertTrue(all(not p.is_symlink() for p in target.iterdir()))

    def test_missing_snapshot_without_acquisition_permission_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with self.assertRaisesRegex(ValueError,'permission'):
                ensure_model(fixture_model(),root/'model',approved_root=root)

    def test_concurrent_model_requests_acquire_each_file_only_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            calls=[]
            def fetch(model,item,path):
                calls.append(item.path)
                path.write_bytes(PAYLOADS[item.path])
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:ensure_model(fixture_model(),root/'model',approved_root=root,fetch_file=fetch),range(2)))
            self.assertEqual(len(calls),len(PAYLOADS))
            self.assertEqual({r['reuse'] for r in results},{'acquired','managed'})

    def test_bounded_acquisition_publishes_only_verified_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            calls=[]
            def fetch(model,item,destination):
                calls.append(item.path)
                destination.write_bytes(PAYLOADS[item.path])
            result=ensure_model(fixture_model(),root/'model',approved_root=root,fetch_file=fetch)
            self.assertEqual(result['acquired_files'],2)
            self.assertEqual(len(calls),2)

    def test_acquisition_failure_never_publishes_and_keeps_external_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            sentinel=root/'unrelated.txt'
            sentinel.write_text('preserve')
            def fail(model,item,path):
                path.write_bytes(b'partial')
                raise OSError('controlled')
            with self.assertRaises(OSError):
                ensure_model(fixture_model(),root/'model',approved_root=root,fetch_file=fail)
            self.assertFalse((root/'model').exists())
            self.assertEqual(list(root.glob('.*partial*')),[])
            self.assertEqual(sentinel.read_text(),'preserve')

    def test_interrupted_model_acquisition_can_retry_without_accepting_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            target=root/'shared'/'model'
            calls=[]
            def interrupted(model,item,path):
                calls.append(item.path)
                path.write_bytes(b'partial')
                raise OSError('controlled interruption')
            with self.assertRaisesRegex(OSError, 'controlled interruption'):
                ensure_model(fixture_model(),target,approved_root=root/'shared',fetch_file=interrupted)
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob('.*.partial-*')), [])
            # An orphan bearing a partial-looking name is retained as evidence and
            # must never be mistaken for the managed target.
            orphan=target.parent/('.'+target.name+'.partial-prior-process')
            orphan.mkdir(parents=True)
            (orphan/'config.json').write_bytes(PAYLOADS['config.json'])
            def complete(model,item,path):
                path.write_bytes(PAYLOADS[item.path])
            result=ensure_model(fixture_model(),target,approved_root=root/'shared',fetch_file=complete)
            self.assertEqual(result['reuse'], 'acquired')
            self.assertTrue(orphan.is_dir())
            self.assertTrue(result['verified'])

    def test_unsafe_and_duplicate_filenames_rejected(self):
        for name in ('.','../escape','C:/escape','CON.txt','name.','a\\b'):
            data=json.loads(fixture_model().canonical_json())
            data['files'][0]['path']=name
            with self.assertRaises(ValueError):
                ModelSnapshot.from_dict(data)
        data=json.loads(fixture_model().canonical_json())
        data['files'].append(data['files'][0])
        with self.assertRaises(ValueError):
            ModelSnapshot.from_dict(data)

    def test_managed_extra_file_or_record_change_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            candidate=make_candidate(root)
            target=root/'shared'/'model'
            ensure_model(fixture_model(),target,approved_root=root/'shared',candidate=candidate,candidate_revision=REV)
            (target/'unexpected.py').write_text('raise RuntimeError()')
            with self.assertRaisesRegex(ValueError,'coverage'):
                ensure_model(fixture_model(),target,approved_root=root/'shared')


class ProviderTests(unittest.TestCase):
    def metadata(self):
        model=fixture_model()
        return {'id':model.repository,'sha':REV,'private':False,'gated':False,
                'siblings':[{'rfilename':f.path,'size':f.size,'blobId':f.digest,
                             **({'lfs':{'sha256':f.digest,'size':f.size}} if f.digest_kind=='sha256' else {})}
                            for f in model.files]}

    def test_provider_metadata_projection_and_wrong_identity(self):
        data=self.metadata()
        model=model_from_metadata(data,repository='example/fixture',revision=REV,
                                 required_files=tuple(PAYLOADS),resource_id='fixture',compatibility={})
        self.assertEqual(len(model.files),2)
        for key,value in [('sha','b'*40),('id','other/repo'),('gated',True)]:
            invalid={**data,key:value}
            with self.assertRaises(ValueError):
                model_from_metadata(invalid,repository='example/fixture',revision=REV,
                                    required_files=tuple(PAYLOADS),resource_id='fixture',compatibility={})

    def test_provider_retry_is_bounded_and_partial_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'file'
            sleeps=[]
            with patch('install_manager.hf_source._rate_limit'), patch('urllib.request.urlopen'):
                calls=[]
                def failed(*args,**kwargs):
                    calls.append(True)
                    raise OSError('offline')
                client=HuggingFaceSource(policy=AcquisitionPolicy(HOSTS,3,1,0),opener=failed,sleep=sleeps.append)
                with self.assertRaisesRegex(OSError,'attempt 3'):
                    client.download('https://huggingface.co/example',path,100)
            self.assertEqual(len(calls),3)
            self.assertEqual(sleeps,[1,2])
            self.assertFalse(path.exists())

    def test_redirect_cannot_escape_https_allowlist(self):
        handler=SafeRedirect(AcquisitionPolicy(HOSTS))
        credentialed = 'https://' + 'user' + ':' + 'pass' + '@huggingface.co/file'
        for url in ('http://huggingface.co/file','https://evil.example/file', credentialed):
            request=urllib.request.Request('https://huggingface.co/example/file')
            request._im_original_url=request.full_url
            request._im_identity='fixture:model'
            request._im_redirect_hop=0
            with self.assertRaises(SourcePolicyError):
                handler.redirect_request(request,None,302,'',{},url)

    def test_observed_official_storage_redirect_is_accepted_without_credentials(self):
        initial='https://huggingface.co/example/fixture/resolve/'+REV+'/model.safetensors'
        request=urllib.request.Request(initial, headers={'Authorization':'Bearer private'})
        request._im_original_url=initial
        request._im_identity='fixture:model.safetensors'
        request._im_redirect_hop=0
        redirected=SafeRedirect(AcquisitionPolicy(HOSTS, minimum_request_interval_seconds=0)).redirect_request(
            request,None,302,'',{},'https://us.aws.cdn.hf.co/xet-bridge-us/blob?X-Amz-Signature=' + 'secret')
        self.assertEqual(redirected.host, 'us.aws.cdn.hf.co')
        self.assertFalse(redirected.has_header('Authorization'))
        self.assertEqual(redirected._im_redirect_hop, 1)

    def test_redirect_limit_is_bounded_and_reports_hop(self):
        initial='https://huggingface.co/example/fixture/resolve/'+REV+'/model.safetensors'
        request=urllib.request.Request(initial)
        request._im_original_url=initial
        request._im_identity='fixture:model.safetensors'
        request._im_redirect_hop=MAX_REDIRECTS
        with self.assertRaisesRegex(SourcePolicyError, rf'hop={MAX_REDIRECTS + 1}/{MAX_REDIRECTS}'):
            SafeRedirect(AcquisitionPolicy(HOSTS)).redirect_request(
                request,None,302,'',{},'https://huggingface.co/loop?token=' + 'never-log')

    def test_rejection_diagnostic_is_sanitized_and_actionable(self):
        original='https://huggingface.co/example/fixture/resolve/'+REV+'/model.safetensors?token=' + 'origin-secret'
        attempted='https://evil.example/path-secret/file?X-Amz-Credential=' + 'redirect-secret'
        with self.assertRaises(SourcePolicyError) as caught:
            validate_url(attempted, original_url=original, hop=2,
                         identity='florence2-large-ft:model.safetensors')
        diagnostic=str(caught.exception)
        for expected in ('origin=huggingface.co', 'attempted=evil.example', 'hop=2/5',
                         'rule=redirect-host-not-approved', 'artifact=florence2-large-ft:model.safetensors'):
            self.assertIn(expected, diagnostic)
        self.assertNotIn('origin-secret', diagnostic)
        self.assertNotIn('redirect-secret', diagnostic)
        self.assertNotIn('path-secret', diagnostic)

    def test_initial_source_and_ca_requirements_fail_closed(self):
        with self.assertRaisesRegex(SourcePolicyError, 'origin-host-not-approved'):
            validate_url('https://us.aws.cdn.hf.co/file', origin=True)
        with self.assertRaisesRegex(ValueError, 'delivered CA bundle'):
            HuggingFaceSource()


class ReadinessTests(unittest.TestCase):
    def test_completed_journal_contains_model_smoke_and_readiness(self):
        self._journal_case(None)

    def test_model_failure_prevents_caption_and_readiness(self):
        self._journal_case('ensure_model')

    def test_caption_failure_prevents_readiness(self):
        self._journal_case('caption')

    def test_readiness_failure_never_reports_ready(self):
        self._journal_case('readiness')

    def _journal_case(self, failure):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            model=load_model(RECIPES/'florence2-large-ft.json')
            recipe=load_recipe(RECIPES/'lic.json')
            plan=plan_readiness(model,recipe,operation_id='journal',operation_root=root/'op',shared_root=root/'shared',
                                base_journal=root/'base.json',candidate=None,allow_download=False)
            validation={'passed':True,'path_consistency':True,'pip_check':{'exit_code':0}}
            base={'passed':True,'python':sys.executable,'validation':validation}
            staged=SimpleNamespace(extracted_directory=str(root/'source'),package=SimpleNamespace(package_sha256=recipe.expected_sha256))
            model_result={'verified':True,'revision':model.revision,'reuse':'managed'}
            calls=[]
            def probe(action,*args,**kwargs):
                calls.append(action)
                if action==failure:
                    raise RuntimeError('controlled '+action)
                return {'passed':True,'network_attempts':[],'denied_writes':[]}
            with patch('install_manager.lic_readiness.revalidate_base',return_value=base), \
                 patch('install_manager.lic_readiness.stage_package',return_value=staged), \
                 patch('install_manager.lic_readiness.ensure_model',return_value=model_result,
                       side_effect=RuntimeError('controlled model') if failure=='ensure_model' else None), \
                 patch('install_manager.lic_readiness.run_probe',side_effect=probe):
                arguments=(plan,model,recipe,root/'package',load_dependency_lock(RECIPES/'base-windows-nvidia-cu130.json'),
                           load_artifact_descriptor(RECIPES/'python-3.14.6-windows-x64.json'))
                if failure:
                    with self.assertRaises(RuntimeError):
                        execute_readiness(*arguments)
                else:
                    result=execute_readiness(*arguments)
                    self.assertTrue(result['criteria']['criteria_met'])
                    self.assertFalse(result['activation_performed'])
            journal=json.loads((root/'op/state/operations/journal.json').read_text())
            self.assertEqual(journal['status'],'failed' if failure else 'succeeded')
            self.assertEqual(journal['artifacts'][0]['revision'],model.revision)
            if failure=='ensure_model':
                self.assertEqual(calls,[])
            elif failure=='caption':
                self.assertEqual(calls,['caption'])
            if failure:
                self.assertIsNone(journal['final_validation'])

    def test_activation_missing_false_or_unknown_evidence_fails_closed(self):
        checks={name:True for name in CORE_CRITERIA+LIC_CRITERIA}
        passed=evaluate_criteria(checks,provider_criteria=LIC_CRITERIA)
        self.assertTrue(passed['criteria_met'])
        self.assertFalse(passed['activation_performed'])
        for name in checks:
            self.assertFalse(evaluate_criteria({**checks,name:False},provider_criteria=LIC_CRITERIA)['criteria_met'])
        self.assertFalse(evaluate_criteria({})['criteria_met'])

    def test_real_recipe_plan_is_deterministic_and_never_activates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            args=dict(operation_id='m13-plan',operation_root=root/'op',shared_root=root/'shared',
                      base_journal=root/'base.json',candidate=None,allow_download=False)
            first=plan_readiness(load_model(RECIPES/'florence2-large-ft.json'),load_recipe(RECIPES/'lic.json'),**args)
            self.assertEqual(first,plan_readiness(load_model(RECIPES/'florence2-large-ft.json'),load_recipe(RECIPES/'lic.json'),**args))
            self.assertFalse(first['activation_authorized'])
            self.assertFalse((root/'op').exists())

    def test_offline_probe_rejects_network_and_external_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            code=(f'import sys;sys.path.insert(0,{str(ROOT/"src")!r});'
                  'from pathlib import Path;from install_manager.probe_guard import ProbeGuard;'
                  f'g=ProbeGuard(Path({str(root)!r}));g.install();'
                  'import socket,os,sqlite3;\n'
                  'with open(os.devnull,"w") as sink: sink.write("discarded")\n'
                  'try: socket.getaddrinfo("example.com",443)\n'
                  'except PermissionError: pass\n'
                  f'try: Path({str(root.parent/"forbidden-m13.txt")!r}).write_text("bad")\n'
                  'except PermissionError: pass\n'
                  f'try: sqlite3.connect({str(root.parent/"forbidden-m13.db")!r})\n'
                  'except PermissionError: pass\n'
                  'assert len(g.network_attempts)==1 and len(g.denied_writes)==2;print("blocked")')
            process=subprocess.run([sys.executable,'-I','-B','-c',code],capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertIn('blocked',process.stdout)
            self.assertFalse((root.parent/'forbidden-m13.txt').exists())
            self.assertFalse((root.parent/'forbidden-m13.db').exists())

    def test_model_load_inference_readiness_and_network_failures_are_not_ready(self):
        for error in ('model load failed','caption inference failed','LIC readiness failed','network attempted'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                def runner(command,**kwargs):
                    output=Path(command[command.index('--output')+1])
                    output.write_text(json.dumps({'passed':False,'error':error,'network_attempts':[], 'denied_writes':[]}))
                    return SimpleNamespace(returncode=1)
                with self.assertRaises(RuntimeError):
                    run_probe('readiness',Path(sys.executable),root/'source',root/'snapshot',root/'state',root/'hub',runner=runner)

    def test_failed_step_is_durable_and_does_not_touch_existing_lic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            existing=root/'existing-lic'
            existing.mkdir()
            sentinel=existing/'catalog.db'
            sentinel.write_bytes(b'preserve')
            model=load_model(RECIPES/'florence2-large-ft.json')
            recipe=load_recipe(RECIPES/'lic.json')
            plan=plan_readiness(model,recipe,operation_id='failed-op',operation_root=root/'op',shared_root=root/'shared',
                                base_journal=root/'base.json',candidate=None,allow_download=False)
            with patch('install_manager.lic_readiness.revalidate_base',side_effect=RuntimeError('controlled')):
                with self.assertRaises(RuntimeError):
                    execute_readiness(plan,model,recipe,root/'package.zip',
                                      load_dependency_lock(RECIPES/'base-windows-nvidia-cu130.json'),
                                      load_artifact_descriptor(RECIPES/'python-3.14.6-windows-x64.json'))
            journal=json.loads((root/'op/state/operations/failed-op.json').read_text())
            self.assertEqual(journal['status'],'failed')
            self.assertEqual(journal['artifacts'][0]['revision'],model.revision)
            self.assertEqual(journal['steps'][0]['status'],'failed')
            self.assertEqual(sentinel.read_bytes(),b'preserve')
            self.assertEqual(list(existing.iterdir()),[sentinel])

    def test_network_attempt_overrides_probe_success_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def runner(command,**kwargs):
                Path(command[command.index('--output')+1]).write_text(json.dumps(
                    {'passed':True,'network_attempts':['socket.connect'],'denied_writes':[]}))
                return SimpleNamespace(returncode=0)
            with self.assertRaisesRegex(RuntimeError,'failed'):
                run_probe('caption',Path(sys.executable),root/'source',root/'snapshot',root/'state',root/'hub',runner=runner)

    def test_tampered_plan_fails_before_journal_or_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            model=load_model(RECIPES/'florence2-large-ft.json')
            recipe=load_recipe(RECIPES/'lic.json')
            plan=plan_readiness(model,recipe,operation_id='tampered',operation_root=root/'op',shared_root=root/'shared',
                                base_journal=root/'base.json',candidate=None,allow_download=False)
            plan['allow_download']=True
            with self.assertRaisesRegex(ValueError,'plan changed'):
                execute_readiness(plan,model,recipe,root/'package',load_dependency_lock(RECIPES/'base-windows-nvidia-cu130.json'),
                                  load_artifact_descriptor(RECIPES/'python-3.14.6-windows-x64.json'))
            self.assertFalse((root/'op').exists())


if __name__ == '__main__':
    unittest.main()
