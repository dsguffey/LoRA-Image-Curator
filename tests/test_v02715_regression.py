"""Regressions for the v0.27.15 deterministic Tk shutdown correction.

The first live-Windows v0.27.14 golden run still exposed three late Tk
finalizers after every functional checkpoint passed. Browser thumbnail tasks
retained the complete Tk frame, decoded ``PhotoImage`` objects survived until
garbage collection, and application callbacks formed explicit Python cycles.
These checks keep those ownership boundaries detached during shutdown.
"""

from __future__ import annotations

from pathlib import Path

from app_identity import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return ((Path(__file__).resolve().parent if filename.startswith("test_") else ROOT) / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Require public metadata to identify the shutdown correction."""
    assert tuple(int(part) for part in APP_VERSION.split(".")) >= (0, 27, 15)
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_short_background_tasks_do_not_retain_tk_owners() -> None:
    """Keep folder-count and thumbnail workers limited to plain data/queues."""
    app_source = _source("app.py")
    folder_method = app_source.split(
        "    def _refresh_input_folder_count(self) -> None:", 1
    )[1].split(
        "    def _refresh_provider_device_status(self) -> None:", 1
    )[0]
    folder_worker = folder_method.split(
        "        def count_images() -> None:", 1
    )[1].split(
        "        threading.Thread(", 1
    )[0]
    assert "message_queue = self.message_queue" in folder_method
    assert "message_queue.put(" in folder_worker
    assert "self." not in folder_worker

    browser_source = _source("catalog_browser.py")
    thumbnail_method = browser_source.split(
        "    def _queue_thumbnail(", 1
    )[1].split(
        "    def _process_thumbnail_results(self) -> None:", 1
    )[0]
    thumbnail_worker = thumbnail_method.split(
        "        def worker() -> None:", 1
    )[1].split(
        "        self.thumbnail_executor.submit(worker)", 1
    )[0]
    assert "results_queue = self.thumbnail_results" in thumbnail_method
    assert "results_queue.put(" in thumbnail_worker
    assert "self." not in thumbnail_worker


def test_shutdown_releases_tk_images_and_python_callbacks() -> None:
    """Require main-thread image cleanup and explicit callback detachment."""
    browser = _source("catalog_browser.py")
    browser_shutdown = browser.split(
        "    def shutdown(self) -> None:", 1
    )[1]
    assert "card.photo_image = None" in browser_shutdown
    assert "self.decoded_thumbnail_cache.clear()" in browser_shutdown
    assert "self._destroy_cards()" in browser_shutdown
    assert "self.on_image_sets_changed = None" in browser_shutdown
    assert "self.on_filter_settings_changed = None" in browser_shutdown
    assert "self.on_command_state_changed = None" in browser_shutdown

    readiness = _source("readiness_frame.py")
    readiness_shutdown = readiness.split(
        "    def shutdown(self) -> None:", 1
    )[1].split(
        "    @property\n    def is_running", 1
    )[0]
    assert "self.on_settings_saved = None" in readiness_shutdown
    assert "self.on_quality_running_changed = None" in readiness_shutdown
    assert "self.export_scope = None" in readiness_shutdown


def test_current_release_chains_include_v02715() -> None:
    """Keep this fix in regression, package, and live-Windows gates."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    gui = _source("test_v02715_gui.py")
    current_endpoint = _source("test_v02716_gui.py")

    assert '"tests/test_v02721_gui.py"' in runner
    assert '"tests/test_v02715_regression.py"' in regressions
    assert '"tests/test_v02715_regression.py"' in build
    assert '"tests/test_v02715_gui.py"' in build
    assert "from test_v02714_gui import run as run_v02714" in gui
    assert "from test_v02715_gui import run as run_v02715" in current_endpoint


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_short_background_tasks_do_not_retain_tk_owners()
    test_shutdown_releases_tk_images_and_python_callbacks()
    test_current_release_chains_include_v02715()
    print(
        "v0.27.15 regression tests passed: short workers retain no Tk owners, "
        "browser images are released on the GUI thread, application callbacks "
        "detach during shutdown, and current release gates are synchronized."
    )
