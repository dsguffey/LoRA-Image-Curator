"""Contracts for provenance, retirement, profile selection and install safety."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import sys
import zipfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from wheel.wheelfile import WheelFile
from lic_dependencies.insightface_build import build_candidate, dependency_decision, inspect_wheel
from lic_dependencies.installer import install_component
from lic_dependencies.profile import PROFILE, ROOT, conflicts, profile_for_cuda
from lic_dependencies.upstream import check_url


def synthetic_wheel(root: Path, requirements: tuple[str, ...]) -> Path:
    """Create a signed-by-hash synthetic fixture, not an upstream executable."""
    path = root / "insightface-1.0.1-py3-none-any.whl"
    with WheelFile(path, "w") as wheel:
        wheel.writestr("insightface/__init__.py", b"__version__ = '1.0.1'\n")
        info = "insightface-1.0.1.dist-info"
        wheel.writestr(f"{info}/METADATA", "Metadata-Version: 2.1\nName: insightface\nVersion: 1.0.1\n" +
                       "".join(f"Requires-Dist: {r}\n" for r in requirements) + "\nUpstream description\n")
        wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(f"{info}/licenses/LICENSE", "Upstream fixture license")
    return path


class MaintenanceTests(unittest.TestCase):
    """Exercise malformed inputs and decision boundaries without network or installs."""

    def test_profiles_and_duplicates(self):
        self.assertEqual(profile_for_cuda(None), "cpu")
        self.assertEqual(profile_for_cuda("13.0"), PROFILE["profile"])
        with self.assertRaises(RuntimeError):
            profile_for_cuda("12.8")
        self.assertTrue(conflicts({"onnxruntime": "1", "onnxruntime-gpu": "1"}))
        self.assertTrue(conflicts({"opencv-python": "1", "opencv-contrib-python": "1"}))
        self.assertEqual(conflicts({"onnxruntime-gpu": "1", "opencv-contrib-python": "1"}, nvidia=True), [])

    def test_retirement_and_new_conflicts(self):
        self.assertFalse(dependency_decision(["numpy", "onnxruntime-gpu>=1.27", "opencv-contrib-python"])['patch_required'])
        self.assertTrue(dependency_decision(["onnxruntime", "opencv-python"])['patch_required'])
        self.assertEqual(len(dependency_decision(["onnxruntime; sys_platform == 'win32'"])['substitutions']), 1)
        self.assertFalse(dependency_decision(["onnxruntime; extra == 'unused'"])['patch_required'])
        with self.assertRaises(ValueError):
            dependency_decision(["onnxruntime-gpu>=99"])
        with self.assertRaises(ValueError):
            dependency_decision(["opencv-python-headless"])

    def test_build_provenance_reproducibility_and_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = synthetic_wheel(root, ("numpy", "onnxruntime", "opencv-python"))
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            reports = [build_candidate(original, version="1.0.1", source="https://pypi.org/fixture",
                        expected_hash=digest, output=root / name) for name in ("a", "b")]
            self.assertEqual(reports[0]['wheel_sha256'], reports[1]['wheel_sha256'])
            files, info, meta = inspect_wheel(root / 'a' / reports[0]['wheel'])
            self.assertEqual(files['insightface/__init__.py'], b"__version__ = '1.0.1'\n")
            self.assertIn('onnxruntime-gpu==1.28.0', meta.get_all('Requires-Dist'))
            self.assertEqual(meta['Version'], '1.0.1+lic.cuda13.1')
            self.assertEqual(json.loads(files[info + '/LIC_PROVENANCE.json'])['upstream_sha256'], digest)
            self.assertFalse(reports[0]['approved_for_lic'])
            with self.assertRaises(ValueError):
                build_candidate(original, version='1.0.1', source='', expected_hash='0'*64, output=root/'bad')

    def test_official_artifact_is_not_repacked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = synthetic_wheel(root, ('numpy',))
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            report = build_candidate(original, version='1.0.1', source='fixture', expected_hash=digest, output=root/'out')
            self.assertEqual(report['wheel_sha256'], digest)
            self.assertFalse(report['patch_required'])

    def test_unsafe_windows_wheel_members_are_rejected_before_unpack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('../escape.py', 'insightface/CON', 'insightface/x. ', 'insightface//x'):
                wheel = synthetic_wheel(root, ())
                with zipfile.ZipFile(wheel, 'a') as archive:
                    archive.writestr(name, b'unsafe fixture')
                with self.assertRaises(ValueError):
                    inspect_wheel(wheel)

    def test_no_writes_or_pip_for_cpu_face_or_dirty_environment(self):
        for state in ({'venv': True, 'cuda': None, 'packages': {}},
                      {'venv': True, 'cuda': '13.0', 'packages': {'onnxruntime': '1', 'onnxruntime-gpu': '1'}}):
            with patch('lic_dependencies.installer.target_state', return_value=state), patch('lic_dependencies.installer.pip_command') as pip:
                with self.assertRaises(RuntimeError):
                    install_component('face')
                pip.assert_not_called()

    def test_body_uses_exact_constraints_and_never_installs_standard_packages(self):
        state = {'venv': True, 'cuda': '13.0', 'packages': {}}
        with patch('lic_dependencies.installer.target_state', return_value=state), patch('lic_dependencies.installer.pip_command') as pip:
            install_component('body')
            install = pip.call_args_list[1].args
            self.assertIn(str(ROOT/'constraints-nvidia.txt'), install)
            self.assertIn(str(ROOT/'requirements-body.txt'), install)
            self.assertNotIn('--upgrade', pip.call_args_list[0].args)

    def test_requirements_consistency(self):
        import tomllib
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
        for name in ('mediapipe', 'opencv-contrib-python'):
            pin = f'{name}=={PROFILE[name]}'
            self.assertIn(pin, (ROOT/'requirements-body.txt').read_text())
            self.assertIn(pin, project['optional-dependencies']['body'])
        self.assertIn('insightface==1.0.1+lic.cuda13.1', (ROOT/'requirements-face.txt').read_text())
        for name in PROFILE['forbidden']:
            self.assertIn(f'{name}===LIC-FORBIDDEN', (ROOT/'constraints-lic.txt').read_text())
        for name in ('onnxruntime-gpu', 'opencv-contrib-python'):
            pin = f'{name}=={PROFILE[name]}'
            self.assertIn(pin, project['optional-dependencies']['face-nvidia'])
            self.assertIn(pin, (ROOT/'requirements-face.txt').read_text())
            self.assertIn(pin, (ROOT/'constraints-lic.txt').read_text())
        for name in ('torch', 'torchvision'):
            self.assertIn(f'{name}=={PROFILE[name]}', (ROOT/'constraints-nvidia.txt').read_text())
        self.assertIn(f"transformers=={PROFILE['transformers']}", project['dependencies'])

    def test_urls_reject_nonpublisher_redirects(self):
        for url in ('http://pypi.org/x', 'https://example.org/x', 'https://user@pypi.org/x'):
            with self.assertRaises(ValueError):
                check_url(url)

    @unittest.skipUnless(sys.platform == 'win32', 'Reviewed Windows repair path')
    def test_nvidia_repair_routes_to_reviewed_face_installer(self):
        import setup_assistant as setup
        runtime = {'version': PROFILE['torch'], 'cuda': '13.0', 'cuda_available': True,
                   'smoke_ok': True, 'device': 'test GPU', 'architectures': ['sm_120']}
        with patch.object(setup, 'ensure_local_environment'), \
             patch.object(setup, 'inspect_nvidia_runtime', return_value=(('test GPU', '610.74'),)), \
             patch.object(setup, '_read_package_versions', side_effect=[{'torch': PROFILE['torch'], 'torchvision': PROFILE['torchvision']}, {'insightface': '1.0.1+lic.cuda13.1'}]), \
             patch.object(setup, 'save_dependency_snapshot', return_value=Path('test-only.txt')), \
             patch.object(setup, '_inspect_torch_runtime', return_value=runtime), \
             patch.object(setup, '_pip_install') as pip, patch.object(setup, '_run') as run, \
             patch.object(setup, 'print_setup_status'):
            setup.install_tested_nvidia_pytorch()
            args = pip.call_args.args
            self.assertIn('torch==2.13.0+cu130', args)
            self.assertIn('torchvision==0.28.0+cu130', args)
            self.assertTrue(any(str(x).endswith('install_face_dependencies.py') for x in run.call_args.args[0]))
            self.assertNotIn('onnxruntime', args)

    def test_advertised_cuda_and_preload_failure_are_not_gpu_validation(self):
        from lic_dependencies.runtime_probe import inspect_runtime
        def preload():
            raise RuntimeError('synthetic missing DLL')
        ort = SimpleNamespace(get_available_providers=lambda: ['CUDAExecutionProvider'], preload_dlls=preload)
        torch = SimpleNamespace(version=SimpleNamespace(cuda='13.0'),
                                backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: 92000)))
        with patch.dict(sys.modules, {'onnxruntime': ort, 'torch': torch}):
            result = inspect_runtime()
        self.assertFalse(result['gpu_execution_ok'])
        self.assertIn('synthetic missing DLL', result['errors'][0])

    def test_cpu_to_nvidia_repair_rejects_cpu_ort_before_pip(self):
        import setup_assistant as setup
        state = {'venv': True, 'cuda': None, 'packages': {'onnxruntime': '1'}}
        with patch('lic_dependencies.installer.target_state', return_value=state), patch.object(setup, '_run') as run:
            with self.assertRaises(RuntimeError):
                setup._pip_install('install', f"torch=={PROFILE['torch']}")
            run.assert_not_called()

    def test_explicit_nvidia_repair_can_replace_other_cuda(self):
        import setup_assistant as setup
        state = {'venv': True, 'cuda': '12.8', 'packages': {}}
        with patch('lic_dependencies.installer.target_state', return_value=state), patch.object(setup, '_run') as run:
            setup._pip_install('install', f"torch=={PROFILE['torch']}")
            self.assertIn(str(ROOT/'constraints-nvidia.txt'), run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
