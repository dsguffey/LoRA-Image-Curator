import hashlib
import json
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from install_manager.compatibility_profiles import (
    dependency_profile_for_components, load_approved_profiles, recommended_profile)
from install_manager.component_adapters import install_resource, launch_environment, move_resource
from install_manager.component_operations import _resource_state, inspect_recovery, operation_plan
from install_manager.component_state import (
    ComponentInventory, InstalledComponent, ResourceState, empty_inventory,
    load_inventory, project_legacy_inventory, replace_component, write_inventory)
from install_manager.artifacts import AcquiredArtifact, ArtifactDescriptor
from install_manager.journal import OperationJournal
from install_manager.bootstrap_layout import layout
from install_manager.managed_move import move_installation


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "src/install_manager"
PROFILE_ROOT = DELIVERY / "recipes/compatibility/profiles"


def resource(resource_id="resource", ownership="manager-owned", disposition="copy-and-verify",
             local_path=r"C:\Fixture\resource.bin", adapter="sha256-file-v1"):
    return ResourceState(resource_id, "model", adapter, local_path, ownership, disposition,
                         {"sha256": "a" * 64}, {"kind": "fixture"},
                         {"version": "fixture-v1", "passed": True})


def component(component_id="future-component", provider="future-provider", resources=()):
    return InstalledComponent("future-capability", provider, component_id,
                              "future-implementation-v1", "1.0", "installed", True,
                              {"version": "fixture-v1", "passed": True}, tuple(resources))


class GenericStateTests(unittest.TestCase):
    def test_profile_component_inventory_round_trip_all_ownership_classes(self):
        profile = recommended_profile(PROFILE_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = (
                resource("managed", "manager-owned", "copy-and-verify"),
                resource("external", "user-supplied/external", "preserve-reference"),
                resource("shared", "shared", "preserve-reference"),
                resource("legacy", "unknown/legacy", "preserve-in-place"),
            )
            inventory = ComponentInventory(
                1, {"profile_id": profile.profile_id, "digest": profile.digest},
                root.resolve().as_posix(), (component(resources=values),),
                {"source": "test"})
            write_inventory(root, inventory)
            self.assertEqual(load_inventory(root), inventory)

    def test_unknown_future_provider_needs_no_state_schema_change(self):
        value = component(provider="provider-never-seen-before")
        encoded = value.resources
        self.assertEqual(value.provider_id, "provider-never-seen-before")
        self.assertEqual(encoded, ())

    def test_legacy_projection_is_read_only_and_marks_unknown_model_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "State/components/florence.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(json.dumps({"state": "installed", "snapshot": str(root / "shared")}),
                              encoding="utf-8")
            active = {"application": str(root / "Application/extracted"),
                      "python": str(root / "venv/Scripts/python.exe"),
                      "channel": {"schema_version": 2}}
            before = legacy.read_bytes()
            inventory = project_legacy_inventory(DELIVERY, root, active)
            self.assertFalse((root / "State/components/inventory.json").exists())
            self.assertEqual(legacy.read_bytes(), before)
            florence = inventory.get("florence-captioning")
            self.assertEqual(florence.resources[0].ownership, "unknown/legacy")
            self.assertEqual(florence.resources[0].disposition, "preserve-in-place")


