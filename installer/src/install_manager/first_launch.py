"""Compatibility entrypoints for the persistent manager shell."""
from pathlib import Path

from .manager_ui import friendly_error, progress_view, show_first_run, show_installed


def show(delivery: Path, initial_root: Path, execute, activate, launch, *, install_component=None,
         record_existing_component=None,
         ui_probe: Path | None = None, review_mode: bool = False,
         initial_page: str | None = None, quiet: bool = False,
         review_scenario: str | None = None, initial_model_root: Path | None = None):
    return show_first_run(delivery, initial_root, execute, activate, launch,
                          install_component=install_component,
                          record_existing_component=record_existing_component,
                          ui_probe=ui_probe, review_mode=review_mode, initial_page=initial_page,
                          quiet=quiet, review_scenario=review_scenario,
                          initial_model_root=initial_model_root)


def show_manager(delivery: Path, root: Path, launch, move=None, *, record: dict | None = None,
                 prepare=None, activate=None,
                 install_component=None,
                 record_existing_component=None,
                 ui_probe: Path | None = None, review_mode: bool = False,
                 initial_page: str | None = None, review_scenario: str | None = None):
    return show_installed(delivery, root, launch, move, record=record, prepare=prepare, activate=activate,
                          ui_probe=ui_probe,
                          install_component=install_component,
                          record_existing_component=record_existing_component,
                          review_mode=review_mode, initial_page=initial_page,
                          review_scenario=review_scenario)
