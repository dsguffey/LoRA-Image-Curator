"""M1.8 component catalog, lifecycle, validation, and shell contracts."""
from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from install_manager.component_catalog import (
    CancellationToken, ComponentAction, ComponentFacts, ComponentOperationQueue,
    ComponentPhase, component_action, default_install_root, load_component_catalog,
    product_ready, progress_presentation, validate_existing_selection,
)
from install_manager.manager_ui import (active_launch_contract, component_detail_contract,
                                        component_status_text, open_official_source, picker_contract)


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/install_manager/recipes/lic-components.json"


class ComponentCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = load_component_catalog(CATALOG)
        cls.by_id = {item.component_id: item for item in cls.components}

    def test_catalog_has_one_core_and_four_optional_capabilities(self):
        self.assertEqual(tuple(self.by_id),
                         ("lic-core", "florence-captioning", "face-analysis",
                          "body-analysis", "video-extraction"))
        self.assertEqual([item.component_id for item in self.components if item.required_for_readiness],
                         ["lic-core"])
        self.assertTrue(all(item.provider and item.source_name and item.source_url
                            for item in self.components))

    def test_optional_components_do_not_gate_product_readiness(self):
        facts = {item.component_id: ComponentFacts() for item in self.components}
        facts["lic-core"] = ComponentFacts(ComponentPhase.INSTALLED, verified=True)
        self.assertTrue(product_ready(self.components, facts))
        facts["face-analysis"] = ComponentFacts(ComponentPhase.ERROR)
        self.assertTrue(product_ready(self.components, facts))
        facts["lic-core"] = ComponentFacts(ComponentPhase.PARTIAL, resumable=True)
        self.assertFalse(product_ready(self.components, facts))

    def test_state_driven_actions_never_offer_unapproved_update(self):
        core = self.by_id["lic-core"]
        self.assertEqual(component_action(core, ComponentFacts()), ComponentAction.INSTALL)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.PARTIAL, resumable=True)),
                         ComponentAction.RESUME)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.DOWNLOADING)),
                         ComponentAction.CANCEL)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.QUEUED)),
                         ComponentAction.CANCEL_QUEUE)
        unavailable = ComponentFacts(ComponentPhase.UPDATE_AVAILABLE, verified=True,
                                     approved_update_available=False)
        self.assertEqual(component_action(core, unavailable), ComponentAction.NONE)
        unavailable.approved_update_available = True
        self.assertEqual(component_action(core, unavailable), ComponentAction.UPDATE)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.REPAIR_REQUIRED)),
                         ComponentAction.REPAIR)
        self.assertEqual(component_action(core, ComponentFacts(ComponentPhase.INSTALLED, verified=True)),
                         ComponentAction.CHECK_UPDATES)

    def test_progress_is_indeterminate_only_when_total_is_unknown(self):
        unknown = progress_presentation(ComponentFacts(ComponentPhase.DOWNLOADING))
        known = progress_presentation(ComponentFacts(ComponentPhase.DOWNLOADING,
                                                     completed_bytes=25, total_bytes=100))
        done = progress_presentation(ComponentFacts(ComponentPhase.INSTALLED, verified=True))
        self.assertEqual((unknown["mode"], unknown["active"]), ("indeterminate", True))
        self.assertEqual((known["mode"], known["value"]), ("determinate", 25.0))
        self.assertEqual(done["value"], 100.0)

    def test_details_disclose_unknown_size_and_advanced_identity(self):
        component = self.by_id["video-extraction"]
        details = component_detail_contract(component, ComponentFacts())
        fields = dict(field for _heading, section in details["sections"] for field in section)
        advanced = dict(details["advanced"])
        self.assertEqual(fields["Expected download"], "146.1 MB")
        self.assertIn("License", fields)
        self.assertIn("Identity", advanced)

    def test_existing_provider_picker_contracts_are_explicit(self):
        media = picker_contract(self.by_id["body-analysis"])
        ffmpeg = picker_contract(self.by_id["video-extraction"])
        face = picker_contract(self.by_id["face-analysis"])
        self.assertIn(".task", media["hint"])
        self.assertEqual(media["filetypes"], [])
        self.assertIn("folder", media["title"].casefold())
        self.assertIn("ffmpeg.exe", ffmpeg["hint"])
        self.assertEqual(ffmpeg["filetypes"], [])
        self.assertIn("folder", ffmpeg["title"].casefold())
        self.assertIn("folder", face["hint"])

    def test_managed_face_analysis_uses_install_wording(self):
        text = component_status_text(self.by_id["face-analysis"], ComponentFacts())
        self.assertIn("Not installed", text)
        self.assertNotIn("Automatic installation is not currently available", text)

    def test_official_source_requires_https_and_user_action(self):
        opened = []
        self.assertTrue(open_official_source("https://example.test/", lambda url: (opened.append(url) or True)))
        self.assertEqual(opened, ["https://example.test/"])
        self.assertFalse(open_official_source("http://example.test/", opened.append))

    def test_default_install_root_is_per_user_local_appdata(self):
        self.assertEqual(default_install_root(r"C:\TestProfile\AppData\Local"),
                         Path(r"C:\TestProfile\AppData\Local\LoRA Image Curator"))


class ExistingArtifactValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_id = {item.component_id: item for item in load_component_catalog(CATALOG)}

    def test_exact_mediapipe_task_is_partial_until_packages_exist(self):
        content = b"approved task fixture"
        definition = replace(self.by_id["body-analysis"],
                             identity={"sha256": hashlib.sha256(content).hexdigest()})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pose_landmarker_full.task"
            path.write_bytes(content)
            facts = validate_existing_selection(definition, path)
            self.assertEqual(facts.phase, ComponentPhase.PARTIAL)
            self.assertFalse(facts.verified)
            path.write_bytes(b"wrong")
            self.assertEqual(validate_existing_selection(definition, path).phase,
                             ComponentPhase.INCOMPATIBLE)

    def test_mediapipe_requires_task_extension_and_ffmpeg_requires_exact_basename(self):
        media = self.by_id["body-analysis"]
        video = self.by_id["video-extraction"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_task = root / "model.bin"; wrong_task.write_bytes(b"fixture")
            self.assertEqual(validate_existing_selection(media, wrong_task).phase, ComponentPhase.INCOMPATIBLE)
            other = root / "other.exe"; other.write_bytes(b"fixture")
            self.assertEqual(validate_existing_selection(video, other).phase, ComponentPhase.INCOMPATIBLE)

    def test_face_analysis_requires_the_exact_yunet_sface_pair(self):
        definition = self.by_id["face-analysis"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "face"
            invalid.mkdir()
            self.assertEqual(validate_existing_selection(definition, invalid).phase,
                             ComponentPhase.INCOMPATIBLE)
            valid = root / "models" / "face"
            valid.mkdir(parents=True)
            (valid / "det.onnx").write_bytes(b"fixture")
            facts = validate_existing_selection(definition, valid)
            self.assertEqual(facts.phase, ComponentPhase.INCOMPATIBLE)
            self.assertEqual(component_action(definition, facts), ComponentAction.REPAIR)

    def test_ffmpeg_probe_is_bounded_and_must_identify_itself(self):
        definition = self.by_id["video-extraction"]
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ffmpeg.exe"
            executable.write_bytes(b"fixture")
            calls = []
            def valid(command, **kwargs):
                calls.append((command, kwargs))
                return SimpleNamespace(returncode=0, stdout="ffmpeg version 8.0", stderr="")
            facts = validate_existing_selection(definition, executable, runner=valid)
            self.assertTrue(facts.verified)
            self.assertEqual(calls[0][0], [str(executable.resolve()), "-version"])
            self.assertEqual(calls[0][1]["timeout"], 30)
            invalid = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="other", stderr="")
            self.assertEqual(validate_existing_selection(definition, executable, runner=invalid).phase,
                             ComponentPhase.INCOMPATIBLE)


class OperationAndLaunchTests(unittest.TestCase):
    def test_queue_allows_one_active_operation_and_cancelable_fifo_waiters(self):
        queue = ComponentOperationQueue()
        core, started = queue.submit("lic-core", "Install")
        _face, face_started = queue.submit("face-analysis", "Install")
        self.assertTrue(started)
        self.assertFalse(face_started)
        self.assertIs(queue.active, core)
        self.assertEqual(queue.cancel("face-analysis"), "queue-canceled")
        body, body_started = queue.submit("body-analysis", "Install")
        self.assertFalse(body_started)
        self.assertEqual(queue.cancel("lic-core"), "canceling")
        self.assertTrue(core.token.requested())
        self.assertIs(queue.complete("lic-core"), body)

    def test_cancellation_token_is_monotonic(self):
        token = CancellationToken()
        self.assertFalse(token.requested())
        token.request()
        self.assertTrue(token.requested())

    def test_launch_requires_matching_active_record_and_real_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, python, manager = root / "app", root / "venv/python.exe", root / "manager.exe"
            app.mkdir(); python.parent.mkdir()
            (app / "app.py").write_text("pass")
            python.write_bytes(b"python"); manager.write_bytes(b"manager")
            record = {"state": "active", "root": str(root), "application": str(app),
                      "python": str(python), "manager": str(manager)}
            self.assertTrue(active_launch_contract(record, root))
            self.assertFalse(active_launch_contract(dict(record, state="testing-ready"), root))
            self.assertFalse(active_launch_contract(record, root / "other"))
            manager.unlink()
            self.assertFalse(active_launch_contract(record, root))


if __name__ == "__main__":
    unittest.main()
