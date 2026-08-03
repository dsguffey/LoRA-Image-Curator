"""Own the versioned first-launch third-party and warranty acknowledgment.

The notice is disclosure, not a substitute for an upstream license. Choosing OK
stores only the release-owned notice version in the normal local settings file;
no identity, timestamp, telemetry event, or network request is created. A new
material notice revision appears once even for an established installation.
"""

from __future__ import annotations

import os
import tkinter as tk

from pathlib import Path
from tkinter import ttk

from app_identity import APP_NAME
from provider_registry import notice_version
from settings_manager import AppSettings, load_settings, save_settings
from ui_fonts import get_ui_font


TEST_SKIP_ENVIRONMENT = "LORA_IMAGE_CURATOR_TEST_MODE"
PROJECT_ROOT = Path(__file__).resolve().parent


def notice_is_required(settings: AppSettings) -> bool:
    """Return whether the current disclosure has not yet been acknowledged."""
    return settings.third_party_notice_version != notice_version()


def record_notice_acknowledgement(settings: AppSettings) -> None:
    """Persist only the current disclosure version after an explicit OK."""
    settings.third_party_notice_version = notice_version()
    save_settings(settings)


def _show_local_document(parent: tk.Misc, title: str, filename: str) -> None:
    """Display one bundled notice without invoking a browser or network app."""
    try:
        document_text = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
    except OSError as error:
        document_text = f"Could not read {filename}:\n\n{error}"
    window = tk.Toplevel(parent)
    window.title(title)
    window.geometry("760x620")
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    text = tk.Text(frame, wrap="word", padx=12, pady=10)
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scrollbar.set)
    text.insert("1.0", document_text)
    text.configure(state="disabled")
    ttk.Button(window, text="Close", command=window.destroy).pack(pady=(0, 12))
    window.transient(parent)
    window.grab_set()
    window.focus_set()


def show_first_launch_notice(root: tk.Tk) -> bool:
    """Show the modal disclosure when needed and return whether to open the app."""
    if os.environ.get(TEST_SKIP_ENVIRONMENT) == "1":
        return True
    settings = load_settings()
    if not notice_is_required(settings):
        return True

    accepted = False
    window = tk.Toplevel(root)
    window.title("Third-Party Components and Warranty Notice")
    window.geometry("720x610")
    window.minsize(560, 440)

    # The application root is deliberately withdrawn until the user responds
    # to this first-launch notice. On Windows, making the notice transient to
    # that withdrawn owner can keep the notice hidden as well: app.py then
    # waits forever for a dialog the user cannot see. Keep this first window
    # independent of the hidden root, map it before taking the modal grab, and
    # raise/focus it only after Windows confirms that it is visible. Document
    # viewers opened from the notice may still be transient because their
    # parent notice is visible.

    frame = ttk.Frame(window, padding=18)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)

    ttk.Label(
        frame,
        text="Third-Party Components and Warranty Notice",
        font=get_ui_font(root, size=14, weight="bold"),
    ).grid(row=0, column=0, sticky="w", pady=(0, 12))

    text_frame = ttk.Frame(frame)
    text_frame.grid(row=1, column=0, sticky="nsew")
    text_frame.columnconfigure(0, weight=1)
    text_frame.rowconfigure(0, weight=1)
    content = tk.Text(text_frame, wrap="word", padx=12, pady=10)
    content.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=content.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    content.configure(yscrollcommand=scrollbar.set)
    content.insert(
        "1.0",
        (
            f"{APP_NAME} is copyright David Scott Guffey and is distributed "
            "under the MIT License. It is provided without warranty, subject "
            "to the bundled LICENSE file.\n\n"
            "Optional models, provider packages, runtimes, and external tools "
            "are third-party products. Their publisher, tested identity, "
            "source, license, restrictions, and integrity status are recorded "
            "in provider_registry.json, MODEL_LICENSES.txt, "
            "THIRD_PARTY_NOTICE.md, and SBOM.spdx.json. Their authors do not "
            "create, sponsor, or endorse LoRA Image Curator.\n\n"
            "Some pretrained face-model weights, including InsightFace "
            "buffalo_l, are restricted to non-commercial research use unless "
            "separately licensed. A click here does not change or expand those "
            "upstream rights.\n\n"
            "Explicit installation or model-download actions contact the named "
            "publisher host. Ordinary cataloging, analysis, review, and export "
            "are local, and LoRA Image Curator does not upload your images, "
            "catalogs, embeddings, or results.\n\n"
            "LoRA Image Curator does not collect telemetry data, but some "
            "third-party tools it's using may. The default settings for them "
            "are set to telemetry off.\n\n"
            "Choose OK to acknowledge that these disclosures were shown and "
            "open the application. Only notice revision "
            f"{notice_version()} is stored locally. Closing this window exits "
            "the app."
        ),
    )
    content.configure(state="disabled")

    button_row = ttk.Frame(frame)
    button_row.grid(row=2, column=0, sticky="ew", pady=(14, 0))

    def exit_application() -> None:
        window.destroy()

    def accept_notice() -> None:
        nonlocal accepted
        record_notice_acknowledgement(settings)
        accepted = True
        window.destroy()

    ttk.Button(
        button_row,
        text="View Application License",
        command=lambda: _show_local_document(window, "Application License", "LICENSE"),
    ).pack(side="left", padx=(0, 8))
    ttk.Button(
        button_row,
        text="View Third-Party Notices",
        command=lambda: _show_local_document(
            window,
            "Third-Party Notices",
            "THIRD_PARTY_NOTICE.md",
        ),
    ).pack(side="left")
    ttk.Button(
        button_row,
        text="OK",
        command=accept_notice,
        default="active",
    ).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", exit_application)
    window.bind("<Escape>", lambda _event: exit_application())
    window.bind("<Return>", lambda _event: accept_notice())
    window.wait_visibility()
    window.lift()
    window.grab_set()
    window.focus_force()
    root.wait_window(window)
    return accepted
