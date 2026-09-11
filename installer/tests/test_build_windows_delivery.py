"""Focused provenance contracts for the Windows delivery builder."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_windows_delivery', ROOT / 'tools/build_windows_delivery.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildProvenanceTests(unittest.TestCase):
    def test_native_origin_rejects_host_and_changed_staged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / 'approved'
            staged = root / 'staged'
            approved.mkdir()
            (approved / 'python314.dll').write_bytes(b'approved-runtime')
            shutil.copytree(approved, staged)
            self.assertEqual(BUILD.approved_native_origin(staged / 'python314.dll', approved, staged),
                             ('python314.dll', BUILD.sha256_file(approved / 'python314.dll')))
            (staged / 'python314.dll').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Unapproved staged native'):
                BUILD.approved_native_origin(staged / 'python314.dll', approved, staged)
            host = root / 'host-python314.dll'
            host.write_bytes(b'host')
            with self.assertRaisesRegex(ValueError, 'Unapproved native'):
                BUILD.approved_native_origin(host, approved, staged)

    def test_build_tool_lock_has_exact_cached_wheels(self):
        import json
        lock = json.loads((ROOT / 'tools/build-tools-windows.json').read_text(encoding='utf-8'))
        wheels = BUILD.build_tool_wheels(lock)
        self.assertEqual(len(wheels), 7)
        self.assertEqual([wheel.name for wheel in wheels], [item['filename'] for item in lock['wheels']])

    def test_readiness_probe_payload_includes_package_initializer_dependency(self):
        self.assertIn('__init__.py', BUILD.PROBE_FILES)
        self.assertIn('product.py', BUILD.PROBE_FILES)
        self.assertIn('lic_model_probe.py', BUILD.PROBE_FILES)


if __name__ == '__main__':
    unittest.main()
