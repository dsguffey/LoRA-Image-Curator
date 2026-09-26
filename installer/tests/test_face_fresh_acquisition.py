"""Fresh Face acquisition, verified publication, retry and actionable failures."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from install_manager.acquisition import (AcquisitionFailure, AcquisitionPolicy,
                                         acquire_artifact, sha256_file)
from install_manager.artifacts import ArtifactDescriptor
from install_manager.component_adapters import (acquisition_hosts, install_resource,
                                                resource_destination)
from install_manager.component_catalog import load_component_catalog
from install_manager.compatibility_profiles import recommended_profile
from install_manager import managed_resources
from install_manager.manager_ui import provider_failure_presentation


DELIVERY = Path(__file__).resolve().parents[1] / "src/install_manager"
PROFILES = DELIVERY / "recipes/compatibility/profiles"
DEFINITIONS = {item.component_id: item for item in load_component_catalog(
    DELIVERY / "recipes/lic-components.json")}


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class FaceFreshAcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "LIC Test"
        self.cache = self.root / "Data/Downloads"
        self.manifest = recommended_profile(PROFILES).component_by_id("face-analysis").raw
        self.resources = deepcopy(self.manifest["resources"])
        self.payloads = (b"fixture yunet model", b"fixture sface model")
        self.specs = []
        for resource, payload in zip(self.resources, self.payloads):
            artifact = resource["artifact"]
            artifact["expected_sha256"] = hashlib.sha256(payload).hexdigest()
            artifact["expected_size"] = len(payload)
            resource["validation"]["accepted_sha256"] = artifact["expected_sha256"]
            destination = resource_destination(self.root, resource)
            self.specs.append(managed_resources.ManagedResourceSpec(
                f"model:{artifact['artifact_id']}:{artifact['version']}:{artifact['expected_sha256']}",
                artifact["artifact_id"], "model-file", ("face-analysis",), destination,
                artifact["filename"], len(payload), (("sha256", artifact["expected_sha256"]),),
                artifact))
        self.policy = AcquisitionPolicy(("media.githubusercontent.com",), max_attempts=1,
                                        minimum_request_interval_seconds=0)

    def descriptor(self, index):
        return ArtifactDescriptor.from_dict(self.resources[index]["artifact"])

    def fetch(self, index, *, opener=None):
        return acquire_artifact(self.descriptor(index), self.cache, self.policy,
                                opener=opener or (lambda request, timeout: Response(self.payloads[index])),
                                sleep=lambda seconds: None)

    def test_approved_pair_is_exact_and_uses_binary_git_lfs_source(self):
        self.assertEqual(recommended_profile(PROFILES).profile_id, "2026-09-26.1")
        expected = (
            ("opencv-yunet-2026may", "face_detection_yunet_2026may.onnx", 229738,
             "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0"),
            ("opencv-sface-2021dec", "face_recognition_sface_2021dec.onnx", 38696353,
             "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
        )
        for resource, (artifact_id, filename, size, digest) in zip(self.manifest["resources"], expected):
            descriptor = ArtifactDescriptor.from_dict(resource["artifact"])
            self.assertEqual((descriptor.artifact_id, descriptor.filename,
                              descriptor.expected_size, descriptor.expected_sha256),
                             (artifact_id, filename, size, digest))
            self.assertEqual(descriptor.version, "47534e27c9851bb1128ccc0102f1145e27f23f98")
            self.assertEqual(acquisition_hosts(descriptor), ("media.githubusercontent.com",))
            self.assertIn("/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/",
                          descriptor.url)

    def test_both_missing_then_verified_publication_and_registry_make_pair_complete(self):
        with patch.object(managed_resources, "approved_resource_specs", return_value=tuple(self.specs)):
            self.assertEqual(len(managed_resources.component_resource_status(
                DELIVERY, self.root, "face-analysis")["missing"]), 2)
            for index in range(2):
                acquired = self.fetch(index)
                self.assertFalse(acquired.reused)
                self.assertEqual(acquired.actual_sha256, self.descriptor(index).expected_sha256)
                installed = install_resource(self.root, self.resources[index], acquired)
                self.assertEqual(Path(installed["path"]), self.specs[index].destination)
                self.assertEqual(sha256_file(self.specs[index].destination), acquired.actual_sha256)
                state = managed_resources.component_resource_status(
                    DELIVERY, self.root, "face-analysis")
                self.assertEqual(len(state["missing"]), 1-index)
                self.assertEqual(state["resource_complete"], index == 1)
            managed_resources.synchronize_resource_library(DELIVERY, self.root)
            library = managed_resources.load_resource_library(self.root)
            self.assertEqual(len(library["resources"]), 2)
            self.assertTrue(managed_resources.component_resource_status(
                DELIVERY, self.root, "face-analysis")["resource_complete"])

    def test_second_failure_retains_first_verified_file_and_retry_fetches_only_second(self):
        first = self.fetch(0)
        def missing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)
        with self.assertRaises(AcquisitionFailure) as failure:
            self.fetch(1, opener=missing)
        self.assertEqual(failure.exception.artifact_id, "opencv-sface-2021dec")
        self.assertTrue(Path(first.cache_path).is_file())
        self.assertFalse((self.cache / "verified/opencv-sface-2021dec" /
                          self.descriptor(1).version / self.descriptor(1).filename).exists())
        with patch.object(managed_resources, "approved_resource_specs", return_value=tuple(self.specs)):
            install_resource(self.root, self.resources[0], first)
            self.assertFalse(managed_resources.component_resource_status(
                DELIVERY, self.root, "face-analysis")["resource_complete"])
            reused = self.fetch(0, opener=lambda request, timeout: self.fail("YuNet was downloaded again"))
            self.assertTrue(reused.reused)
            second = self.fetch(1)
            install_resource(self.root, self.resources[1], second)
            self.assertTrue(managed_resources.component_resource_status(
                DELIVERY, self.root, "face-analysis")["resource_complete"])

    def test_http_timeout_and_tls_failures_name_sface_and_offer_repair(self):
        errors = (
            (urllib.error.HTTPError("https://media.githubusercontent.com/", 404, "missing", {}, None), "not-found"),
            (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
            (urllib.error.URLError(ssl.SSLError("certificate verify failed")), "tls-certificate"),
        )
        for error, category in errors:
            with self.subTest(category=category):
                with self.assertRaises(AcquisitionFailure) as failure:
                    self.fetch(1, opener=lambda request, timeout: (_ for _ in ()).throw(error))
                self.assertEqual(failure.exception.category, category)
                message, diagnostic = provider_failure_presentation(
                    DEFINITIONS["face-analysis"], failure.exception)
                self.assertIn("SFace model", message)
                self.assertIn("Repair", message)
                self.assertIn(f"Category: {category}", diagnostic)
                self.assertIn("Source URL: https://media.githubusercontent.com/", diagnostic)

    def test_pointer_size_and_bad_hash_never_enter_verified_cache_or_managed_storage(self):
        for payload in (b"version https://git-lfs.github.com/spec/v1\n",
                        b"X" * len(self.payloads[0])):
            with self.subTest(size=len(payload)):
                with self.assertRaisesRegex(ValueError, "artifact (size|SHA-256) mismatch"):
                    self.fetch(0, opener=lambda request, timeout: Response(payload))
                self.assertFalse((self.cache / "verified/opencv-yunet-2026may" /
                                  self.descriptor(0).version / self.descriptor(0).filename).exists())
                self.assertFalse(self.specs[0].destination.exists())

    def test_publication_failure_does_not_make_model_ready(self):
        acquired = self.fetch(0)
        with patch("install_manager.component_adapters.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                install_resource(self.root, self.resources[0], acquired)
        self.assertTrue(Path(acquired.cache_path).is_file())
        self.assertFalse(self.specs[0].destination.exists())
        with patch.object(managed_resources, "approved_resource_specs", return_value=tuple(self.specs)):
            self.assertFalse(managed_resources.component_resource_status(
                DELIVERY, self.root, "face-analysis")["resource_complete"])

    def test_failure_diagnostics_strip_source_query_parameters(self):
        error = AcquisitionFailure("timeout", "opencv-yunet-2026may", "example.org", 1,
                                   "TimeoutError", url="https://example.org/model?token=private")
        self.assertNotIn("token", str(error))
        self.assertEqual(error.url, "https://example.org/model")


if __name__ == "__main__":
    unittest.main()
