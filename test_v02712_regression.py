"""Regressions for the v0.27.12 redraw and golden-verdict correction.

v0.27.11 cancelled the timer it still knew about when the enlarged viewer
closed. A synchronous zoom/fit redraw could nevertheless clear that stored ID
while the Tcl timer remained queued. This suite locks down separate scheduled
and immediate redraw paths and proves that the parent golden runner rejects
stderr diagnostics even when a GUI child exits successfully.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile

from pathlib import Path

from app_identity import APP_VERSION
from test_golden_build import _run_gui_gate


ROOT = Path(__file__).parent


def _source(filename: str) -> str:
    """Read one current project file for release-chain assertions."""
    return (ROOT / filename).read_text(encoding="utf-8")


def test_current_version_is_consistent() -> None:
    """Keep public metadata synchronized after the v0.27.12 correction."""
    assert f"Version {APP_VERSION}" in _source("VERSION.txt")
    assert f'version = "{APP_VERSION}"' in _source("pyproject.toml")
    assert f"v{APP_VERSION}" in _source("README.md")
    assert f"v{APP_VERSION}" in _source("README.txt")


def test_immediate_redraw_cannot_disown_a_pending_timer() -> None:
    """Keep callback ownership separate from the image-rendering operation."""
    viewer = _source("image_review_dialog.py")
    assert "def _cancel_pending_redraw(self) -> None:" in viewer
    assert "def _run_scheduled_redraw(self) -> None:" in viewer
    assert "def _redraw_now(self) -> None:" in viewer
    assert "def _render(self) -> None:" in viewer
    assert "self._cancel_pending_redraw()" in viewer
    assert "self._redraw_after_id = None" in viewer
    assert "self._redraw_now()" in viewer
    assert "self._redraw(" not in viewer


def test_gui_gate_rejects_successful_process_with_stderr() -> None:
    """Do not declare a GUI child clean merely because its exit code is zero."""
    with tempfile.TemporaryDirectory(prefix="v02712_gui_gate_") as temporary:
        script = Path(temporary) / "successful_but_noisy.py"
        script.write_text(
            "import sys\n"
            "print('invalid command name synthetic_redraw', file=sys.stderr)\n",
            encoding="utf-8",
        )
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            try:
                _run_gui_gate(
                    [sys.executable, str(script)],
                    environment=os.environ.copy(),
                )
            except RuntimeError as error:
                assert "emitted stderr diagnostics" in str(error)
            else:
                raise AssertionError("A noisy GUI child must fail the golden gate.")
        assert "invalid command name synthetic_redraw" in captured.getvalue()


def test_current_release_chains_include_v02712() -> None:
    """Keep this correction in regression, package, and Windows GUI gates."""
    runner = _source("test_golden_build.py")
    regressions = _source("tools/run_regressions.py")
    build = _source("tools/build_release.py")
    v02713_gui = _source("test_v02713_gui.py")
    current_gui = _source("test_v02714_gui.py")
    next_gui = _source("test_v02715_gui.py")
    current_endpoint = _source("test_v02716_gui.py")

    assert '"test_v02719_gui.py"' in runner
    assert "_run_gui_gate(" in runner
    assert '"test_v02712_regression.py"' in regressions
    assert '"test_v02712_regression.py"' in build
    assert '"test_v02712_gui.py"' in build
    assert "from test_v02712_gui import run as run_v02712" in v02713_gui
    assert "from test_v02713_gui import run as run_v02713" in current_gui
    assert "from test_v02714_gui import run as run_v02714" in next_gui
    assert "from test_v02715_gui import run as run_v02715" in current_endpoint


if __name__ == "__main__":
    test_current_version_is_consistent()
    test_immediate_redraw_cannot_disown_a_pending_timer()
    test_gui_gate_rejects_successful_process_with_stderr()
    test_current_release_chains_include_v02712()
    print(
        "v0.27.12 regression tests passed: synchronized version metadata, "
        "separate redraw scheduling/rendering, strict GUI stderr handling, "
        "and current release gates."
    )
