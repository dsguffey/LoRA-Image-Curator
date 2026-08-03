"""NVIDIA runtime-repair contracts for v0.27.23.

These tests remain dependency-free. They verify setup ordering, official wheel
identity, driver gating, real CUDA-smoke intent, optional face-stack ownership,
and release synchronization without installing packages or requiring a GPU.
"""

from __future__ import annotations

import inspect

from pathlib import Path

from app_identity import APP_VERSION
from setup_assistant import (
    MINIMUM_CUDA_13_WINDOWS_DRIVER,
    TESTED_NVIDIA_CUDA_VERSION,
    TESTED_NVIDIA_INDEX_URL,
    TESTED_NVIDIA_TORCH_VERSION,
    TESTED_NVIDIA_TORCHVISION_VERSION,
    _driver_version_key,
    install_required_packages,
    install_tested_nvidia_pytorch,
)


ROOT = Path(__file__).resolve().parents[1]


def _project(relative_name: str) -> str:
    """Read one UTF-8 project member for cross-file assertions."""
    return (ROOT / relative_name).read_text(encoding="utf-8")


def test_release_identity_and_reviewed_runtime_pair_are_synchronized() -> None:
    """Keep the narrow repair version and official wheel pair explicit."""
    assert APP_VERSION == "0.27.23"
    assert "Version 0.27.23" in _project("VERSION.txt")
    assert 'version = "0.27.23"' in _project("pyproject.toml")
    assert "v0.27.23" in _project("README.md")
    assert TESTED_NVIDIA_TORCH_VERSION == "2.13.0"
    assert TESTED_NVIDIA_TORCHVISION_VERSION == "0.28.0"
    assert TESTED_NVIDIA_CUDA_VERSION == "13.0"
    assert TESTED_NVIDIA_INDEX_URL == "https://download.pytorch.org/whl/cu130"
    assert MINIMUM_CUDA_13_WINDOWS_DRIVER == (580, 88)


def test_pytorch_is_selected_before_timm_can_resolve_cpu_torch() -> None:
    """Prevent recurrence of the clean-install dependency-order bug."""
    source = inspect.getsource(install_required_packages)
    menu_call = source.index("pytorch_install_menu()")
    requirements_install = source.index('"requirements.txt"')
    assert menu_call < requirements_install
    requirements = _project("requirements.txt")
    assert "timm depends on those packages" in requirements
    assert requirements.index("PyTorch/Torchvision") < requirements.index("timm>=")


def test_nvidia_repair_fails_before_pip_on_old_drivers_and_smokes_cuda() -> None:
    """Require the safety gates around the large focused wheel replacement."""
    assert _driver_version_key("580.88") >= MINIMUM_CUDA_13_WINDOWS_DRIVER
    assert _driver_version_key("579.99") < MINIMUM_CUDA_13_WINDOWS_DRIVER
    source = inspect.getsource(install_tested_nvidia_pytorch)
    assert source.index("MINIMUM_CUDA_13_WINDOWS_DRIVER") < source.index(
        'save_dependency_snapshot("nvidia_pytorch_repair")'
    )
    assert source.index('save_dependency_snapshot("nvidia_pytorch_repair")') < (
        source.index("_pip_install(")
    )
    assert '"--force-reinstall"' in source
    assert 'runtime.get("smoke_ok")' in source
    assert "face_stack_present" in source
    assert "install_face_dependencies.py" in source
    assert "Optional face-analysis packages are not installed" in source


def test_setup_status_uses_a_real_synchronized_tensor_probe() -> None:
    """Do not equate CUDA discovery alone with usable Florence inference."""
    source = _project("setup_assistant.py")
    assert 'torch.tensor([6.0], device="cuda") * 7.0' in source
    assert "torch.cuda.synchronize()" in source
    assert '"smoke_ok": smoke_ok' in source
    assert "CUDA tensor check" in source


def test_release_gates_include_the_runtime_repair_endpoint() -> None:
    """Keep hosted checks, deterministic packaging, and GUI replay aligned."""
    regressions = _project("tools/run_regressions.py")
    builder = _project("tools/build_release.py")
    workflow = _project(".github/workflows/repository-checks.yml")
    golden = _project("tests/test_golden_build.py")
    assert '"tests/test_v02723_regression.py"' in regressions
    assert '"tests/test_v02723_regression.py"' in builder
    assert '"tests/test_v02723_gui.py"' in builder
    assert "tests.test_v02723_regression" in workflow
    assert 'GUI_ENTRYPOINT = "tests/test_v02723_gui.py"' in golden


if __name__ == "__main__":
    test_release_identity_and_reviewed_runtime_pair_are_synchronized()
    test_pytorch_is_selected_before_timm_can_resolve_cpu_torch()
    test_nvidia_repair_fails_before_pip_on_old_drivers_and_smokes_cuda()
    test_setup_status_uses_a_real_synchronized_tensor_probe()
    test_release_gates_include_the_runtime_repair_endpoint()
    print(
        "v0.27.23 regression tests passed: PyTorch selection precedes timm, "
        "the reviewed CUDA 13 repair is gated and verified, and release "
        "endpoints are synchronized."
    )
