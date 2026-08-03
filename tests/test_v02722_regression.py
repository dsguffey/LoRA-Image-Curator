"""Large-catalog Florence recovery contracts for v0.27.22."""

from __future__ import annotations

import sys

from pathlib import Path
from types import ModuleType

from app_identity import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "florence-community/Florence-2-large-ft"
MODEL_REVISION = "26b734a54fdfbf9c398351eedfabb7f27fc470b7"
TRANSFORMERS_VERSION = "4.56.2"


def _project(relative_name: str) -> str:
    """Read one current release file for a dependency-free contract check."""
    return (PROJECT_ROOT / relative_name).read_text(encoding="utf-8")


def _load_florence_module():
    """Import provider helpers without installing the heavyweight runtimes."""
    if "torch" not in sys.modules:
        sys.modules["torch"] = ModuleType("torch")
    if "transformers" not in sys.modules:
        transformers_stub = ModuleType("transformers")
        transformers_stub.AutoModelForImageTextToText = object
        transformers_stub.AutoProcessor = object
        sys.modules["transformers"] = transformers_stub
    import florence_analyzer

    return florence_analyzer


def test_release_identity_and_checkpoint_are_synchronized() -> None:
    """Require the corrected native checkpoint throughout the release."""
    assert APP_VERSION == "0.27.23"
    assert "Version 0.27.23" in _project("VERSION.txt")
    assert 'version = "0.27.23"' in _project("pyproject.toml")
    source = _project("florence_analyzer.py")
    assert f'MODEL_NAME = "{MODEL_NAME}"' in source
    assert f'MODEL_REVISION = "{MODEL_REVISION}"' in source
    assert "AutoModelForImageTextToText.from_pretrained" in source
    assert source.count("trust_remote_code=False") == 2
    assert "use_safetensors=True" in source


def test_exact_legacy_identities_resume_before_new_inference() -> None:
    """Preserve reviewed 4.49/v0.27.21 results without broad wildcard reuse."""
    florence = _load_florence_module()

    class RecordingCatalog:
        def __init__(self, successful_identity):
            self.successful_identity = successful_identity
            self.calls = []

        def get_reusable_analysis(self, **arguments):
            identity = (
                arguments["model_name"],
                arguments["transformers_version"],
                arguments["analysis_version"],
            )
            self.calls.append((identity, arguments["requested_triage"]))
            if identity == self.successful_identity:
                return {"caption": "stored and reusable"}
            return None

    legacy_449 = ("microsoft/Florence-2-large-ft", "4.49.0", 1)
    catalog = RecordingCatalog(legacy_449)
    result = florence.get_reusable_florence_analysis(
        catalog,
        image_id=8079,
        requested_triage=True,
    )
    assert result == {"caption": "stored and reusable"}
    assert catalog.calls == [
        ((MODEL_NAME, TRANSFORMERS_VERSION, 1), True),
        (("microsoft/Florence-2-large-ft", "4.56.2", 1), True),
        (legacy_449, True),
    ]

    current = (MODEL_NAME, TRANSFORMERS_VERSION, 1)
    current_catalog = RecordingCatalog(current)
    assert florence.get_reusable_florence_analysis(
        current_catalog,
        image_id=1,
        requested_triage=False,
    ) is not None
    assert current_catalog.calls == [(current, False)]


def test_preflight_covers_every_live_task_before_catalog_inference() -> None:
    """Guard the prompt/tokenizer path that v0.27.21 failed to exercise."""
    source = _project("florence_analyzer.py")
    assert "processor.tokenizer.image_token" in source
    assert "processor.tokenizer.image_token_id" in source
    assert "for task_prompt in TASK_MAX_NEW_TOKENS:" in source
    assert "max_new_tokens=1" in source
    assert "prepared_inputs[OBJECT_DETECTION_TASK]" in source
    assert source.index("                preflight_florence_tasks(") < source.index(
        "with partial_csv.open("
    )
    assert "Existing stored results " in source
    assert "remain intact." in source


def test_release_gates_include_the_recovery_endpoint() -> None:
    """Keep tests, deterministic packaging, hosted checks, and GUI replay aligned."""
    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    workflow = _project(".github/workflows/repository-checks.yml")
    golden = _project("tests/test_golden_build.py")
    assert '"tests/test_v02722_regression.py"' in regressions
    assert '"tests/test_v02722_regression.py"' in builder
    assert '"tests/test_v02722_gui.py"' in builder
    assert "tests.test_v02723_regression" in workflow
    assert 'GUI_ENTRYPOINT = "tests/test_v02723_gui.py"' in golden


if __name__ == "__main__":
    test_release_identity_and_checkpoint_are_synchronized()
    test_exact_legacy_identities_resume_before_new_inference()
    test_preflight_covers_every_live_task_before_catalog_inference()
    test_release_gates_include_the_recovery_endpoint()
    print(
        "v0.27.22 regression tests passed: Florence uses the corrected native "
        "checkpoint, fails fast, and resumes exact reviewed legacy results."
    )