class ProfileAndArtifactTests(unittest.TestCase):
    def test_historical_profile_is_retained_and_new_profile_is_recommended(self):
        profiles = load_approved_profiles(PROFILE_ROOT)
        self.assertEqual([item.profile_id for item in profiles], ["2026-09-06", "2026-09-07", "2026-09-10"])
        self.assertEqual(profiles[0].digest,
                         "9f895d3706a3a7d5a5a02be8bc549c05b8d5c892520de8976446bd6f7e5fce96")
        self.assertEqual(profiles[1].digest,
                         "2f6af3aa5186cb8db82dc7ec59059bf3130fc1173e442e92a7939bd4e157d58a")
        self.assertEqual(profiles[2].digest,
                         "ac2da1f0e9a7d8cd606e72adfdcdb2e22abb86a5b9eb3b1815807edb80e2e443")

    def test_mediapipe_resolution_records_authoritative_and_equivalent_hashes(self):
        manifest = recommended_profile(PROFILE_ROOT).component_by_id("body-analysis").raw
        model = manifest["resources"][0]
        self.assertEqual(model["artifact"]["expected_sha256"],
                         "5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1")
        self.assertEqual(model["artifact"]["expected_size"], 9_398_198)
        self.assertEqual(model["validation"]["accepted_equivalent_sha256s"],
                         ["4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad"])
        self.assertEqual(len(model["validation"]["inner_sha256"]), 2)

    def test_ffmpeg_is_exact_pinned_lgpl_upstream_acquisition(self):
        manifest = recommended_profile(PROFILE_ROOT).component_by_id("video-extraction").raw
        tool = manifest["resources"][0]
        artifact = tool["artifact"]
        self.assertEqual(artifact["version"], "n8.1.2-50-g1a748fe2cd-20260905")
        self.assertEqual(artifact["expected_size"], 146_078_600)
        self.assertEqual(artifact["expected_sha256"],
                         "a86187b579310debe4241b4b4f4cefdac3ca294cfde76f775993445d19cf5b1d")
        self.assertTrue(artifact["url"].startswith("https://github.com/BtbN/FFmpeg-Builds/releases/"))
        self.assertEqual(artifact["license_id"], "LGPL-3.0-or-later")
        self.assertIn("--enable-gpl", tool["validation"]["forbidden_configuration"])

    def test_exact_component_composition_is_not_a_solver(self):
        profile = recommended_profile(PROFILE_ROOT)
        lock = dependency_profile_for_components(profile, {"lic-core", "body-analysis"})
        self.assertIn("mediapipe", lock.expected_inventory)
        self.assertNotIn("torch", lock.expected_inventory)
        with self.assertRaises(ValueError):
            dependency_profile_for_components(profile, {"body-analysis"})

    def test_representative_move_component_sets_resolve_exactly(self):
        profile = recommended_profile(PROFILE_ROOT)
        cases = (
            {"lic-core"},
            {"lic-core", "florence-captioning"},
            {"lic-core", "body-analysis"},
            {"lic-core", "video-extraction"},
            {item.component_id for item in profile.components.values()},
        )
        sizes = [len(dependency_profile_for_components(profile, selected).expected_inventory)
                 for selected in cases]
        self.assertEqual(sizes[0], 7)
        self.assertEqual(sizes[3], 7)
        self.assertEqual(sizes[-1], 57)


