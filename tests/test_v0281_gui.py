"""Current cumulative Windows GUI smoke entry point for v0.28.1."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk

from types import SimpleNamespace
from unittest.mock import patch

from test_v0280_gui import run as run_v0280


def run() -> None:
    """Replay the established GUI after explicit provider setup changes."""
    run_v0280()
    with tempfile.TemporaryDirectory(prefix="lora_v0281_gui_") as temporary:
        with patch.dict(os.environ, {"APPDATA": temporary}):
            from app import DatasetToolsApp

            root = tk.Tk()
            root.withdraw()
            application = DatasetToolsApp(root)
            labels = {
                application.tools_menu.entrycget(index, "label")
                for index in range(int(application.tools_menu.index("end")) + 1)
            }
            assert "Open Setup & Repair…" in labels
            with (
                patch(
                    "app.inspect_florence_cache",
                    return_value=SimpleNamespace(
                        model_ready=False,
                        cache_root=os.path.join(temporary, "huggingface", "hub"),
                    ),
                ),
                patch("app.messagebox.askyesno", return_value=False) as prompt,
            ):
                assert application._confirm_florence_download_if_needed() is None
                assert prompt.call_count == 1
                assert "1.43 GiB" in prompt.call_args.args[1]
            application._finish_close()
    print(
        "v0.28.1 cumulative GUI smoke test passed: provider preflights, "
        "explicit download consent, and shared setup actions preserve the "
        "established catalog workspace."
    )


if __name__ == "__main__":
    run()
