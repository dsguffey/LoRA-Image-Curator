"""Regressions for the v0.27.13 startup-worker ownership correction.

The v0.27.12 Windows gate correctly rejected three late Tk Variable finalizers.
Provider-device inspection had already stopped reading Tk variables from its
worker, but the worker still retained the complete ``DatasetToolsApp`` merely
to reach its thread-safe message queue. Slow PyTorch or ONNX imports could
therefore outlive GUI teardown and release the application on the worker
thread. These checks make the narrower ownership boundary explicit.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Require every public current-version marker to agree after v0.27.13."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 13)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_provider_device_worker_does_not_retain_the_application() -> None:
    """Keep the slow startup worker independent of Tk/application ownership."""
    app_source = _source("app.py")
    method_source = app_source.split(
        "    def _refresh_provider_device_status(self) -> None:", 1
    )[1].split(
        "    def _build_body_provider(self, parent: ttk.Frame) -> None:", 1
    )[0]
    worker_source = method_source.split(
        "        def inspect_devices() -> None:", 1
    )[1].split(
        "        threading.Thread(", 1
    )[0]

    assert "message_queue = self.message_queue" in method_source
    assert 'message_queue.put(("provider_devices", devices))' in worker_source
    assert "message_queue.put(" in worker_source
    assert "self." not in worker_source
    assert ".get()" not in worker_source


def test_current_release_chains_include_v02713() -> None:
    """Keep this correction in regression, package, and Windows GUI gates."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    gui = _source("test_v02713_gui.py")
    current_gui = _source("test_v02714_gui.py")
    next_gui = _source("test_v02715_gui.py")
    current_endpoint = _source("test_v02716_gui.py")

    assert '"tests/test_v0284_gui.py"' in runner
    assert '"tests/test_v02713_regression.py"' in regressions
    assert '"tests/test_v02713_regression.py"' in build
    assert '"tests/test_v02713_gui.py"' in build
    assert "from test_v02712_gui import run as run_v02712" in gui
    assert "from test_v02713_gui import run as run_v02713" in current_gui
    assert "from test_v02714_gui import run as run_v02714" in next_gui
    assert "from test_v02715_gui import run as run_v02715" in current_endpoint


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_provider_device_worker_does_not_retain_the_application()
    test_current_release_chains_include_v02713()
    print(
        "v0.27.13 regression tests passed: synchronized version metadata, "
        "startup device-worker ownership bounded to plain values and a "
        "thread-safe queue, and current release gates."
    )