class AdapterAndTransactionTests(unittest.TestCase):
    def _active_root(self, base: Path):
        root = base / "LIC"
        active = root / "State/installations/lic-lite.json"
        active.parent.mkdir(parents=True)
        active.write_text(json.dumps({"state": "active", "root": root.resolve().as_posix()}),
                          encoding="utf-8")
        profile = recommended_profile(PROFILE_ROOT)
        core_manifest = profile.component_by_id("lic-core")
        core = InstalledComponent("core-functionality", core_manifest.provider_id,
                                  core_manifest.component_id, core_manifest.manifest_id,
                                  core_manifest.native_version, "installed", True,
                                  {"version": "core-v1", "passed": True}, ())
        write_inventory(root, replace_component(empty_inventory(root, profile), core))
        return root

    def test_passive_plan_never_acquires_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._active_root(Path(directory))
            inventory_bytes = (root / "State/components/inventory.json").read_bytes()
            with patch("install_manager.component_operations.acquire_artifact",
                       side_effect=AssertionError("passive plan acquired")):
                plan = operation_plan(DELIVERY, root, "body-analysis")
            self.assertEqual(plan["component"]["provider_id"], "google-mediapipe")
            self.assertEqual((root / "State/components/inventory.json").read_bytes(), inventory_bytes)
            self.assertFalse((root / "State/operations/component-body-analysis.json").exists())

    def test_recovery_accepts_exact_plan_and_rejects_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._active_root(Path(directory))
            plan = operation_plan(DELIVERY, root, "body-analysis")
            journal = OperationJournal.create(root / "State/operations", "component-body-analysis",
                                              target_path=root,
                                              plan_digest=hashlib.sha256(json.dumps(
                                                  plan, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=False).encode()).hexdigest(),
                                              artifacts=[plan],
                                              steps=("verify_core", "acquire_dependencies",
                                                     "install_dependencies", "acquire_resource",
                                                     "install_resource", "validate", "publish"))
            journal.set_status("cancelled")
            self.assertTrue(inspect_recovery(DELIVERY, root, "body-analysis").resumable)
            data = json.loads(journal.path.read_text(encoding="utf-8"))
            data["plan_digest"] = "0" * 64
            journal.path.write_text(json.dumps(data), encoding="utf-8")
            blocked = inspect_recovery(DELIVERY, root, "body-analysis")
            self.assertTrue(blocked.blocked)
            self.assertFalse(blocked.resumable)

    def test_zip_adapter_installs_only_allowlisted_verified_members(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "tool.zip"
            payload = b"binary"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("bin/ffmpeg.exe", payload)
                output.writestr("unlisted.txt", b"ignore")
            archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
            member_hash = hashlib.sha256(payload).hexdigest()
            descriptor = ArtifactDescriptor("fixture", "1", archive.name, "https://github.com/x/y",
                                            archive_hash, "fixture", "fixture", "MIT",
                                            archive.stat().st_size)
            acquired = AcquiredArtifact(descriptor, str(archive), archive_hash,
                                        archive.stat().st_size, "verified", True, True, 0)
            manifest = {"artifact": descriptor.as_dict(),
                        "installation": {"adapter": "verified-zip-members-v1",
                                         "relative_path": "Resources/tool",
                                         "members": [{"source": "bin/ffmpeg.exe",
                                                      "destination": "ffmpeg.exe",
                                                      "size": len(payload), "sha256": member_hash}]}}
            result = install_resource(base / "root", manifest, acquired)
            self.assertEqual(Path(result["path"]).joinpath("ffmpeg.exe").read_bytes(), payload)
            self.assertFalse(Path(result["path"]).joinpath("unlisted.txt").exists())

    def test_move_ownership_preserves_external_and_copies_manager_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / "source"; destination = base / "destination"
            managed_path = source / "Resources/item.bin"
            managed_path.parent.mkdir(parents=True); managed_path.write_bytes(b"managed")
            managed = ResourceState("item", "tool", "sha256-file-v1", str(managed_path),
                                    "manager-owned", "copy-and-verify",
                                    {"sha256": hashlib.sha256(b"managed").hexdigest()},
                                    {"kind": "fixture"}, {"version": "v1", "passed": True})
            external_path = base / "external.bin"; external_path.write_bytes(b"external")
            external = ResourceState("external", "tool", "sha256-file-v1", str(external_path),
                                     "user-supplied/external", "preserve-reference", {},
                                     {"kind": "user-selected"}, {"version": "v1", "passed": True})
            manifest = SimpleNamespace(raw={})
            moved = move_resource(DELIVERY, manifest, managed, source, destination, base / "models",
                                  copy_shared_models=False)
            preserved = move_resource(DELIVERY, manifest, external, source, destination, base / "models",
                                      copy_shared_models=False)
            self.assertEqual(Path(moved.local_path).read_bytes(), b"managed")
            self.assertEqual(preserved.local_path, str(external_path))
            self.assertEqual(preserved.ownership, "user-supplied/external")

    def test_move_preserves_shared_and_unknown_resources_without_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / "source"; destination = base / "destination"
            shared_path = base / "shared-model"; shared_path.mkdir()
            legacy_path = base / "legacy-model"; legacy_path.mkdir()
            shared = resource("shared", "shared", "preserve-reference",
                              str(shared_path), "future-model-adapter-v1")
            legacy = resource("legacy", "unknown/legacy", "preserve-in-place",
                              str(legacy_path), "unknown-legacy-v1")
            manifest = SimpleNamespace(raw={})
            self.assertEqual(move_resource(DELIVERY, manifest, shared, source, destination,
                                           base / "models", copy_shared_models=False), shared)
            self.assertEqual(move_resource(DELIVERY, manifest, legacy, source, destination,
                                           base / "models", copy_shared_models=True), legacy)

    def test_managed_or_external_ffmpeg_is_visible_only_to_child_launch(self):
        managed = resource("ffmpeg", "manager-owned", "copy-and-verify",
                           r"C:\Managed\FFmpeg", "ffmpeg-lic-video-v1")
        external = resource("external-ffmpeg", "user-supplied/external", "preserve-reference",
                            r"D:\Tools\ffmpeg.exe", "ffmpeg-executable")
        self.assertEqual(launch_environment((managed,))["INSTALL_MANAGER_PATH_PREPEND"],
                         str(Path(managed.local_path).resolve()))
        self.assertEqual(launch_environment((external,))["INSTALL_MANAGER_PATH_PREPEND"],
                         str(Path(external.local_path).resolve().parent))

    def test_explicit_external_reuse_keeps_the_catalog_adapter_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "ffmpeg.exe"
            selected.write_bytes(b"external-ffmpeg")
            resource_manifest = {
                "resource_id": "ffmpeg", "kind": "tool-directory",
                "artifact": {
                    "artifact_id": "managed-ffmpeg", "version": "1",
                    "filename": "ffmpeg.zip", "url": "https://example.test/ffmpeg.zip",
                    "expected_sha256": "0" * 64, "publisher": "fixture",
                    "source_name": "fixture", "license_id": "LGPL-3.0-or-later",
                    "expected_size": 1,
                },
                "validation": {"adapter": "ffmpeg-lic-video-v1", "version": "v1"},
            }
            state = _resource_state(None, resource_manifest, selected,
                                    {"version": "v1", "passed": True}, external=True,
                                    external_adapter="ffmpeg-executable")
            self.assertEqual(state.adapter_id, "ffmpeg-executable")
            self.assertEqual(state.kind, "external-resource")
            self.assertEqual(launch_environment((state,))["INSTALL_MANAGER_PATH_PREPEND"],
                             str(selected.resolve().parent))

    def test_inventory_driven_move_rebuilds_selected_profile_and_publishes_last(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / "source"; destination = base / "destination"
            places = layout(source)
            for name in ("runtime", "cache", "application"):
                places[name].mkdir(parents=True)
            (places["runtime"] / "python.exe").write_bytes(b"runtime")
            application = places["application"] / "extracted"
            application.mkdir(); (application / "app.py").write_text("pass", encoding="utf-8")
            manager = source / "Manager/current"; manager.mkdir(parents=True)
            (manager / "LIC Install Manager.exe").write_bytes(b"manager")
            (places["state"] / "User").mkdir(parents=True)
            record = {"state": "active", "root": source.resolve().as_posix(),
                      "application": str(application),
                      "python": str(places["venv"] / "Scripts/python.exe"),
                      "manager": str(manager / "LIC Install Manager.exe"),
                      "model_root": str(places["models"]), "channel": {"release_id": "fixture"},
                      "choices": {"start_menu": True, "desktop": False}, "shortcuts": {}}
            record_path = source / "State/installations/lic-lite.json"
            record_path.parent.mkdir(parents=True); record_path.write_text(json.dumps(record), encoding="utf-8")
            profile = recommended_profile(PROFILE_ROOT)
            core_manifest = profile.component_by_id("lic-core")
            resources = (
                ResourceState("application", "application", "managed-tree-v1", str(application),
                              "manager-owned", "copy-and-verify", {}, {"kind": "fixture"},
                              {"version": "v1", "passed": True}),
                ResourceState("python-environment", "python-environment", "approved-profile-venv-v1",
                              record["python"], "manager-owned", "rebuild", {}, {"kind": "fixture"},
                              {"version": "v1", "passed": True}),
            )
            core = InstalledComponent("core-functionality", core_manifest.provider_id,
                                      core_manifest.component_id, core_manifest.manifest_id,
                                      core_manifest.native_version, "installed", True,
                                      {"version": "v1", "passed": True}, resources)
            write_inventory(source, replace_component(empty_inventory(source, profile), core))

            def create_venv(_runtime, target, **_kwargs):
                python = target / "Scripts/python.exe"
                python.parent.mkdir(parents=True); python.write_bytes(b"venv")
                return python

            links = {"start_menu_app": base / "links/app.lnk",
                     "start_menu_manager": base / "links/manager.lnk",
                     "desktop": base / "links/desktop.lnk"}

            def shortcut(path, target, arguments):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"link")
                return {"path": str(path), "target": str(target), "arguments": arguments, "owned": True}

            with patch("install_manager.managed_move.validate_root", side_effect=lambda root, **_: root), \
                 patch("install_manager.managed_move.reject_reparse_entries"), \
                 patch("install_manager.managed_move.process_lock", side_effect=lambda _: nullcontext()), \
                 patch("install_manager.managed_move._locked_artifacts", return_value=()), \
                 patch("install_manager.managed_move.create_final_path_venv", side_effect=create_venv), \
                 patch("install_manager.managed_move.install_locked_wheels"), \
                 patch("install_manager.managed_move._validate_destination", return_value={"passed": True}), \
                 patch("install_manager.managed_move.shortcut_locations", return_value=links), \
                 patch("install_manager.managed_move._shortcut", side_effect=shortcut):
                moved = move_installation(DELIVERY, source, destination)
            inventory = load_inventory(destination)
            self.assertEqual(Path(moved["root"]), destination.resolve())
            self.assertEqual(inventory.selected_profile["profile_id"], "2026-09-10")
            self.assertEqual(inventory.installed_component_ids, {"lic-core"})
            self.assertTrue(moved["move"]["component_inventory_driven"])
            self.assertTrue(record_path.is_file())


if __name__ == "__main__":
    unittest.main()
