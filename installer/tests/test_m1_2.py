"""M1.2 tests for trusted acquisition, final-path planning and journaling."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from install_manager.acquisition import AcquisitionFailure, AcquisitionPolicy, acquire_artifact, admit_local_artifact
from install_manager.artifacts import AcquiredArtifact, ArtifactDescriptor
from install_manager.dependency_lock import DependencyLock, LockedWheel
from install_manager.environment import build_environment_plan, create_final_path_venv, extract_runtime
from install_manager.executor import execute_environment_plan
from install_manager.journal import OperationJournal


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def descriptor(data: bytes = b"runtime") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        "pythoncore-win-x64", "3.14.6", "python-3.14.6-amd64.zip",
        "https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.zip",
        hashlib.sha256(data).hexdigest(), "Python Software Foundation",
        "Python release manifest", "PSF-2.0", len(data),
    )


def lock_for(data: bytes = b"wheel") -> DependencyLock:
    artifact = ArtifactDescriptor(
        "wheel-packaging", "26.2", "packaging-26.2-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/packaging-26.2-py3-none-any.whl",
        hashlib.sha256(data).hexdigest(), "PyPI project: packaging", "PyPI", "Apache-2.0", len(data),
    )
    return DependencyLock(1, "windows-nvidia-cu130-base", "3.14.6", "win_amd64",
                          (LockedWheel("packaging", "26.2", artifact, ("packaging",)),))


class AcquisitionTests(unittest.TestCase):
    def test_runtime_artifact_model_and_verified_acquisition(self) -> None:
        data = b"runtime"
        with tempfile.TemporaryDirectory(prefix="im-m12-acquire-") as temporary:
            calls = []

            def opener(request, timeout):
                calls.append((request.full_url, timeout))
                return Response(data)

            result = acquire_artifact(descriptor(data), Path(temporary),
                                      AcquisitionPolicy(("www.python.org",), 2, 0, 0), opener=opener)
            self.assertTrue(result.verified)
            self.assertFalse(result.reused)
            self.assertEqual(result.actual_sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(len(calls), 1)

    def test_acquisition_reports_only_measured_byte_progress(self) -> None:
        data = b"runtime"
        with tempfile.TemporaryDirectory(prefix="im-m12-progress-") as temporary:
            events = []
            acquire_artifact(descriptor(data), Path(temporary),
                             AcquisitionPolicy(("www.python.org",), 1, 0, 0),
                             opener=lambda request, timeout: Response(data), progress=events.append)
            self.assertTrue(events)
            self.assertEqual(events[-1]['downloaded_bytes'], len(data))
            self.assertEqual(events[-1]['total_bytes'], len(data))
            self.assertTrue(events[-1]['complete'])

    def test_verified_cache_reuse_avoids_redownload(self) -> None:
        data = b"runtime"
        with tempfile.TemporaryDirectory(prefix="im-m12-reuse-") as temporary:
            calls = 0

            def opener(request, timeout):
                nonlocal calls
                calls += 1
                return Response(data)

            policy = AcquisitionPolicy(("www.python.org",), 2, 0, 0)
            acquire_artifact(descriptor(data), Path(temporary), policy, opener=opener)
            reused = acquire_artifact(descriptor(data), Path(temporary), policy, opener=opener)
            self.assertTrue(reused.reused)
            self.assertEqual(reused.attempts, 0)
            self.assertEqual(calls, 1)

    def test_concurrent_requests_share_one_verified_acquisition(self) -> None:
        data = b"runtime"
        with tempfile.TemporaryDirectory(prefix="im-m12-concurrent-") as temporary:
            calls = 0

            def opener(request, timeout):
                nonlocal calls
                calls += 1
                return Response(data)

            policy = AcquisitionPolicy(("www.python.org",), 2, 0, 0)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = tuple(pool.map(
                    lambda _: acquire_artifact(descriptor(data), Path(temporary), policy,
                                               opener=opener),
                    range(2),
                ))
            self.assertEqual(calls, 1)
            self.assertEqual(sum(result.reused for result in results), 1)
            self.assertTrue(all(result.verified for result in results))

    def test_hash_mismatch_removes_partial_and_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-hash-") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                acquire_artifact(descriptor(b"expected"), root,
                                 AcquisitionPolicy(("www.python.org",), 1, 0, 0),
                                 opener=lambda request, timeout: Response(b"wrong!!!"))
            self.assertEqual(list(root.rglob("*.partial")), [])
            self.assertEqual(list((root / "verified").rglob("*.zip")), [])

    def test_incomplete_acquisition_retries_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-retry-") as temporary:
            calls = 0
            sleeps = []

            def opener(request, timeout):
                nonlocal calls
                calls += 1
                raise OSError("interrupted")

            with self.assertRaises(AcquisitionFailure) as failure:
                acquire_artifact(descriptor(), Path(temporary),
                                 AcquisitionPolicy(("www.python.org",), 3, 0.25, 0),
                                 opener=opener, sleep=sleeps.append)
            self.assertEqual(failure.exception.attempts, 3)
            self.assertEqual(failure.exception.category, "network-unavailable")
            self.assertEqual(calls, 3)
            self.assertEqual(sleeps, [0.25, 0.5])

    def test_source_policy_rejects_non_https_or_untrusted_host(self) -> None:
        base = descriptor()
        for url in ("http://www.python.org/runtime.zip", "https://example.org/runtime.zip"):
            bad = ArtifactDescriptor(base.artifact_id, base.version, "runtime.zip", url,
                                     base.expected_sha256, base.publisher, base.source_name,
                                     base.license_id, base.expected_size)
            with self.assertRaises(ValueError):
                bad.validate(("www.python.org",))

    def test_retry_after_is_respected_with_a_bounded_retry_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-retry-after-") as temporary:
            calls = 0
            sleeps = []

            def opener(request, timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise urllib.error.HTTPError(request.full_url, 429, "slow down",
                                                 {"Retry-After": "3"}, None)
                return Response(b"runtime")

            result = acquire_artifact(descriptor(), Path(temporary),
                                      AcquisitionPolicy(("www.python.org",), 2, 0.25, 0),
                                      opener=opener, sleep=sleeps.append)
            self.assertTrue(result.verified)
            self.assertEqual(calls, 2)
            self.assertEqual(sleeps, [3.0])

    def test_local_candidate_is_reverified_before_cache_admission(self) -> None:
        data = b"runtime"
        with tempfile.TemporaryDirectory(prefix="im-m12-admit-") as temporary:
            root = Path(temporary)
            candidate = root / "candidate.zip"
            candidate.write_bytes(data)
            result = admit_local_artifact(descriptor(data), candidate, root / "cache",
                                          AcquisitionPolicy(("www.python.org",)))
            self.assertTrue(result.verified)
            self.assertEqual(Path(result.cache_path).read_bytes(), data)
            candidate.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                admit_local_artifact(descriptor(data), candidate, root / "other-cache",
                                     AcquisitionPolicy(("www.python.org",)))


class EnvironmentTests(unittest.TestCase):
    def test_plan_is_deterministic_and_selects_final_path_first(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-plan-") as temporary:
            first = build_environment_plan("op-001", "windows-nvidia-cu130-base",
                                           descriptor(), "a" * 64, Path(temporary))
            second = build_environment_plan("op-001", "windows-nvidia-cu130-base",
                                            descriptor(), "a" * 64, Path(temporary))
            self.assertEqual(first.canonical_json(), second.canonical_json())
            self.assertEqual(first.digest(), second.digest())
            self.assertTrue(first.final_venv_path.endswith("apps/lic-m12-proof/venv"))
            self.assertFalse(first.activation_allowed)

    def test_populated_target_is_a_blocker_and_creation_refuses_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-target-") as temporary:
            root = Path(temporary)
            target = root / "apps" / "lic-m12-proof" / "venv"
            target.mkdir(parents=True)
            plan = build_environment_plan("op-002", "profile", descriptor(), "b" * 64, root)
            self.assertTrue(any("final venv" in item for item in plan.blockers))
            with self.assertRaises(FileExistsError):
                create_final_path_venv(root / "python.exe", target,
                                       approved_root=root, log_path=root / "logs" / "venv.log")

    def test_runtime_staging_rejects_traversal_and_is_confined(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-stage-") as temporary:
            root = Path(temporary)
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape.txt", b"bad")
            data = archive.read_bytes()
            item = descriptor(data)
            acquired = AcquiredArtifact(item, archive.as_posix(), item.expected_sha256,
                                        len(data), "verified", True, False, 1)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                extract_runtime(acquired, root / "runtime")
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse((root / "runtime").exists())

    def test_corrupt_runtime_archive_never_publishes_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-corrupt-runtime-") as temporary:
            root = Path(temporary)
            archive = root / "runtime.zip"
            archive.write_bytes(b"not a zip archive")
            item = descriptor(archive.read_bytes())
            acquired = AcquiredArtifact(item, archive.as_posix(), item.expected_sha256,
                                        archive.stat().st_size, "verified", True, False, 1)
            with self.assertRaises(zipfile.BadZipFile):
                extract_runtime(acquired, root / "runtime")
            self.assertFalse((root / "runtime").exists())
            self.assertEqual(list(root.glob(".runtime.partial-*")), [])

    def test_journal_atomic_update_and_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-journal-") as temporary:
            root = Path(temporary)
            journal = OperationJournal.create(root, "op-003", target_path=root / "venv",
                                              plan_digest="c" * 64, artifacts=[], steps=("one",))
            journal.set_status("running")
            journal.set_step("one", "failed", error="controlled")
            journal.add_cleanup_action("review", performed=False)
            journal.set_status("failed", failure="controlled")
            loaded = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["status"], "failed")
            self.assertEqual(loaded["steps"][0]["status"], "failed")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_dependency_failure_is_recorded_without_environment_mutation(self) -> None:
        self._assert_executor_failure("install_locked_wheels", RuntimeError("dependency failed"),
                                      "install_dependencies")

    def test_venv_creation_failure_is_recorded(self) -> None:
        self._assert_executor_failure("create_final_path_venv", RuntimeError("venv failed"),
                                      "create_final_path_venv")

    def test_validation_failure_is_recorded(self) -> None:
        self._assert_executor_failure("validate_environment", {"passed": False},
                                      "validate_environment")

    def test_executor_does_not_write_to_existing_lic_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-isolation-") as temporary:
            parent = Path(temporary)
            operation_root = parent / "operation"
            existing_lic = parent / "existing-lic"
            existing_lic.mkdir()
            sentinel = existing_lic / "user-state.sqlite3"
            sentinel.write_bytes(b"must remain unchanged")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            plan = build_environment_plan("op-isolation", "profile", descriptor(),
                                          lock_for().digest(), operation_root)
            runtime_result = AcquiredArtifact(
                descriptor(), (operation_root / "runtime.zip").as_posix(),
                descriptor().expected_sha256, 7, "verified", True, False, 1)
            wheel_result = AcquiredArtifact(
                lock_for().wheels[0].artifact, (operation_root / "wheel.whl").as_posix(),
                lock_for().wheels[0].artifact.expected_sha256, 5, "verified", True, False, 1)
            with patch("install_manager.executor.acquire_artifact", return_value=runtime_result), \
                    patch("install_manager.executor.extract_runtime",
                          return_value=operation_root / "runtimes" / "python"), \
                    patch("install_manager.executor.create_final_path_venv",
                          return_value=operation_root / "apps" / "lic" / "venv" / "Scripts" / "python.exe"), \
                    patch("install_manager.executor.acquire_locked_wheels", return_value=(wheel_result,)), \
                    patch("install_manager.executor.install_locked_wheels", return_value={"exit_code": 0}), \
                    patch("install_manager.executor.validate_environment", return_value={"passed": True}):
                result = execute_environment_plan(
                    plan, lock_for(), AcquisitionPolicy(("www.python.org",)),
                    lic_source_root=existing_lic,
                )
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
            self.assertEqual(tuple(existing_lic.iterdir()), (sentinel,))

    def _assert_executor_failure(self, failing: str, failure, expected_step: str) -> None:
        with tempfile.TemporaryDirectory(prefix="im-m12-executor-") as temporary:
            root = Path(temporary)
            plan = build_environment_plan("op-failure", "profile", descriptor(),
                                          lock_for().digest(), root)
            fake_runtime = root / "runtimes" / "python-3.14.6-win-x64"
            fake_venv_python = root / "apps" / "lic-m12-proof" / "venv" / "Scripts" / "python.exe"
            runtime_result = AcquiredArtifact(descriptor(), (root / "runtime.zip").as_posix(),
                                              descriptor().expected_sha256, 7, "verified", True, False, 1)
            wheel_result = AcquiredArtifact(lock_for().wheels[0].artifact,
                                            (root / "wheel.whl").as_posix(),
                                            lock_for().wheels[0].artifact.expected_sha256,
                                            5, "verified", True, False, 1)
            patches = {
                "acquire_artifact": patch("install_manager.executor.acquire_artifact", return_value=runtime_result),
                "extract_runtime": patch("install_manager.executor.extract_runtime", return_value=fake_runtime),
                "create_final_path_venv": patch("install_manager.executor.create_final_path_venv", return_value=fake_venv_python),
                "acquire_locked_wheels": patch("install_manager.executor.acquire_locked_wheels", return_value=(wheel_result,)),
                "install_locked_wheels": patch("install_manager.executor.install_locked_wheels", return_value={"exit_code": 0}),
                "validate_environment": patch("install_manager.executor.validate_environment", return_value={"passed": True}),
            }
            if isinstance(failure, Exception):
                patches[failing] = patch(f"install_manager.executor.{failing}", side_effect=failure)
            else:
                patches[failing] = patch(f"install_manager.executor.{failing}", return_value=failure)
            with patches["acquire_artifact"], patches["extract_runtime"], patches["create_final_path_venv"], \
                    patches["acquire_locked_wheels"], patches["install_locked_wheels"], \
                    patches["validate_environment"]:
                with self.assertRaises(RuntimeError):
                    execute_environment_plan(plan, lock_for(), AcquisitionPolicy(("www.python.org",)))
            journal = json.loads((root / "state" / "operations" / "op-failure.json").read_text())
            self.assertEqual(journal["status"], "failed")
            step = next(item for item in journal["steps"] if item["name"] == expected_step)
            self.assertEqual(step["status"], "failed")

    def test_process_environment_path_is_never_modified(self) -> None:
        before = os.environ.get("PATH")
        with tempfile.TemporaryDirectory(prefix="im-m12-env-") as temporary:
            build_environment_plan("op-path", "profile", descriptor(), "d" * 64, Path(temporary))
        self.assertEqual(os.environ.get("PATH"), before)


if __name__ == "__main__":
    unittest.main()
