"""Optional providers may fail without changing a healthy activated Core."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from install_manager import component_operations as operations
from install_manager import florence_component as florence
from install_manager import managed_resources
from install_manager.acquisition import AcquisitionCancelled, AcquisitionFailure
from install_manager.active_venv import managed_venv
from install_manager.artifacts import AcquiredArtifact
from install_manager.compatibility_profiles import recommended_profile
from install_manager.compatibility_profiles import accepted_installed_profile
from install_manager.component_catalog import ComponentFacts, ComponentPhase, ComponentAction, component_action, load_component_catalog
from install_manager.component_state import (ResourceState, component_from_manifest,
                                             empty_inventory, load_inventory,
                                             replace_component, write_inventory)
from install_manager.provider_venv import (inventory_for_generation,
                                           recover_pending_provider_promotions,
                                           record_candidate)
from install_manager.journal import OperationJournal
from install_manager.manager_ui import ManagerShell, provider_failure_presentation


DELIVERY = Path(__file__).resolve().parents[1] / 'src/install_manager'


class ProviderTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'LIC Test'
        self.old_venv = self.root / 'Apps/LIC-Lite/candidate-1/venv'
        python = self.old_venv / 'Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'fixture')
        app = self.root / 'Apps/LIC-Lite/candidate-1/package/extracted/app.py'
        app.parent.mkdir(parents=True)
        app.write_text('pass\n', encoding='utf-8')
        record = {'state': 'active', 'root': str(self.root.resolve()), 'python': str(python)}
        target = self.root / 'State/installations/lic-lite.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(record), encoding='utf-8')
        self.old_record = target.read_bytes()
        profile = recommended_profile(DELIVERY / 'recipes/compatibility/profiles')
        core = component_from_manifest(profile.component_by_id('lic-core'),
                                       state='installed', enabled=True,
                                       readiness={'version': 'test', 'passed': True},
                                       resources=(ResourceState(
                                           'python-environment', 'runtime', 'test', str(python),
                                           'manager-owned', 'copy-and-verify', {}, {},
                                           {'version': 'test', 'passed': True}),))
        write_inventory(self.root, replace_component(empty_inventory(self.root, profile), core))
        self.old_inventory = (self.root / 'State/components/inventory.json').read_bytes()

    def _fake_acquire(self, descriptor, *args, **kwargs):
        return AcquiredArtifact(descriptor, str(self.root / 'fixture.whl'),
                                descriptor.expected_sha256, descriptor.expected_size or 1,
                                'verified', True, True, 0)

    def _fake_venv(self, runtime, candidate, **kwargs):
        python = candidate / 'Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'candidate')
        return python

    def _fake_resource(self, root, resource, acquired):
        path = operations.resource_destination(root, resource)
        if resource['installation']['adapter'] == 'verified-zip-members-v1':
            path.mkdir(parents=True, exist_ok=True)
            (path / 'ffmpeg.exe').write_bytes(b'fixture-resource')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture-resource')
        return {'path': str(path), 'reused': False}

    def _run(self, failure=None, *, component_id='face-analysis', cancel=None, resume=False):
        def validate(*args, **kwargs):
            if failure == 'validation' and 'venv-provider-' in str(args[1]):
                return {'passed': False}
            return {'passed': True}

        def install(*args, **kwargs):
            if failure == 'pip':
                raise RuntimeError('injected package installation failure')
            return {'passed': True}

        def resource(*args, **kwargs):
            if failure == 'resource':
                raise RuntimeError('injected provider validation failure')
            return {'version': 'test', 'passed': True}

        def acquire(*args, **kwargs):
            if failure == 'acquire':
                raise RuntimeError('injected acquisition failure')
            if failure == 'face-model-http' and args[0].artifact_id == 'opencv-sface-2021dec':
                raise AcquisitionFailure('not-found', args[0].artifact_id,
                                         'media.githubusercontent.com', 1, 'HTTP 404',
                                         url=args[0].url)
            return self._fake_acquire(*args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(operations, '_acquire', side_effect=acquire))
            stack.enter_context(patch.object(operations, 'promote_package'))
            stack.enter_context(patch.object(operations, 'create_final_path_venv', side_effect=self._fake_venv))
            stack.enter_context(patch.object(operations, 'install_locked_wheels', side_effect=install))
            if failure == 'face-model-publication':
                def publish(root, resource, acquired):
                    if resource['artifact']['artifact_id'] == 'opencv-sface-2021dec':
                        raise PermissionError('managed model destination is locked')
                    return self._fake_resource(root, resource, acquired)
                stack.enter_context(patch.object(operations, 'install_resource', side_effect=publish))
            else:
                stack.enter_context(patch.object(operations, 'install_resource', side_effect=self._fake_resource))
            stack.enter_context(patch.object(operations, 'validate_resource', side_effect=resource))
            stack.enter_context(patch.object(operations, 'validate_environment', side_effect=validate))
            stack.enter_context(patch.object(operations, 'synchronize_resource_library'))
            return operations.execute(DELIVERY, self.root, component_id,
                                      resume=resume, cancel_requested=cancel or (lambda: False))

    def _assert_core_preserved(self):
        self.assertEqual(json.loads((self.root / 'State/installations/lic-lite.json').read_text()),
                         json.loads(self.old_record))
        self.assertEqual((self.root / 'State/components/inventory.json').read_bytes(), self.old_inventory)
        self.assertEqual(managed_venv(self.root), self.old_venv)
        self.assertTrue((self.old_venv / 'Scripts/python.exe').is_file())

    def test_failure_before_dependency_change_preserves_core(self):
        with self.assertRaisesRegex(RuntimeError, 'acquisition'):
            self._run('acquire')
        self._assert_core_preserved()
        self.assertNotIn('face-analysis', load_inventory(self.root).installed_component_ids)

    def test_failed_package_install_preserves_core_and_requires_fresh_attempt(self):
        with self.assertRaisesRegex(RuntimeError, 'package installation'):
            self._run('pip')
        self._assert_core_preserved()
        recovery = operations.inspect_recovery(DELIVERY, self.root, 'face-analysis')
        self.assertEqual(recovery.status, 'failed')
        self.assertFalse(recovery.resumable)
        journal = json.loads(recovery.journal_path.read_text(encoding='utf-8'))
        self.assertTrue(journal['provider_diagnostic']['core_validation_after_failure']['passed'])
        self.assertEqual(journal['provider_diagnostic']['command_category'], 'package-install')
        self.assertEqual(journal['provider_diagnostic']['old_venv'], str(self.old_venv))
        with self.assertRaises(ValueError):
            self._run(resume=True)
        self._assert_core_preserved()

    def test_validation_failure_does_not_publish_provider_or_break_core(self):
        with self.assertRaisesRegex(RuntimeError, 'profile failed validation'):
            self._run('validation')
        self._assert_core_preserved()
        self.assertNotIn('face-analysis', load_inventory(self.root).installed_component_ids)

    def test_provider_validation_failure_does_not_publish_provider_or_break_core(self):
        with self.assertRaisesRegex(RuntimeError, 'provider validation'):
            self._run('resource')
        self._assert_core_preserved()
        self.assertNotIn('face-analysis', load_inventory(self.root).installed_component_ids)

    def test_success_promotes_validated_generation_and_keeps_old_core(self):
        result = self._run()
        self.assertEqual(result['state'], 'installed')
        active = managed_venv(self.root)
        self.assertNotEqual(active, self.old_venv)
        self.assertTrue((self.old_venv / 'Scripts/python.exe').is_file())
        inventory = load_inventory(self.root)
        self.assertIn('face-analysis', inventory.installed_component_ids)
        self.assertEqual(inventory.get('lic-core').resources[0].local_path,
                         str(active / 'Scripts/python.exe'))

    def test_publish_failure_rolls_back_active_record_and_inventory(self):
        original = operations.promote_generation
        def reject(*args, **kwargs):
            return original(*args[:-1], validate_active=lambda: (_ for _ in ()).throw(
                RuntimeError('injected promotion validation failure')))
        with patch.object(operations, 'promote_generation', side_effect=reject):
            with self.assertRaisesRegex(RuntimeError, 'promotion validation'):
                self._run()
        self._assert_core_preserved()

    def test_failed_attempt_can_be_retried_without_resume_loop(self):
        with self.assertRaises(RuntimeError):
            self._run('resource')
        self.assertFalse(operations.inspect_recovery(DELIVERY, self.root, 'face-analysis').resumable)
        result = self._run()
        self.assertEqual(result['state'], 'installed')
        self.assertIsNone(operations.inspect_recovery(DELIVERY, self.root, 'face-analysis'))
        self.assertTrue(list((self.root / 'State/operations/history').glob('component-face-analysis-*.json')))

    def test_stale_plan_cannot_be_resumed(self):
        with self.assertRaises(RuntimeError):
            self._run('resource')
        path = self.root / 'State/operations/component-face-analysis.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['plan_digest'] = '0' * 64
        path.write_text(json.dumps(data), encoding='utf-8')
        recovery = operations.inspect_recovery(DELIVERY, self.root, 'face-analysis')
        self.assertTrue(recovery.blocked)
        self.assertFalse(recovery.resumable)

    def test_cancel_after_candidate_build_is_resumable_without_mutating_core(self):
        journal_path = self.root / 'State/operations/component-face-analysis.json'
        def cancel_after_build():
            if not journal_path.is_file():
                return False
            data = json.loads(journal_path.read_text(encoding='utf-8'))
            return any(step['name'] == 'install_dependencies' and step['status'] == 'completed'
                       for step in data['steps'])
        with self.assertRaises(AcquisitionCancelled):
            self._run(cancel=cancel_after_build)
        self._assert_core_preserved()
        self.assertTrue(operations.inspect_recovery(DELIVERY, self.root, 'face-analysis').resumable)
        self.assertEqual(self._run(resume=True)['state'], 'installed')
        self.assertIn('face-analysis', load_inventory(self.root).installed_component_ids)

    def test_failed_journal_maps_to_repair_action_after_relaunch(self):
        with self.assertRaises(RuntimeError):
            self._run('resource')
        recovery = operations.inspect_recovery(DELIVERY, self.root, 'face-analysis')
        definition = next(item for item in load_component_catalog(DELIVERY / 'recipes/lic-components.json')
                          if item.component_id == 'face-analysis')
        facts = ComponentFacts(ComponentPhase.ERROR, resumable=recovery.resumable,
                               recovery_blocked=recovery.blocked)
        self.assertEqual(component_action(definition, facts), ComponentAction.REPAIR)

    def test_face_model_http_failure_remains_visible_and_core_is_unchanged(self):
        with self.assertRaises(AcquisitionFailure) as failure:
            self._run('face-model-http')
        self._assert_core_preserved()
        recovery = operations.inspect_recovery(DELIVERY, self.root, 'face-analysis')
        self.assertEqual(recovery.status, 'failed')
        self.assertFalse(recovery.resumable)
        definition = next(item for item in load_component_catalog(DELIVERY / 'recipes/lic-components.json')
                          if item.component_id == 'face-analysis')
        message, diagnostic = provider_failure_presentation(definition, recovery.failure)
        self.assertIn('SFace model', message)
        self.assertIn('404', message)
        self.assertIn('Repair', message)
        self.assertIn('Source URL: https://media.githubusercontent.com/', diagnostic)
        journal = json.loads(recovery.journal_path.read_text(encoding='utf-8'))
        self.assertEqual(journal['provider_diagnostic']['artifact_id'], 'opencv-sface-2021dec')
        self.assertTrue(journal['provider_diagnostic']['core_validation_after_failure']['passed'])
        self.assertEqual(self._run()['state'], 'installed')
        self.assertIsNone(operations.inspect_recovery(DELIVERY, self.root, 'face-analysis'))

    def test_face_publication_failure_reports_stage_and_preserves_core(self):
        with self.assertRaises(AcquisitionFailure) as failure:
            self._run('face-model-publication')
        self.assertEqual(failure.exception.category, 'publication-failure')
        self.assertEqual(failure.exception.stage, 'publication')
        self.assertEqual(failure.exception.artifact_id, 'opencv-sface-2021dec')
        self._assert_core_preserved()
        recovery = operations.inspect_recovery(DELIVERY, self.root, 'face-analysis')
        definition = next(item for item in load_component_catalog(DELIVERY / 'recipes/lic-components.json')
                          if item.component_id == 'face-analysis')
        message, diagnostic = provider_failure_presentation(definition, recovery.failure)
        self.assertIn('SFace model', message)
        self.assertIn('Stage: publication', diagnostic)
        self.assertEqual(self._run()['state'], 'installed')

    def test_available_opencv_package_does_not_hide_face_download_error(self):
        shell = ManagerShell.__new__(ManagerShell)
        shell.delivery, shell.root = DELIVERY, self.root
        original = ComponentFacts(ComponentPhase.ERROR, detail='SFace model: HTTP 404',
                                  diagnostic='Artifact: opencv-sface-2021dec')
        shell.component_facts = {'face-analysis': original}
        with patch('install_manager.manager_ui.component_resource_status',
                   side_effect=AssertionError('failed provider status must stay visible')):
            shell._refresh_managed_resource_facts()
        self.assertIs(shell.component_facts['face-analysis'], original)
        self.assertEqual(original.phase, ComponentPhase.ERROR)

    def test_exact_old_profile_keeps_existing_core_usable_without_ffmpeg(self):
        directory = DELIVERY / 'recipes/compatibility/profiles'
        old = accepted_installed_profile(directory, {
            'profile_id': '2026-09-11',
            'digest': 'ddd947d8dd52c28d4cb2ccae9ae998683807dd24dd5d1f42ebc95331886db6dd',
        }, {'lic-core', 'face-analysis'})
        self.assertEqual(old.profile_id, '2026-09-11')
        with self.assertRaisesRegex(ValueError, 'differs'):
            accepted_installed_profile(directory, {
                'profile_id': '2026-09-11',
                'digest': old.digest,
            }, {'lic-core', 'video-extraction'})

    def test_corrected_ffmpeg_notice_hash_is_in_new_immutable_profile(self):
        profile = recommended_profile(DELIVERY / 'recipes/compatibility/profiles')
        self.assertEqual(profile.profile_id, '2026-09-26')
        members = profile.component_by_id('video-extraction').raw['resources'][0]['installation']['members']
        notice = next(item for item in members if item['destination'] == 'LICENSE.txt')
        self.assertEqual(notice['sha256'],
                         'da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768')

    def test_previous_face_profile_remains_accepted_with_same_wheel_closure(self):
        directory = DELIVERY / 'recipes/compatibility/profiles'
        old = accepted_installed_profile(directory, {
            'profile_id': '2026-09-25',
            'digest': 'bb5bf432f745da4f2dc613137817044915329c2c03f0b39c3e1bc4f0f2a94671',
        }, {'lic-core', 'face-analysis', 'video-extraction'})
        self.assertEqual(old.profile_id, '2026-09-25')
        with self.assertRaisesRegex(ValueError, 'differs'):
            accepted_installed_profile(directory, {
                'profile_id': '2026-09-25', 'digest': '0' * 64,
            }, {'lic-core', 'face-analysis'})

    def test_relaunch_restores_incomplete_provider_pointer_switch(self):
        old_record = json.loads(self.old_record)
        old_inventory = load_inventory(self.root)
        candidate = self.old_venv.with_name('venv-provider-crashproof')
        (candidate / 'Scripts').mkdir(parents=True)
        (candidate / 'Scripts/python.exe').write_bytes(b'candidate')
        journal = OperationJournal.create(
            self.root / 'State/operations', 'component-face-analysis', target_path=self.root,
            plan_digest='0' * 64, artifacts=[{}], steps=operations.STEPS)
        record_candidate(journal, self.root, old_record, old_inventory, self.old_venv, candidate)
        journal.data['provider_environment']['promotion_pending'] = True
        journal._write()
        new_record = dict(old_record, python=str(candidate / 'Scripts/python.exe'))
        (self.root / 'State/installations/lic-lite.json').write_text(json.dumps(new_record), encoding='utf-8')
        write_inventory(self.root, inventory_for_generation(old_inventory, candidate))
        restored = recover_pending_provider_promotions(self.root)
        self.assertEqual(restored, (journal.path,))
        self._assert_core_preserved()
        self.assertEqual(recover_pending_provider_promotions(self.root), ())

    def _assert_provider_success(self, component_id):
        result = self._run(component_id=component_id)
        self.assertEqual(result['state'], 'installed')
        self.assertIn(component_id, load_inventory(self.root).installed_component_ids)
        manifest = recommended_profile(
            DELIVERY / 'recipes/compatibility/profiles').component_by_id(component_id)
        expected = [str(operations.resource_destination(self.root, resource))
                    for resource in manifest.raw['resources']]
        self.assertEqual(result['resources'], expected)
        self.assertTrue((self.old_venv / 'Scripts/python.exe').is_file())

    def test_body_uses_canonical_resources_and_preserves_old_core(self):
        self._assert_provider_success('body-analysis')

    def test_ffmpeg_uses_canonical_resources_and_preserves_old_core(self):
        self._assert_provider_success('video-extraction')

    def test_body_validation_failure_preserves_core(self):
        with self.assertRaisesRegex(RuntimeError, 'provider validation'):
            self._run('resource', component_id='body-analysis')
        self._assert_core_preserved()
        self.assertFalse(operations.inspect_recovery(
            DELIVERY, self.root, 'body-analysis').resumable)

    def test_ffmpeg_validation_failure_preserves_core(self):
        with self.assertRaisesRegex(RuntimeError, 'provider validation'):
            self._run('resource', component_id='video-extraction')
        self._assert_core_preserved()
        self.assertFalse(operations.inspect_recovery(
            DELIVERY, self.root, 'video-extraction').resumable)

    def test_shared_certifi_alias_is_accepted_only_with_exact_hash(self):
        profile = recommended_profile(DELIVERY / 'recipes/compatibility/profiles')
        wheel = next(package for component in profile.components.values()
                     for package in component.packages
                     if package.name == 'certifi' and package.artifact_id == 'certifi')
        with patch.object(managed_resources, '_destination_state', return_value='already-present'):
            location = managed_resources.promote_package(
                DELIVERY, self.root, self.root / 'unused.whl', wheel.artifact_id,
                wheel.version, wheel.sha256)
            self.assertEqual(location, managed_resources.managed_artifact_path(
                DELIVERY, self.root, wheel.artifact_id, wheel.version, wheel.sha256))
            with self.assertRaisesRegex(ValueError, 'approved managed-resource profile'):
                managed_resources.promote_package(
                    DELIVERY, self.root, self.root / 'unused.whl', wheel.artifact_id,
                    wheel.version, '0' * 64)

    def _run_florence(self, failure=None):
        model_root = self.root / 'Data/Models'
        snapshot = model_root / 'huggingface/hub/fixture/snapshots/pinned'
        snapshot.mkdir(parents=True, exist_ok=True)
        fixture_wheel = self.root / 'fixture.whl'
        fixture_wheel.write_bytes(b'fixture-wheel')
        evidence = {'status': 'compatible', 'snapshot': str(snapshot),
                    'hub_root': str(model_root / 'huggingface/hub')}
        def install(*args, **kwargs):
            if failure == 'pip':
                raise RuntimeError('injected Florence package failure')
            return {'passed': True}
        def validate(*args, **kwargs):
            if failure == 'validate' and 'venv-provider-' in str(args[1]):
                return {'passed': False}
            return {'passed': True}
        def probe(*args, **kwargs):
            if failure == 'caption':
                raise RuntimeError('injected Florence caption failure')
            return {'passed': True, 'network_attempts': [], 'denied_writes': []}
        with ExitStack() as stack:
            stack.enter_context(patch.object(florence, 'inspect_model_storage', return_value=evidence))
            stack.enter_context(patch.object(florence, 'verify_snapshot', return_value={'verified': True}))
            stack.enter_context(patch.object(florence, 'managed_artifact_path', return_value=fixture_wheel))
            stack.enter_context(patch.object(florence, 'acquire_artifact', side_effect=self._fake_acquire))
            stack.enter_context(patch.object(florence, 'promote_package'))
            stack.enter_context(patch.object(florence, 'create_final_path_venv', side_effect=self._fake_venv))
            stack.enter_context(patch.object(florence, 'install_locked_wheels', side_effect=install))
            stack.enter_context(patch.object(florence, 'validate_environment', side_effect=validate))
            stack.enter_context(patch.object(florence, 'run_probe', side_effect=probe))
            stack.enter_context(patch.object(florence, 'synchronize_resource_library'))
            stack.enter_context(patch.object(florence, 'HuggingFaceSource',
                                           side_effect=AssertionError('existing model was downloaded')))
            return florence.execute(DELIVERY, self.root, model_root)

    def test_florence_success_promotes_only_after_caption_and_preserves_old_core(self):
        result = self._run_florence()
        self.assertTrue(result['caption']['passed'])
        self.assertIn('florence-captioning', load_inventory(self.root).installed_component_ids)
        self.assertTrue((self.old_venv / 'Scripts/python.exe').is_file())
        self.assertNotEqual(managed_venv(self.root), self.old_venv)
        self.assertTrue(Path(result['snapshot']).is_dir())

    def test_florence_package_and_caption_failures_preserve_core(self):
        for failure in ('pip', 'caption'):
            with self.subTest(failure=failure):
                with self.assertRaisesRegex(RuntimeError, 'injected Florence'):
                    self._run_florence(failure)
                self._assert_core_preserved()
                self.assertNotIn('florence-captioning', load_inventory(self.root).installed_component_ids)
                recovery = florence.inspect_recovery(DELIVERY, self.root, self.root / 'Data/Models')
                self.assertFalse(recovery.resumable)


if __name__ == '__main__':
    unittest.main()
