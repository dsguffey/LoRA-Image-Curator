"""M1.4 local fault, trust, delivery and recovery tests; no upstream downloads."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request
import zipfile

from install_manager.acquisition import (AcquisitionCancelled, ArtifactRedirect, AcquisitionPolicy,
                                         acquire_artifact, verified_tls_context)
from install_manager.artifacts import AcquiredArtifact
from install_manager.bootstrap import execute, disclosure, verify_extracted
from install_manager.bootstrap_layout import layout, validate_root, reject_reparse_entries
from install_manager.child_process import run as child_run
from install_manager.delivery_paths import probe_file
from install_manager.first_launch import progress_view
from install_manager.journal import OperationJournal
from install_manager.process_lock import process_lock
from install_manager.release_channel import validate_channel, verify_delivery
from install_manager.release_preflight import scan_content, validate_notices
from test_m1_2 import descriptor, lock_for
from test_m1_3 import fixture_model

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = json.loads((ROOT / 'src/install_manager/recipes/lic-candidate.json').read_text())


class ChannelAndHygieneTests(unittest.TestCase):
    def test_candidate_cannot_be_claimed_production(self):
        validate_channel(CHANNEL, qualification=True, activation_capable=True)
        with self.assertRaises(ValueError):
            validate_channel(CHANNEL, qualification=False, activation_capable=True)

    def test_explicit_production_channel_shape(self):
        validate_channel(dict(CHANNEL, classification='production-approved', channel='stable'),
                         qualification=False, activation_capable=True)

    def test_revoked_incompatible_and_unknown_fail(self):
        for state in ('revoked', 'incompatible', 'untested'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_channel(dict(CHANNEL, compatibility=state), qualification=True, activation_capable=True)

    def test_executable_metadata_and_activation_fail(self):
        for changed in (dict(CHANNEL, command='anything'), dict(CHANNEL, activation_allowed=False)):
            with self.assertRaises(ValueError):
                validate_channel(changed, qualification=True, activation_capable=True)

    def test_missing_digest_fails(self):
        with self.assertRaises(ValueError):
            validate_channel(dict(CHANNEL, model_digest=''), qualification=True, activation_capable=True)

    def test_sensitive_paths_and_secrets_are_redacted(self):
        content = ('Path C:' + r'\Users\PrivateName\catalog.db' + '\n' + 'ghp_' + 'a'*35).encode()
        findings = scan_content('example.txt', content)
        self.assertEqual({f['rule'] for f in findings}, {'personal-windows-path', 'token'})
        self.assertNotIn('PrivateName', json.dumps(findings))

    def test_private_generated_data_and_models(self):
        for name in ('reports/a.json', 'settings.json', 'weights.safetensors', 'data.db', 'logs/run.log'):
            with self.subTest(name=name):
                self.assertTrue(scan_content(name, b'fixture'))

    def test_lic_license_is_not_manager_license(self):
        self.assertTrue(scan_content('LICENSE', b'MIT'))
        self.assertFalse(scan_content('LIC-Lite/LICENSE', b'MIT'))

    def test_bundled_notices_must_exist(self):
        inventory = json.loads((ROOT / 'tools/third-party-inventory.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                validate_notices(inventory, root)
            for item in inventory['components']:
                if item['delivery'] == 'bundled-in-installer':
                    for name in item['notices']:
                        (root/name).parent.mkdir(parents=True, exist_ok=True)
                        (root/name).write_text('fixture notice')
            validate_notices(inventory, root)

    def test_delivery_index_rejects_tampering_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'recipe.json').write_bytes(b'{}')
            index = root/'payload-index.json'
            index.write_text(json.dumps({'files': {'recipe.json': hashlib.sha256(b'{}').hexdigest()}}))
            expected = hashlib.sha256(index.read_bytes()).hexdigest()
            verify_delivery(root, expected)
            (root/'recipe.json').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                verify_delivery(root, expected)

    def test_redirect_does_not_contact_untrusted_host(self):
        redirect = ArtifactRedirect(AcquisitionPolicy(('www.python.org',)))
        request = urllib.request.Request('https://www.python.org/test.zip')
        credentialed = 'https://' + 'user' + ':' + 'secret' + '@www.python.org/test.zip'
        for url in ('http://www.python.org/test.zip', 'https://evil.invalid/test.zip', credentialed):
            with self.assertRaises(ValueError):
                redirect.redirect_request(request, None, 302, '', {}, url)

    def test_https_acquisition_refuses_implicit_host_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'delivered CA bundle'):
                acquire_artifact(descriptor(), Path(directory), AcquisitionPolicy(('www.python.org',)))

    def test_missing_or_invalid_delivered_ca_bundle_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(FileNotFoundError):
                verified_tls_context(root/'missing.pem')
            invalid=root/'invalid.pem'
            invalid.write_text('not a certificate')
            with self.assertRaises(ssl.SSLError):
                verified_tls_context(invalid)

    def test_delivered_ca_bundle_is_added_to_strict_default_context(self):
        context = SimpleNamespace(verify_mode=ssl.CERT_REQUIRED, check_hostname=True,
                                  load_verify_locations=Mock())
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / 'cacert.pem'
            bundle.write_text('fixture')
            with patch('install_manager.acquisition.ssl.create_default_context', return_value=context):
                self.assertIs(verified_tls_context(bundle), context)
            context.load_verify_locations.assert_called_once_with(cafile=str(bundle.resolve()))


class LayoutAndDeliveryTests(unittest.TestCase):
    def test_progress_view_uses_real_download_fraction_and_stops_on_error(self):
        phase = progress_view({'message':'[7/10] Downloading Florence-2', 'step':7,
                               'total_steps':10, 'terminal':None})
        self.assertTrue(phase['working'])
        self.assertEqual(phase['mode'], 'indeterminate')
        download = progress_view({'message':'Downloading model', 'downloaded_bytes':25,
                                  'total_bytes':100, 'step':7, 'total_steps':10})
        self.assertEqual(download['mode'], 'determinate')
        self.assertEqual(download['value'], 25)
        failure = progress_view({'message':'Stopped safely', 'terminal':'error'})
        self.assertFalse(failure['working'])
        self.assertEqual(failure['mode'], 'idle')

    def test_resume_refuses_inserted_reparse_entry_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'Shared').mkdir()
            with patch.object(Path, 'is_junction', return_value=True):
                with self.assertRaisesRegex(ValueError, 'reparse entry requiring review'):
                    reject_reparse_entries(root)

    def test_core_root_no_longer_inherits_optional_torch_path_limit(self):
        with patch('install_manager.bootstrap_layout.long_paths_enabled', return_value=False):
            self.assertEqual(validate_root(Path('C:/folder/' + 'x'*40), delivery=ROOT),
                             Path('C:/folder/' + 'x'*40).resolve())

    def test_windows_locked_replacement_preserves_then_publishes(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        with tempfile.TemporaryDirectory() as directory:
            target, candidate = Path(directory)/'target', Path(directory)/'candidate'
            target.write_bytes(b'old')
            candidate.write_bytes(b'new')
            handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            try:
                with self.assertRaises(PermissionError):
                    os.replace(candidate, target)
                self.assertEqual(target.read_bytes(), b'old')
                self.assertEqual(candidate.read_bytes(), b'new')
            finally:
                kernel.CloseHandle(handle)
            os.replace(candidate, target)
            self.assertEqual(target.read_bytes(), b'new')

    def test_layout_separates_mutable_venv_shared_models_and_state(self):
        places = layout(Path('C:/fixture'))
        self.assertNotIn(places['venv'], places['models'].parents)
        self.assertEqual(len(set(places.values())), len(places))

    def test_populated_target_refused_preserving_unrelated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'keep.txt').write_text('preserve')
            with self.assertRaises(FileExistsError):
                validate_root(root, delivery=ROOT)
            self.assertEqual((root/'keep.txt').read_text(), 'preserve')

    def test_short_space_unicode_root_is_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'LIC é'
            validate_root(target, delivery=ROOT)
            target.mkdir()
            (target/'owned').write_text('ok')

    def test_long_root_and_unsafe_nesting_refused(self):
        for root, delivery in ((Path('C:/'+'x'*115), ROOT), (ROOT/'child', ROOT), (ROOT.parent, ROOT)):
            with self.assertRaises(ValueError):
                validate_root(root, delivery=delivery)

    def test_probe_path_has_no_checkout_fallback_when_frozen(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, '_MEIPASS', directory, create=True):
            with self.assertRaises(FileNotFoundError):
                probe_file('lic_model_probe.py')

    def test_child_tcl_environment_is_not_borrowed_and_parent_unchanged(self):
        env = dict(os.environ, TCL_LIBRARY='bad', TK_LIBRARY='bad')
        with patch('install_manager.child_process.subprocess.run') as run:
            child_run(['fixture'], env=env)
            self.assertNotIn('TCL_LIBRARY', run.call_args.kwargs['env'])
            self.assertEqual(env['TCL_LIBRARY'], 'bad')

    def test_disclosure_covers_plan_no_admin_and_no_activation(self):
        recipe = SimpleNamespace(version='0.28.4', artifact_id='fixture-lic',
                                 expected_sha256='1' * 64, source='fixture source')
        with patch('install_manager.bootstrap.profile', return_value=(recipe, descriptor(), lock_for(), None, CHANNEL)):
            plan = disclosure(ROOT, Path('C:/fixture'))
        self.assertFalse(plan['administrator_required'])
        self.assertFalse(plan['activated'])
        self.assertIn('offline', plan['summary'])
        self.assertIn('Optional AI providers are installed separately', plan['summary'])
        self.assertNotIn('Florence caption model', plan['details'])
        self.assertGreater(plan['download_bytes_without_reuse'], 0)
        self.assertEqual(plan['details']['LIC Lite']['version'], '0.28.4')

    def test_immutable_payload_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'target'
            target.mkdir()
            archive = root/'test.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('file.txt', b'original')
            (target/'file.txt').write_bytes(b'original')
            self.assertEqual(verify_extracted(archive, target), 1)
            (target/'file.txt').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verify_extracted(archive, target)


class BootstrapTests(unittest.TestCase):
    def fixture(self, root, stack):
        delivery = root/'delivery'
        (delivery/'artifacts').mkdir(parents=True)
        with zipfile.ZipFile(delivery/'artifacts/lic-lite.zip', 'w') as z:
            z.writestr('app.py', b'pass')
        archive = root/'runtime.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('python.exe', b'fixture')
            z.writestr('Lib/venv/__init__.py', b'pass')
        runtime = descriptor(archive.read_bytes())
        model = fixture_model()
        acquired = AcquiredArtifact(runtime, str(archive), runtime.expected_sha256, archive.stat().st_size,
                                    'verified', True, True, 0)
        stack.enter_context(patch('install_manager.bootstrap.profile', return_value=(None, runtime, lock_for(), model, CHANNEL)))
        stack.enter_context(patch('install_manager.bootstrap.verify_package', return_value=SimpleNamespace(verified=True)))
        stack.enter_context(patch('install_manager.bootstrap.acquire_artifact', return_value=acquired))
        def venv(_, path, **kwargs):
            python = path/'Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.write_bytes(b'fixture')
            return python
        stack.enter_context(patch('install_manager.bootstrap.create_final_path_venv', side_effect=venv))
        stack.enter_context(patch('install_manager.bootstrap.install_locked_wheels', return_value={'exit_code':0}))
        def stage(package, target, recipe):
            (target/'extracted').mkdir(parents=True)
            with zipfile.ZipFile(package) as z:
                (target/'extracted/app.py').write_bytes(z.read('app.py'))
        stack.enter_context(patch('install_manager.bootstrap.stage_package', side_effect=stage))
        stack.enter_context(patch('install_manager.bootstrap.ensure_model', return_value={'verified':True, 'revision':model.revision}))
        stack.enter_context(patch('install_manager.bootstrap.validate_environment', return_value={
            'passed':True, 'path_consistency':True, 'pip_check':{'exit_code':0}}))
        stack.enter_context(patch('install_manager.bootstrap.run_probe', return_value={'passed':True, 'network_attempts':[]}))
        return delivery, root/'install'

    def test_full_preflight_without_activation_and_no_parent_environment_changes(self):
        before = dict(os.environ)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            events=[]
            result = execute(delivery, target, status=events.append)
            self.assertTrue(result['activation_preflight_passed'])
            self.assertFalse(result['activated'])
            self.assertEqual([event['step'] for event in events if event['message'].startswith('[')],
                             list(range(1, 9)))
            self.assertEqual(events[-1]['terminal'], 'success')
        self.assertEqual(dict(os.environ), before)

    def test_journal_discovery_and_resume_reuses_completed_immutable_work(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            def interrupt(step):
                if step == 'acquire_dependencies':
                    raise InterruptedError('fixture interruption')
            with self.assertRaises(InterruptedError):
                execute(delivery, target, boundary=interrupt)
            keep = target/'unrelated.txt'
            keep.write_text('keep')
            result = execute(delivery, target, resume=True)
            self.assertTrue(result['activation_preflight_passed'])
            self.assertEqual(keep.read_text(), 'keep')

    def test_cancel_before_work_records_truthful_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            events = []
            with self.assertRaises(AcquisitionCancelled):
                execute(delivery, target, status=events.append, cancel_requested=lambda: True)
            journal = OperationJournal.load(target/'State/operations/bootstrap.json')
            self.assertEqual(journal.data['status'], 'cancelled')
            self.assertIsNone(journal.data['final_validation'])
            self.assertEqual(events[-1]['terminal'], 'cancelled')
            self.assertFalse(any(step['status'] == 'completed' for step in journal.data['steps']))

    def test_cancel_at_durable_boundary_can_resume_verified_work(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            requested = {'value': False}
            def boundary(step):
                if step == 'acquire_runtime':
                    requested['value'] = True
            with self.assertRaises(AcquisitionCancelled):
                execute(delivery, target, boundary=boundary,
                        cancel_requested=lambda: requested['value'])
            cancelled = OperationJournal.load(target/'State/operations/bootstrap.json')
            self.assertEqual(cancelled.data['status'], 'cancelled')
            self.assertEqual(cancelled.data['steps'][0]['status'], 'completed')
            result = execute(delivery, target, resume=True)
            self.assertTrue(result['activation_preflight_passed'])
            self.assertFalse(result['activated'])

    def test_unknown_venv_after_interruption_is_not_assumed_valid(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            def interrupt(step):
                if step == 'create_venv':
                    raise InterruptedError('fixture interruption')
            with self.assertRaises(InterruptedError):
                execute(delivery, target, boundary=interrupt)
            with self.assertRaisesRegex(ValueError, 'venv requires review'):
                execute(delivery, target, resume=True)

    def test_failed_required_validation_never_promotes_readiness(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            stack.enter_context(patch('install_manager.bootstrap.validate_environment', return_value={'passed':False}))
            with self.assertRaises(RuntimeError):
                execute(delivery, target)
            journal = OperationJournal.load(target/'State/operations/bootstrap.json')
            self.assertEqual(journal.data['status'], 'failed')
            self.assertIsNone(journal.data['final_validation'])

    def test_corrupt_or_wrong_target_resume_journal_refused(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            path = target/'State/operations'
            OperationJournal.create(path, 'bootstrap', target_path=target, plan_digest='wrong', artifacts=[], steps=())
            with self.assertRaisesRegex(ValueError, 'journal does not belong'):
                execute(delivery, target, resume=True)

    def test_early_acquisition_failure_writes_log_and_can_resume(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            with patch('install_manager.bootstrap.acquire_artifact', side_effect=OSError('TLS fixture failure')):
                with self.assertRaisesRegex(OSError, 'TLS fixture failure'):
                    execute(delivery, target)
            log = target / 'Logs/bootstrap.log'
            self.assertTrue(log.is_file())
            self.assertIn('TLS fixture failure', log.read_text())
            journal = OperationJournal.load(target/'State/operations/bootstrap.json')
            self.assertEqual(journal.data['steps'][0]['status'], 'failed')
            result = execute(delivery, target, resume=True)
            self.assertTrue(result['activation_preflight_passed'])
            self.assertIn('Resuming recorded work', log.read_text())

    def test_core_bootstrap_never_invokes_model_acquisition(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            delivery, target = self.fixture(Path(directory), stack)
            with patch('install_manager.bootstrap.ensure_model') as acquire_model:
                result = execute(delivery, target)
            self.assertTrue(result['activation_preflight_passed'])
            acquire_model.assert_not_called()
            completed = OperationJournal.load(target/'State/operations/bootstrap.json')
            self.assertNotIn('ensure_model', {step['name'] for step in completed.data['steps']})

    def test_journal_retries_a_transient_windows_reader_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = OperationJournal.create(root, 'reader-lock', target_path=root,
                                              plan_digest='fixture', artifacts=[], steps=('one',))
            real_replace = os.replace
            attempts = 0
            def transient(source, target):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError('fixture sharing violation')
                return real_replace(source, target)
            with patch('install_manager.journal.os.replace', side_effect=transient), \
                    patch('install_manager.journal.time.sleep'):
                journal.set_status('running')
            self.assertEqual(attempts, 2)
            self.assertEqual(OperationJournal.load(journal.path).data['status'], 'running')


class ProcessLockTests(unittest.TestCase):
    def test_cross_process_bounded_contention_then_death_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = ("import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                    "from install_manager.process_lock import process_lock; "
                    "ctx=process_lock(Path(sys.argv[2])); ctx.__enter__(); "
                    "Path(sys.argv[3]).write_text('ready'); time.sleep(20)")
            process = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(ROOT/'src'),
                                        str(root/'resource'), str(root/'ready')])
            try:
                deadline = time.monotonic()+5
                while not (root/'ready').exists() and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertTrue((root/'ready').exists())
                with self.assertRaises(TimeoutError):
                    with process_lock(root/'resource', timeout=.1):
                        pass
                with process_lock(root/'different', timeout=.1):
                    pass
            finally:
                process.terminate()
                process.wait(timeout=5)
            with process_lock(root/'resource', timeout=.1):
                pass

    def test_invalid_lock_wait_refused(self):
        with self.assertRaises(ValueError):
            with process_lock(Path('fixture'), timeout=999):
                pass


if __name__ == '__main__':
    unittest.main()
