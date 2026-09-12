"""Customer-facing persistent Windows manager shell for first-run and ongoing use."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable
import webbrowser

from .acquisition import AcquisitionCancelled
from .bootstrap import BootstrapIdentityMismatch, disclosure
from .bootstrap_layout import validate_root
from .capabilities import first_run_contract, lic_capabilities
from .component_catalog import (CancellationToken, ComponentAction, ComponentFacts,
                                ComponentOperationQueue, ComponentPhase, component_action,
                                load_component_catalog, product_ready, progress_presentation,
                                validate_existing_selection)
from .component_state import load_inventory
from .component_operations import (discard_cancelled_attempt, operation_download_summary,
                                   inspect_recovery as inspect_component_recovery)
from .managed_move import move_plan
from .florence_component import (inspect_installed as inspect_florence_installed,
                                 inspect_recovery as inspect_florence_recovery)
from .recovery import (BootstrapRecovery, inspect_bootstrap_recovery,
                       restored_recovery_locations, validate_new_recovery_target)
from .storage import default_model_root, inspect_model_storage, storage_review, validate_model_root
from .lic_face_settings import read_face_model_root
from .lic_face_settings import read_provider_location, write_provider_location
from .cancellation_cleanup import delete_unfinished_acquisitions, unfinished_acquisitions
from .journal import OperationJournal
from .provider_discovery import discover_provider_candidates, discovery_message
from .product import (PRODUCT_EXPANDED_NAME, PRODUCT_NAME as PRODUCT_DISPLAY_NAME,
                      PRODUCT_VERSION, DEPENDENCY_PROFILE_ID)


NAVY = "#18324a"
NAVY_ACTIVE = "#274e6e"
BLUE = "#216a9a"
PALE = "#eef5f9"
INK = "#18313f"
MUTED = "#617380"
GREEN = "#24734f"
AMBER = "#9a6515"
RED = "#9b3d3d"
WHITE = "#ffffff"
GOLD = "#e2ae36"
IDENTITY_LOCKED_PHASES = frozenset({
    ComponentPhase.PREPARING, ComponentPhase.DOWNLOADING, ComponentPhase.VERIFYING,
    ComponentPhase.INSTALLING, ComponentPhase.CANCELING,
})
OPTIONAL_PROVIDER_IDS = frozenset({"florence-captioning", "face-analysis", "body-analysis", "video-extraction"})
PRODUCT_NAME = "LoRA Image Curator"
MANAGER_NAME = PRODUCT_DISPLAY_NAME
FIRST_RUN_SECTIONS = ("Install & Update", "Move Installation", "Help")
INSTALLED_SECTIONS = FIRST_RUN_SECTIONS
HELP_ANCHORS = {
    "core-functionality": "Core functionality",
    "florence-captioning": "Optional features and providers",
    "face-analysis": "Optional features and providers",
    "body-analysis": "Optional features and providers",
    "video-extraction": "Optional features and providers",
}


def identity_controls_editable(phase: ComponentPhase) -> bool:
    return phase not in IDENTITY_LOCKED_PHASES


def active_launch_contract(record: dict | None, root: Path) -> bool:
    """Use the durable activation record and essential files; never infer readiness from UI state."""
    if not record or record.get("state") != "active":
        return False
    try:
        if Path(record["root"]).resolve() != root.resolve():
            return False
        application = Path(record["application"])
        python = Path(record["python"])
        manager = Path(record["manager"])
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return python.is_file() and manager.is_file() and (application / "app.py").is_file()


def component_status_text(definition, facts: ComponentFacts) -> str:
    if facts.detail:
        return facts.detail
    return {
        "florence-model-directory": "Select the Florence model folder or a parent Hugging Face model directory.",
        ComponentPhase.CHECKING: "Checking installed files…",
        ComponentPhase.NOT_INSTALLED: ("Not installed" if definition.managed_install else
                                       "Not configured. Automatic installation is not currently available. "
                                       + selection_instruction(definition)),
        ComponentPhase.PARTIAL: "Installation was interrupted. Verified completed work can be reused.",
        ComponentPhase.QUEUED: "Queued",
        ComponentPhase.PREPARING: "Preparing installation…",
        ComponentPhase.DOWNLOADING: f"Downloading from {definition.source_name}…",
        ComponentPhase.VERIFYING: "Verifying downloaded files…",
        ComponentPhase.INSTALLING: "Installing and configuring…",
        ComponentPhase.CANCELING: "Canceling at the next safe boundary…",
        ComponentPhase.INSTALLED: "✓ Installed and verified",
        ComponentPhase.UPDATE_AVAILABLE: "A manager-approved compatible update is available.",
        ComponentPhase.REPAIR_REQUIRED: ("Installed files need repair. Choosing Repair authorizes reacquisition "
                                         "only of the missing approved files disclosed for this component."),
        ComponentPhase.INCOMPATIBLE: "The selected files are not compatible.",
        ComponentPhase.ERROR: "The operation stopped safely.",
    }[facts.phase]


def selection_instruction(definition) -> str:
    """Short, visible contract for Browse; validation remains authoritative."""
    return {
        "mediapipe-task-file": "Choose a MediaPipe provider folder; the approved task is found locally.",
        "ffmpeg-executable": "Choose an FFmpeg folder; ffmpeg.exe is found in that folder or its bin folder.",
        "insightface-pack-directory": "Select an InsightFace model-pack folder containing the expected ONNX files.",
        "yunet-sface-pair-v1": "Select the folder containing both approved Face Analysis ONNX files.",
    }.get(definition.validation_adapter, "Select compatible existing files.")


def provider_root_from_resource(component_id: str, value: str | Path) -> Path:
    """Project an inventory member back to the folder a person selected."""
    path = Path(value)
    if (component_id == "body-analysis" and path.name.casefold() == "pose_landmarker_full.task" or
            component_id == "face-analysis" and path.suffix.casefold() == ".onnx" or
            component_id == "video-extraction" and path.name.casefold() == "ffmpeg.exe"):
        return path.parent
    return path


def primary_label(definition, facts: ComponentFacts, action: ComponentAction | None = None) -> str:
    if facts.phase == ComponentPhase.CANCELING:
        return "Canceling…"
    action = action or component_action(definition, facts)
    return ("Install Core" if definition.component_id == "lic-core" and action == ComponentAction.INSTALL else
            "Install required libraries" if action == ComponentAction.INSTALL and facts.selected_path and definition.component_id != "lic-core" else
            "Choose provider folder" if action == ComponentAction.USE_EXISTING and definition.selector_type != "none" else action.value)


def picker_contract(definition) -> dict[str, object]:
    """Explicit native-picker semantics, kept separate from card presentation."""
    contracts = {
        "florence-model-directory": {"title": "Select Florence model folder", "filetypes": [],
                                       "hint": "Select the pinned Florence snapshot or a parent Hugging Face model directory. Known subfolders are checked without downloading.",
                                       "label": "Existing Florence model folder"},
        "mediapipe-task-file": {"title": "Choose MediaPipe provider folder", "filetypes": [],
                                  "hint": "Choose a provider folder. The approved pose_landmarker_full.task is found without downloading.",
                                  "label": "MediaPipe provider folder"},
        "ffmpeg-executable": {"title": "Choose FFmpeg folder", "filetypes": [],
                              "hint": "Choose the FFmpeg folder. ffmpeg.exe is found in this folder or its bin folder and verified before use.",
                              "label": "FFmpeg folder"},
        "insightface-pack-directory": {"title": "Select InsightFace model-pack folder", "filetypes": [],
                                         "hint": "Select the model-pack folder directly inside models, containing ONNX files.",
                                         "label": "Existing InsightFace model folder"},
        "yunet-sface-pair-v1": {"title": "Select Face Analysis model folder", "filetypes": [],
                                  "hint": "Select one folder containing the qualified YuNet and SFace ONNX files.",
                                  "label": "Active Face Analysis model location"},
    }
    return contracts.get(definition.validation_adapter, {"title": "Select existing files", "filetypes": [], "hint": "Select compatible existing files.", "label": "Existing files"})


def open_official_source(url: str, opener=webbrowser.open_new_tab) -> bool:
    """Open only a displayed HTTPS official source after a user action."""
    if not url.startswith("https://"):
        return False
    return bool(opener(url))


def capability_detail_contract(capability, plan: dict, application_path: Path,
                               model_path: Path) -> dict:
    """Human fields first; identifiers and hashes remain explicitly advanced."""
    if capability.capability_id == "captioning":
        model = capability.details
        return {
            "title": capability.name,
            "summary": capability.summary,
            "sections": [
                ("ABOUT THIS FEATURE", [
                ("Adds", "Optional AI image captioning and triage"),
                    ("Provided by", "Florence community / Microsoft"),
                ]),
                ("MODEL & STORAGE", [
                    ("Model", "Florence-2 large fine-tuned"),
                    ("Location", str(model_path)),
                ]),
                ("COMPATIBILITY", [("Status", "✓ Verified for this managed NVIDIA configuration")]),
                ("LICENSE & SOURCE", [("Source", "Hugging Face"),
                                        ("Use", "The model's publisher terms apply.")]),
            ],
            "advanced": [("Exact model", model["model"]), ("Revision", model["revision"]),
                         ("Source host", model["source_host"])],
        }
    lic = plan["details"]["LIC Lite"]
    return {
        "title": capability.name,
        "summary": capability.summary,
        "sections": [
            ("ABOUT THIS FEATURE", [
                ("What this does", "Provides catalog, review, caption and export tools."),
                ("Publisher", "LoRA Image Curator project"),
            ]),
            ("INSTALLATION", [("Version", lic["version"]), ("Location", str(application_path))]),
            ("LICENSE & NOTICES", [("Notices", "License and third-party notices are included with the application.")]),
        ],
        "advanced": [("Artifact ID", lic["artifact"]), ("SHA-256", lic["sha256"]),
                     ("Provenance", lic["source"])],
    }


def component_download_disclosure(definition, plan: dict | None = None) -> tuple[str, str]:
    """Return the customer-facing download label/value for one component.

    Core has a real acquisition plan available before any work begins.  Prefer
    that plan over the catalog's broad planning estimate so a delivery that
    bundles its verified Core artifacts does not imply another download.
    Optional components retain their catalog estimate until their own exact
    operation plan has been selected.
    """
    if definition.component_id == "lic-core" and plan is not None:
        missing = int(plan.get("download_bytes_without_reuse", 0))
        if missing == 0:
            return "Additional download", "Included — no additional download"
        return "Additional download", human_size(missing)
    if definition.component_id == "face-analysis" and plan is not None:
        missing = int(plan.get("download_bytes", 0))
        return "Download required", ("None — approved files are already available" if missing == 0
                                     else human_size(missing))
    download = (human_size(definition.estimated_bytes)
                if definition.estimated_bytes is not None else "Size is not published reliably")
    return "Expected download", download


def component_detail_contract(definition, facts: ComponentFacts, storage_path: str = "",
                              plan: dict | None = None) -> dict:
    download_label, download = component_download_disclosure(definition, plan)
    location = storage_path or facts.selected_path or definition.storage_policy
    sections = [
        ("SUMMARY", [("Capability", definition.description),
                     ("Classification", "Required core functionality" if definition.tier == "core" else "Optional feature")]),
        ("PROVIDER", [("Provider", definition.provider), ("Publisher", definition.publisher)]),
        ("SOURCE", [("Downloaded from", definition.source_name), ("Official source", definition.source_url)]),
        ("DOWNLOAD", [(download_label, download)]),
        ("STORAGE", [("Current location", location)]),
        ("COMPATIBILITY", [("Supported configuration", definition.compatibility),
                           ("Current status", component_status_text(definition, facts))]),
        ("LICENSE / RESTRICTIONS", [("License", definition.license),
                                    ("Practical restriction", definition.restrictions or "No additional restriction recorded")]),
    ]
    advanced = [("Component ID", definition.component_id),
                ("Acquisition adapter", definition.acquisition_adapter),
                ("Validation adapter", definition.validation_adapter),
                ("Update policy", definition.update_policy),
                ("Identity", json.dumps(definition.identity, sort_keys=True))]
    if facts.diagnostic:
        advanced.append(("Diagnostic", facts.diagnostic))
    return {"title": definition.capability, "summary": definition.description,
            "sections": sections, "advanced": advanced}


def human_size(value: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "bytes" else f"{value:,.1f} {unit}"
        value /= 1000
    return "Unknown"


def model_status_presentation(evidence: dict, *, concise: bool = False) -> str:
    """Translate verified storage evidence into customer language without weakening it."""
    if evidence.get("reusable"):
        return ("Existing compatible model found — no model download is required."
                if concise else "✓ Existing compatible model found\nNo model download is required.")
    if evidence.get("status") == "missing":
        size = human_size(int(evidence.get("download_bytes", 0)))
        return (f"No compatible model found — about {size} will be downloaded."
                if concise else f"No compatible model found\nAbout {size} will be downloaded.")
    if evidence.get("status") == "incompatible":
        return ("Files found here do not match the verified model required by LoRA Image Curator."
                if concise else "Files were found here, but they do not match the verified model version required by LoRA Image Curator.")
    return evidence.get("message", "Model location needs review.")


def compact_path(value: str, *, limit: int = 72) -> str:
    """Keep review rows readable while preserving the complete path in a tooltip."""
    return value if len(value) <= limit else value[:limit - 1] + "…"


def friendly_error(error: Exception) -> str:
    message = str(error).lower()
    if "previous installation folder no longer exists" in message:
        return ("The earlier installation folder no longer exists, so setup cannot resume there. "
                "Choose a new empty location to start setup again.")
    if "target is populated" in message or "existing files will not be adopted" in message:
        return ("The selected installation folder already contains files. To keep them safe, the manager will not "
                "adopt or overwrite them. Choose a new empty folder.")
    if "does not match this exact authorized plan" in message or "different approved installer profile" in message:
        return ("This interrupted setup belongs to a different installer plan. Its files were preserved. "
                "Choose a new empty location to start the current setup.")
    if "access is denied" in message or "permission denied" in message:
        return ("The manager could not access a required file or folder. Close any program using it, check that "
                "the folder is writable, then try again.")
    if "no module named" in message or "delivered validation probe is missing" in message:
        return ("The installer package is incomplete or damaged. Download a fresh verified installer package "
                "before trying again.")
    if "download" in message or "acquisition" in message or "urlopen" in message:
        return ("A required download could not be completed. Your existing files are safe. "
                "Check your connection and select Retry.")
    if "cuda" in message or "gpu" in message:
        return "The NVIDIA GPU readiness check did not pass. Your existing files are safe."
    if "readiness" in message or "preflight" in message or "validation" in message:
        return "LoRA Image Curator did not pass final validation, so the existing installation was left unchanged."
    return "The operation stopped safely. Existing files were preserved."


def component_failure_message(definition, error: str) -> str:
    """Explain a provider failure without making a raw exception the card text."""
    message = error.lower()
    if definition.component_id == "video-extraction":
        if "timeout" in message:
            return ("FFmpeg did not respond during verification. Its files were not changed. Close any program using "
                    "FFmpeg, then choose the folder again or try a different FFmpeg folder.")
        if "not identify" in message or "ffmpeg" in message:
            return ("The selected FFmpeg folder could not be configured. Its files were not changed. "
                    "Choose a folder containing ffmpeg.exe, then review Details if it still fails.")
        return ("FFmpeg could not be configured. Its files were not changed. Choose the folder again, then review "
                "Details for the diagnostic if the problem continues.")
    if definition.component_id == "body-analysis":
        return ("Body and pose analysis could not be configured. Existing model files were not changed. "
                "Choose the provider folder again, then review Details if the problem continues.")
    if definition.component_id == "face-analysis":
        return ("Face Analysis could not be configured. Existing model files were not changed. "
                "Choose the folder containing both model files again, then review Details if the problem continues.")
    if definition.component_id == "florence-captioning":
        return ("Image captioning could not be configured. Existing model files were not changed. "
                "Review Details and the component log before trying again.")
    return friendly_error(RuntimeError(error))


def progress_view(event) -> dict:
    if not isinstance(event, dict):
        event = {"message": str(event), "terminal": None}
    terminal = event.get("terminal")
    downloaded, total = event.get("downloaded_bytes"), event.get("total_bytes")
    if terminal:
        mode, value, working = "idle", (100.0 if terminal == "success" else 0.0), False
    elif isinstance(downloaded, int) and isinstance(total, int) and total > 0:
        mode, value, working = "determinate", min(100.0, downloaded * 100.0 / total), True
    else:
        mode, value, working = "indeterminate", 0.0, True
    return {"message": event.get("message", ""), "step": event.get("step"),
            "total_steps": event.get("total_steps"), "mode": mode, "value": value,
            "working": working, "terminal": terminal}


class Tooltip:
    def __init__(self, widget, text: str):
        self.widget, self.text, self.popup = widget, text, None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.popup:
            return
        x, y = self.widget.winfo_rootx() + 20, self.widget.winfo_rooty() + 24
        self.popup = tk.Toplevel(self.widget)
        self.popup.wm_overrideredirect(True)
        self.popup.wm_geometry(f"+{x}+{y}")
        tk.Label(self.popup, text=self.text, bg="#fff7d6", fg=INK, relief="solid", borderwidth=1,
                 padx=9, pady=6, wraplength=320, justify="left").pack()

    def hide(self, _event=None):
        if self.popup:
            self.popup.destroy()
            self.popup = None


class DetailsDialog:
    def __init__(self, parent, title: str, summary: str,
                 sections: list[tuple[str, list[tuple[str, str]]]],
                 advanced: list[tuple[str, str]] = ()):
        window = self.window = tk.Toplevel(parent)
        window.title(f"{title} — Details")
        window.geometry("760x620")
        window.minsize(620, 500)
        window.transient(parent)
        window.grab_set()
        viewport = ttk.Frame(window)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, background=WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(canvas, padding=24)
        content_window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width))
        ttk.Label(outer, text=title, style="PageTitle.TLabel", wraplength=660,
                  justify="left").pack(anchor="w")
        ttk.Label(outer, text=summary, style="Body.TLabel", wraplength=660,
                  justify="left").pack(anchor="w", pady=(5, 14))
        for section_title, fields in sections:
            ttk.Label(outer, text=section_title, style="DetailHeading.TLabel").pack(anchor="w", pady=(10, 4))
            card = ttk.Frame(outer, style="Card.TFrame", padding=12)
            card.pack(fill="x")
            for label, value in fields:
                row = ttk.Frame(card, style="Card.TFrame")
                row.pack(fill="x", pady=3)
                ttk.Label(row, text=label, style="Field.TLabel", width=16).pack(side="left", anchor="n")
                ttk.Label(row, text=value, style="CardBody.TLabel", wraplength=430,
                          justify="left").pack(side="left", fill="x", expand=True)
                if label == "Official source" and value.startswith("https://"):
                    ttk.Button(row, text="Open official source",
                               command=lambda address=value: open_official_source(address)).pack(side="right", padx=(8, 0))
        advanced_frame = ttk.Frame(outer)
        if advanced:
            visible = tk.BooleanVar(False)
            def toggle():
                visible.set(not visible.get())
                if visible.get():
                    advanced_frame.pack(fill="x", pady=(10, 0))
                    button.configure(text="Hide advanced technical details")
                else:
                    advanced_frame.pack_forget()
                    button.configure(text="Show advanced technical details")
            ttk.Label(outer, text="ADVANCED TECHNICAL DETAILS", style="DetailHeading.TLabel").pack(anchor="w", pady=(14, 4))
            button = ttk.Button(outer, text="Show advanced technical details", command=toggle)
            button.pack(anchor="w")
            for label, value in advanced:
                ttk.Label(advanced_frame, text=f"{label}: {value}", style="Muted.TLabel",
                          wraplength=650).pack(anchor="w", pady=2)
        ttk.Button(outer, text="Close", command=window.destroy).pack(anchor="e", pady=(16, 0))


class ManagerShell:
    def __init__(self, delivery: Path, root: Path, *, prepare=None, activate=None, launch=None,
                 install_component=None,
                 record_existing_component=None,
                 move=None, installed_record: dict | None = None, review_mode=False,
                 initial_page: str | None = None, ui_probe: Path | None = None,
                 quiet: bool = False, review_scenario: str | None = None,
                 initial_model_root: Path | None = None):
        self.delivery, self.root = delivery, root
        self.prepare, self.activate, self.launch, self.move = prepare, activate, launch, move
        self.install_component = install_component
        self.record_existing_component = record_existing_component
        self.record, self.review_mode, self.quiet = installed_record, review_mode, quiet
        self.review_scenario = review_scenario
        self.mode = "installed" if installed_record else "first-run"
        self.plan = disclosure(delivery, root)
        self.components = load_component_catalog(delivery / "recipes/lic-components.json")
        self.component_by_id = {item.component_id: item for item in self.components}
        selected_models = (initial_model_root if initial_model_root is not None else
                           Path(installed_record.get("model_root"))
                           if installed_record and installed_record.get("model_root") else
                           default_model_root(root))
        self.recovery_journal_path: Path | None = root / "State/operations/bootstrap.json"
        self.recovery = self._load_recovery(root, selected_models)
        self.florence_recovery = self._load_florence_recovery(root, selected_models)
        self.component_recoveries = {}
        for definition in self.components:
            if definition.managed_install and definition.component_id not in {"lic-core", "florence-captioning"}:
                recovered = self._load_component_recovery(definition.component_id, None)
                if recovered is not None:
                    self.component_recoveries[definition.component_id] = recovered
        self._apply_review_recovery(root, selected_models)
        self.model_evidence = None
        try:
            self.model_evidence = inspect_model_storage(delivery, selected_models)
        except (OSError, ValueError):
            pass
        self.component_facts = self._initial_component_facts()
        self.operation_queue = ComponentOperationQueue()
        self.component_widgets = {}
        self.help_anchor = ""
        self.window = tk.Tk()
        self.application_path = tk.StringVar(value=str(root))
        self.model_path = tk.StringVar(value=str(selected_models))
        if self.recovery and self.review_scenario == "recovery-mismatch":
            self.model_path.set(str(self.recovery.current_model_root))
        self.component_paths = {item.component_id: tk.StringVar(value=(str(selected_models)
                                if item.component_id == "florence-captioning" else
                                self.component_facts[item.component_id].selected_path))
                                for item in self.components if item.selector_type != "none"}
        self.lic_appdata = Path(os.environ.get("APPDATA", Path.home() / ".config"))
        face = self.component_by_id.get("face-analysis")
        if face and "face-analysis" in self.component_paths and not self.component_facts["face-analysis"].selected_path:
            canonical = read_face_model_root(self.lic_appdata)
            if canonical:
                self.component_paths["face-analysis"].set(canonical)
        self._restore_provider_preferences()
        choices = installed_record.get("choices", {}) if installed_record else {}
        self.start_menu = tk.BooleanVar(value=choices.get("start_menu", True))
        self.desktop = tk.BooleanVar(value=choices.get("desktop", False))
        if review_scenario == "florence-parent-discovered":
            self.model_path.set(r"C:\TestProfile\Shared Models")
            self.model_evidence = {"status": "compatible", "reusable": True,
                                   "message": "Existing compatible Florence image-analysis model found.",
                                   "snapshot": r"C:\TestProfile\Shared Models\huggingface\hub\models--florence-community--Florence-2-large-ft\snapshots\26b734a54fdfbf9c398351eedfabb7f27fc470b7"}
        self.busy = False
        self.events = queue.Queue()
        self.nav_buttons = {}
        self.current_page = ""
        self.window.title(MANAGER_NAME)
        width = min(1800, self.window.winfo_screenwidth() - 80)
        height = min(820, self.window.winfo_screenheight() - 100)
        left = max(20, (self.window.winfo_screenwidth() - width) // 2)
        top = max(20, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{left}+{top}")
        self.window.minsize(min(940, width), min(650, height))
        self.window.configure(bg=WHITE)
        self._styles()
        self._shell()
        sections = FIRST_RUN_SECTIONS if self.mode == "first-run" else INSTALLED_SECTIONS
        self.sections = list(sections)
        self._navigation(sections)
        page = initial_page if initial_page in sections else "Install & Update"
        if initial_page == "Details":
            page = "Install & Update"
            detail_index = 0 if review_scenario == "core-disclosure" else 1
            self.window.after(600, lambda: self.show_component_details(self.components[detail_index]))
        self.show_page(page)
        if review_scenario == "insightface-existing":
            self.window.after(450, lambda: self.canvas.yview_moveto(0.26))
        elif review_scenario and (review_scenario.startswith("optional-") or review_scenario in
                                  {"mediapipe-existing", "ffmpeg-selected", "florence-absent",
                                   "florence-existing", "legacy-florence"}):
            self.window.after(450, lambda: self.canvas.yview_moveto(0.25))
        elif review_scenario == "active-download":
            self.window.after(450, lambda: self.canvas.yview_moveto(0.25))
        elif review_scenario in {"interrupted",
                                "recovery-matching", "recovery-mismatch", "recovery-restored"}:
            self.window.after(450, lambda: self.canvas.yview_moveto(0.20))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(100, self.poll)
        if self.quiet:
            # The auto-closing probe validates layout/state without repeatedly taking focus.
            self.window.withdraw()
        if ui_probe:
            self.window.after(1400, lambda: self._write_probe(ui_probe))

    def _restore_provider_preferences(self) -> None:
        """Use a remembered location only as an editable default, never as inventory truth."""
        for component_id in OPTIONAL_PROVIDER_IDS:
            definition = self.component_by_id.get(component_id)
            facts = self.component_facts.get(component_id)
            if definition is None or facts is None or facts.selected_path:
                continue  # Per-install inventory always wins.
            value = read_provider_location(self.lic_appdata, component_id)
            if not value:
                continue
            if component_id == "florence-captioning":
                self.model_path.set(value)
                try:
                    self.model_evidence = inspect_model_storage(self.delivery, Path(value))
                except (OSError, ValueError):
                    pass
            else:
                variable = self.component_paths.get(component_id)
                if variable is not None:
                    variable.set(value)
            # A preference may be stale. It remains an editable starting point
            # and never changes the component to installed during passive startup.
            facts.selected_path = value

    def _primary_action_for(self, definition, facts: ComponentFacts) -> ComponentAction:
        state = self.operation_queue.state_for(definition.component_id)
        if state == "active":
            return ComponentAction.CANCEL
        if state == "queued":
            return ComponentAction.CANCEL_QUEUE
        return component_action(definition, facts)

    def _load_recovery(self, current_install: Path,
                       current_models: Path) -> BootstrapRecovery | None:
        if self.recovery_journal_path is None:
            return None
        try:
            return inspect_bootstrap_recovery(
                self.recovery_journal_path,
                current_install_root=current_install,
                current_model_root=current_models,
            )
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _load_florence_recovery(self, current_install: Path,
                                current_models: Path):
        try:
            return inspect_florence_recovery(self.delivery, current_install, current_models)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _load_component_recovery(self, component_id: str, selected_path: Path | None):
        try:
            return inspect_component_recovery(self.delivery, self.root, component_id, selected_path)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _apply_review_recovery(self, root: Path, selected_models: Path) -> None:
        scenario = self.review_scenario
        if scenario not in {"recovery-matching", "recovery-mismatch", "recovery-restored"}:
            return
        recorded_models = default_model_root(root).resolve()
        current_models = (Path(r"C:\TestProfile\Existing Models")
                          if scenario == "recovery-mismatch" else recorded_models)
        self.recovery = BootstrapRecovery(
            journal_path=(root / "State/operations/bootstrap.json").resolve(),
            status="cancelled",
            recorded_install_root=root.resolve(),
            recorded_model_root=recorded_models,
            current_install_root=root.resolve(),
            current_model_root=current_models.resolve(),
            completed_steps=1,
            total_steps=10,
        )

    def _initial_component_facts(self) -> dict[str, ComponentFacts]:
        facts = {item.component_id: ComponentFacts() for item in self.components}
        core = facts["lic-core"]
        core.detail = ("Not installed. Nothing will be downloaded until you choose Install Core; that action "
                       "authorizes the missing Core items listed on this card and in Details.")
        if active_launch_contract(self.record, self.root):
            core.phase, core.verified, core.detail = (ComponentPhase.INSTALLED, True,
                                                      "✓ Installed and verified")
        elif self.recovery:
            core.phase = ComponentPhase.PARTIAL
            core.resumable = self.recovery.resumable
            core.recovery_blocked = self.recovery.blocked
            core.completed_bytes = self.recovery.completed_steps
            core.total_bytes = self.recovery.total_steps or None
            core.detail = self.recovery.summary
        elif self.recovery_journal_path and self.recovery_journal_path.is_file():
            core.phase, core.detail = (ComponentPhase.REPAIR_REQUIRED,
                                       "Setup records could not be read safely.")
        if self.record:
            try:
                inventory = load_inventory(self.root)
            except (OSError, ValueError, TypeError, KeyError):
                inventory = None
            if inventory is not None:
                for component in inventory.components:
                    item = facts.get(component.component_id)
                    if item is None or component.component_id == "lic-core":
                        continue
                    item.phase = (ComponentPhase.INSTALLED if component.state == "installed" and
                                  component.readiness.get("passed") else ComponentPhase.PARTIAL)
                    item.verified = item.phase == ComponentPhase.INSTALLED
                    item.detail = ("✓ Installed and verified" if item.verified else
                                   "Installed component state requires validation or repair.")
                    if component.resources:
                        item.selected_path = str(provider_root_from_resource(
                            component.component_id, component.resources[0].local_path))
        florence = facts.get("florence-captioning")
        if florence is not None and self.model_evidence:
            florence.detail = "Not installed. Nothing will be downloaded until you choose Install."
            if self.model_evidence.get("status") == "compatible":
                florence.phase = ComponentPhase.PARTIAL
                florence.completed_bytes = int(self.model_evidence.get("expected_bytes") or 0)
                florence.total_bytes = florence.completed_bytes or None
                florence.selected_path = str(self.model_evidence.get("snapshot") or "")
                florence.detail = ("Compatible Florence model files found. The optional runtime still needs "
                                    "to be installed explicitly." )
            if self.record:
                state = inspect_florence_installed(self.delivery, self.root,
                                                   Path(self.model_evidence.get("model_root") or
                                                        default_model_root(self.root)), self.record)
                if state["ready"]:
                    florence.phase, florence.verified = ComponentPhase.INSTALLED, True
                    florence.detail = ("✓ Florence is installed and verified" +
                                       (" (recognized legacy installation)" if state["legacy"] else ""))
            if self.florence_recovery:
                florence.phase = ComponentPhase.PARTIAL
                florence.resumable = self.florence_recovery.resumable
                florence.recovery_blocked = self.florence_recovery.blocked
                florence.completed_bytes = self.florence_recovery.completed_steps
                florence.total_bytes = self.florence_recovery.total_steps or None
                florence.detail = self.florence_recovery.summary
        for component_id, recovery in self.component_recoveries.items():
            item = facts.get(component_id)
            if item is None:
                continue
            # A verified current external selection is independent of an older
            # setup journal. Keep the recovery available as a separate action,
            # but never replace current resource evidence with it.
            if item.selected_path:
                continue
            item.phase = ComponentPhase.PARTIAL
            item.resumable = recovery.resumable
            item.recovery_blocked = recovery.blocked
            item.completed_bytes = recovery.completed_steps
            item.total_bytes = recovery.total_steps
            item.detail = recovery.summary
            item.selected_path = recovery.selected_path or ""
        scenario = self.review_scenario
        if scenario in {"core-ready", "core-ready-florence-absent"}:
            core.phase, core.verified, core.detail = ComponentPhase.INSTALLED, True, "✓ Installed and verified"
        elif scenario == "active-download":
            target = facts.get("florence-captioning", core)
            target.phase, target.completed_bytes, target.total_bytes = ComponentPhase.DOWNLOADING, 842_000_000, 1_540_000_000
            target.detail = "Downloading the explicitly selected Florence model from Hugging Face — 842 MB of 1.5 GB"
        elif scenario == "interrupted":
            core.phase, core.resumable, core.completed_bytes, core.total_bytes = ComponentPhase.PARTIAL, True, 4, 10
            core.detail = "Setup was interrupted. Verified completed work can be reused."
        elif scenario in {"recovery-matching", "recovery-mismatch", "recovery-restored"}:
            core.phase, core.resumable = ComponentPhase.PARTIAL, self.recovery.resumable
            core.recovery_blocked = self.recovery.blocked
            core.completed_bytes, core.total_bytes = 1, 10
            core.detail = self.recovery.summary
        elif scenario == "insightface-existing":
            target = facts["face-analysis"]
            target.phase = ComponentPhase.PARTIAL
            target.selected_path = r"C:\TestProfile\.insightface\models\buffalo_l"
            target.detail = ("Not configured. InsightFace model files were found and verified. Automatic "
                             "setup of the remaining InsightFace components is not currently available.")
        elif scenario == "florence-absent":
            target = facts["florence-captioning"]
            target.phase, target.verified = ComponentPhase.NOT_INSTALLED, False
            target.detail = "Not installed. Nothing will be downloaded until you choose Install."
        elif scenario in {"florence-existing", "legacy-florence"}:
            target = facts["florence-captioning"]
            target.phase = ComponentPhase.PARTIAL if scenario == "florence-existing" else ComponentPhase.INSTALLED
            target.verified = scenario == "legacy-florence"
            target.completed_bytes = target.total_bytes = 1_540_958_064
            target.selected_path = r"C:\TestProfile\Shared Models\huggingface\hub\models--florence-community--Florence-2-large-ft\snapshots\26b734a54fdfbf9c398351eedfabb7f27fc470b7"
            target.detail = ("Compatible Florence model files found. Install the optional runtime to use them."
                             if not target.verified else
                             "✓ Florence is installed and verified (recognized legacy installation)")
        elif scenario == "optional-installed":
            target = facts["body-analysis"]
            target.phase, target.verified, target.detail = ComponentPhase.INSTALLED, True, "✓ Installed and verified"
        elif scenario == "optional-existing":
            target = facts["body-analysis"]
            target.phase, target.verified, target.completed_bytes = ComponentPhase.INSTALLED, True, 9_398_198
            target.total_bytes, target.selected_path = 9_398_198, r"D:\AI Models\pose_landmarker_full.task"
            target.detail = "✓ Existing compatible files verified — no download required."
        elif scenario == "mediapipe-existing":
            target = facts["body-analysis"]
            target.phase, target.verified, target.completed_bytes = ComponentPhase.INSTALLED, True, 9_398_198
            target.total_bytes, target.selected_path = 9_398_198, r"C:\TestProfile\Models\pose_landmarker_full.task"
            target.detail = "✓ Existing compatible files verified — no download required."
        elif scenario == "ffmpeg-selected":
            target = facts["video-extraction"]
            target.phase, target.verified, target.selected_path = ComponentPhase.INSTALLED, True, r"C:\TestProfile\Tools\ffmpeg.exe"
            target.detail = "✓ Existing compatible FFmpeg executable verified."
        return facts

    def _styles(self):
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("TFrame", background=WHITE)
        style.configure("TLabel", background=WHITE, foreground=INK, font=("Segoe UI", 10))
        style.configure("PageTitle.TLabel", font=("Segoe UI Semibold", 22), foreground=INK)
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 13), foreground=INK)
        style.configure("Body.TLabel", font=("Segoe UI", 10), foreground=INK)
        style.configure("Muted.TLabel", font=("Segoe UI", 9), foreground=MUTED)
        style.configure("Field.TLabel", font=("Segoe UI Semibold", 9), foreground=MUTED)
        style.configure("DetailHeading.TLabel", font=("Segoe UI Semibold", 9), foreground=BLUE)
        style.configure("Card.TFrame", background=PALE, relief="flat")
        style.configure("CardTitle.TLabel", background=PALE, font=("Segoe UI Semibold", 11), foreground=INK)
        style.configure("CardBody.TLabel", background=PALE, font=("Segoe UI", 9), foreground=INK)
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(18, 10),
                        background=BLUE, foreground=WHITE)
        style.map("Accent.TButton", background=[("active", "#18577f"), ("disabled", "#aebbc3")])
        style.configure("TButton", font=("Segoe UI", 9), padding=(12, 7))
        style.configure("TEntry", padding=7)
        style.configure("Status.TLabel", background="#f5f8fa", foreground=MUTED, padding=8)
        style.configure("ThirdParty.TLabel", background="#dde6eb", foreground=INK,
                        font=("Segoe UI Semibold", 8), padding=(7, 3))
        style.configure("Complete.Horizontal.TProgressbar", troughcolor="#d7e2e8",
                        background=GREEN, lightcolor=GREEN, darkcolor=GREEN)

    def _shell(self):
        self.sidebar = tk.Frame(self.window, bg=NAVY, width=238)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="LoRA Image\nCurator", bg=NAVY, fg="#a8d8ef",
                 font=("Segoe UI Semibold", 16), justify="left").pack(anchor="w", padx=20, pady=(20, 0))
        tk.Label(self.sidebar, text="INSTALL MANAGER", bg=NAVY, fg=WHITE,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=20, pady=(4, 18))
        self.nav = tk.Frame(self.sidebar, bg=NAVY)
        self.nav.pack(fill="x")
        tk.Label(self.sidebar, text="Runs without administrator rights", bg=NAVY, fg="#b8cad7",
                 font=("Segoe UI", 8), wraplength=175, justify="left").pack(side="bottom", anchor="w",
                                                                            padx=20, pady=16)
        right = tk.Frame(self.window, bg=WHITE)
        right.pack(side="left", fill="both", expand=True)
        tk.Frame(right, background=GOLD, height=5).pack(fill="x")
        if self.review_mode:
            tk.Label(right, text="Development review — actions are disabled", bg="#fff8e7", fg="#725516",
                     font=("Segoe UI", 8), padx=12, pady=3).pack(fill="x")
        self.footer = ttk.Label(right, text="LIC Install Manager  •  Local installation management",
                                style="Status.TLabel")
        self.footer.pack(side="bottom", fill="x")
        viewport = ttk.Frame(right)
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, background=WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=(38, 30, 38, 20))
        content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>",
                          lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda event: self.canvas.itemconfigure(content_window, width=event.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda event: self.canvas.yview_scroll(int(-event.delta / 120), "units"))

    def _navigation(self, sections):
        for child in self.nav.winfo_children():
            child.destroy()
        for section in sections:
            button = tk.Button(self.nav, text=section, command=lambda value=section: self.show_page(value),
                               bg=NAVY, fg="#d8e4ec", activebackground=NAVY_ACTIVE, activeforeground=WHITE,
                               relief="flat", bd=0, anchor="w", padx=20, pady=9,
                               font=("Segoe UI", 10), cursor="hand2")
            button.pack(fill="x")
            self.nav_buttons[section] = button

    def clear(self):
        for child in self.content.winfo_children():
            child.destroy()

    def show_page(self, page: str):
        self.current_page = page
        self.canvas.yview_moveto(0)
        for name, button in self.nav_buttons.items():
            button.configure(bg=NAVY_ACTIVE if name == page else NAVY,
                             fg=WHITE if name == page else "#d8e4ec")
        self.clear()
        method = getattr(self, "page_" + page.lower().replace(" ", "_").replace("&", "and"))
        method()

    def title(self, heading: str, description: str):
        ttk.Label(self.content, text=heading, style="PageTitle.TLabel", wraplength=900,
                  justify="left").pack(anchor="w")
        ttk.Label(self.content, text=description, style="Body.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(6, 22))

    def card(self, title: str, body: str, *, status: str | None = None, action=None):
        frame = ttk.Frame(self.content, style="Card.TFrame", padding=14)
        frame.pack(fill="x", pady=5)
        top = ttk.Frame(frame, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=title, style="CardTitle.TLabel").pack(side="left")
        if status:
            ttk.Label(top, text=status, style="CardBody.TLabel", foreground=GREEN).pack(side="right")
        ttk.Label(frame, text=body, style="CardBody.TLabel", wraplength=720,
                  justify="left").pack(anchor="w", pady=(5, 0))
        if action:
            ttk.Button(frame, text="Details", command=action).pack(anchor="e", pady=(8, 0))
        return frame

    def info(self, parent, text: str):
        label = tk.Label(parent, text="ⓘ", bg=parent.cget("background") if isinstance(parent, tk.Frame) else WHITE,
                         fg=BLUE, font=("Segoe UI Symbol", 11), cursor="hand2")
        label.pack(side="left", padx=(7, 0))
        Tooltip(label, text)
        return label

    def page_install_and_update(self):
        ready = product_ready(self.components, self.component_facts)
        if ready:
            self.title("✓ LoRA Image Curator is ready", "All required components are installed and verified.")
            ttk.Button(self.content, text="Launch LoRA Image Curator", style="Accent.TButton",
                       command=self.launch_lic,
                       state="disabled" if self.review_mode or not active_launch_contract(self.record, self.root)
                       else "normal").pack(anchor="w", pady=(0, 18))
        else:
            remaining = sum(1 for item in self.components if item.required_for_readiness
                            and not self.component_facts[item.component_id].verified)
            self.title("Setup required", f"{remaining} required component still needs to be installed."
                       if remaining == 1 else f"{remaining} required components still need to be installed.")
        ttk.Label(self.content, text="CORE FUNCTIONALITY — Required", style="Section.TLabel").pack(anchor="w", pady=(0, 5))
        for definition in (item for item in self.components if item.tier == "core"):
            self._render_component_card(definition)
        ttk.Label(self.content, text="OPTIONAL FEATURES", style="Section.TLabel").pack(anchor="w", pady=(20, 5))
        ttk.Label(self.content, text="Install or connect only the features you want. Optional features do not affect core readiness.",
                  style="Muted.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(0, 5))
        for definition in (item for item in self.components if item.tier == "optional"):
            self._render_component_card(definition)

    def _render_component_card(self, definition):
        facts = self.component_facts[definition.component_id]
        frame = ttk.Frame(self.content, style="Card.TFrame", padding=14)
        frame.pack(fill="x", pady=5)
        heading = ttk.Frame(frame, style="Card.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text=definition.capability, style="CardTitle.TLabel").pack(side="left")
        ttk.Label(heading, text="Core" if definition.tier == "core" else "Optional",
                  style="CardBody.TLabel", foreground=BLUE).pack(side="right")
        ttk.Label(frame, text=definition.description, style="CardBody.TLabel", wraplength=850,
                  justify="left").pack(anchor="w", pady=(4, 7))
        metadata = [("Provider", definition.provider), ("Downloaded from", definition.source_name)]
        if definition.component_id != "lic-core" and facts.selected_path and not facts.verified:
            try:
                selected = Path(facts.resource_path or facts.selected_path)
                summary = operation_download_summary(self.delivery, self.root, definition.component_id, selected)
                metadata.extend((("Model files", "Verified locally"),
                                 ("Required libraries", "Not installed"),
                                 ("Download required", human_size(summary["download_bytes"]) +
                                  (" — files already cached" if summary["download_bytes"] == 0 else ""))))
            except (OSError, ValueError, KeyError):
                pass
        if definition.component_id == "lic-core":
            label, value = component_download_disclosure(definition, self.plan)
            metadata.append((label, value))
        elif definition.component_id == "face-analysis":
            try:
                summary = operation_download_summary(self.delivery, self.root, definition.component_id)
                label, value = component_download_disclosure(definition, summary)
                metadata.append((label, value))
            except (OSError, ValueError, KeyError):
                metadata.append(("Download size", "Approximately " + human_size(definition.estimated_bytes)))
        elif definition.estimated_bytes is not None:
            if facts.verified and facts.selected_path:
                metadata.append(("Download required", "None — compatible files already found"))
            elif definition.component_id == "florence-captioning" and self.model_evidence and self.model_evidence.get("reusable"):
                metadata.append(("Model download", "None — compatible model files already found"))
                metadata.append(("Runtime download", "Required only if you choose Install"))
            else:
                metadata.append(("Download size", "Approximately " + human_size(definition.estimated_bytes)))
        for label, value in metadata:
            row = ttk.Frame(frame, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label + ":", style="Field.TLabel", width=18).pack(side="left", anchor="n")
            ttk.Label(row, text=value, style="CardBody.TLabel", wraplength=650,
                      justify="left").pack(side="left", fill="x", expand=True)
        if definition.third_party:
            third = ttk.Frame(frame, style="Card.TFrame")
            third.pack(fill="x", pady=(5, 2))
            ttk.Label(third, text="THIRD-PARTY DOWNLOAD", style="ThirdParty.TLabel").pack(side="left")
            self.info(third, "This component comes from the named provider/source. Exact URLs, versions, hashes and terms are in Details.")
        if definition.component_id == "lic-core":
            identity_editable = identity_controls_editable(facts.phase)
            self._card_path_control(frame, "Application location", self.application_path, self.choose_application,
                                    "Select a parent folder. LoRA Image Curator will be created inside it.",
                                    editable=identity_editable)
            if not identity_editable:
                ttk.Label(frame, text="Locations cannot be changed while Core setup is active.",
                          style="CardBody.TLabel", foreground=AMBER).pack(anchor="w", pady=(4, 0))
            if self.recovery and self.recovery.blocked:
                self._render_recovery_choices(frame)
        elif definition.selector_type != "none":
            variable = (self.model_path if definition.component_id == "florence-captioning" else
                        self.component_paths[definition.component_id])
            contract = picker_contract(definition)
            command = lambda item=definition: self.choose_component_path(item)
            self._card_path_control(frame, contract["label"],
                                    variable, command, contract["hint"],
                                    editable=identity_controls_editable(facts.phase),
                                    commit=lambda item=definition: self.commit_component_path(item))
        status = tk.StringVar(value=component_status_text(definition, facts))
        ttk.Label(frame, textvariable=status, style="CardBody.TLabel", wraplength=850,
                  justify="left", foreground=GREEN if facts.verified else (RED if facts.phase in
                  {ComponentPhase.ERROR, ComponentPhase.INCOMPATIBLE, ComponentPhase.REPAIR_REQUIRED} else INK)).pack(anchor="w", pady=(9, 3))
        progress = progress_presentation(facts)
        bar = ttk.Progressbar(frame, maximum=100, mode=progress["mode"], value=progress["value"],
                              style="Complete.Horizontal.TProgressbar" if progress["value"] >= 100 else "Horizontal.TProgressbar")
        bar.pack(fill="x", pady=(0, 8))
        if progress["mode"] == "indeterminate" and progress["active"]:
            bar.start(12)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x")
        primary = self._primary_action_for(definition, facts)
        primary_button = None
        if primary:
            primary_button = ttk.Button(actions, text=primary_label(definition, facts, primary), style="Accent.TButton",
                                        command=lambda item=definition: self.component_primary_action(item),
                                        state="disabled" if self.review_mode or facts.phase == ComponentPhase.CANCELING else "normal")
            primary_button.pack(side="left")
        if facts.selected_path:
            ttk.Button(actions, text="Go to directory", command=lambda item=definition: self.open_component_directory(item),
                       state="disabled" if self.review_mode else "normal").pack(side="left", padx=(8, 0))
        recovery = self.component_recoveries.get(definition.component_id)
        if recovery and recovery.status == "cancelled":
            ttk.Button(actions, text="Discard cancelled attempt",
                       command=lambda item=definition: self.discard_component_attempt(item),
                       state="disabled" if self.review_mode else "normal").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Help", command=lambda anchor=definition.help_anchor: self.show_help(anchor)).pack(side="right")
        ttk.Button(actions, text="Details", command=lambda item=definition: self.show_component_details(item)).pack(side="right", padx=(0, 8))
        self.component_widgets[definition.component_id] = {"status": status, "progress": bar,
                                                            "primary": primary_button}

    def _update_component_card(self, component_id: str) -> None:
        """Update an already-rendered card without rebuilding the page or moving scroll."""
        widgets = self.component_widgets.get(component_id)
        definition = self.component_by_id.get(component_id)
        if not widgets or definition is None:
            return
        facts = self.component_facts[component_id]
        widgets["status"].set(component_status_text(definition, facts))
        progress = progress_presentation(facts)
        bar = widgets["progress"]
        bar.stop()
        bar.configure(mode=progress["mode"], value=progress["value"],
                      style="Complete.Horizontal.TProgressbar" if progress["value"] >= 100 else "Horizontal.TProgressbar")
        if progress["mode"] == "indeterminate" and progress["active"]:
            bar.start(12)
        button = widgets.get("primary")
        if button is not None:
            action = self._primary_action_for(definition, facts)
            button.configure(text=primary_label(definition, facts, action),
                             state="disabled" if self.review_mode or not action or facts.phase == ComponentPhase.CANCELING else "normal")

    def _card_path_control(self, parent, title, variable, command, hint="", *, editable=True, commit=None):
        ttk.Label(parent, text=title, style="Field.TLabel").pack(anchor="w", pady=(7, 2))
        if hint:
            ttk.Label(parent, text=hint, style="CardBody.TLabel", wraplength=850,
                      justify="left").pack(anchor="w", pady=(0, 3))
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=variable,
                          state="normal" if editable else "readonly")
        entry.pack(side="left", fill="x", expand=True)
        if editable and commit:
            entry.bind("<Return>", lambda _event: commit() or "break")
        ttk.Button(row, text="Browse…", command=command,
                   state="disabled" if self.review_mode or not editable else "normal").pack(side="left", padx=(8, 0))

    def _render_recovery_choices(self, parent):
        recovery = self.recovery
        panel = ttk.Frame(parent, style="Card.TFrame", padding=(0, 8, 0, 2))
        panel.pack(fill="x")
        ttk.Label(panel, text="SETUP LOCATION CHANGED", style="Field.TLabel",
                  foreground=AMBER).pack(anchor="w")
        locations = [("Application location", recovery.recorded_install_root,
                      recovery.current_install_root)]
        if recovery.model_identity_bound:
            locations.append(("Model location", recovery.recorded_model_root,
                              recovery.current_model_root))
        for label, recorded, current in locations:
            if recorded == current:
                continue
            ttk.Label(panel, text=f"Recorded {label}: {recorded}", style="CardBody.TLabel",
                      wraplength=850, justify="left").pack(anchor="w", pady=(3, 0))
            ttk.Label(panel, text=f"Current {label}: {current}", style="CardBody.TLabel",
                      wraplength=850, justify="left").pack(anchor="w")
        actions = ttk.Frame(panel, style="Card.TFrame")
        actions.pack(fill="x", pady=(7, 0))
        state = "disabled" if self.review_mode else "normal"
        ttk.Button(actions, text="Restore recorded location",
                   command=self.restore_recorded_locations, state=state).pack(side="left")
        ttk.Button(actions, text="Begin a new installation",
                   command=self.begin_new_installation, state=state).pack(side="left", padx=(8, 0))

    def _refresh_recovery_state(self):
        if self.recovery_journal_path is None:
            return
        try:
            recovery = inspect_bootstrap_recovery(
                self.recovery_journal_path,
                current_install_root=Path(self.application_path.get()),
                current_model_root=Path(self.model_path.get()),
            )
        except (OSError, ValueError, TypeError, KeyError):
            self.recovery = None
            self.component_facts["lic-core"] = ComponentFacts(
                ComponentPhase.REPAIR_REQUIRED,
                detail="Setup records could not be read safely.")
            return
        self.recovery = recovery
        if recovery:
            self.component_facts["lic-core"] = ComponentFacts(
                ComponentPhase.PARTIAL,
                resumable=recovery.resumable,
                completed_bytes=recovery.completed_steps,
                total_bytes=recovery.total_steps or None,
                detail=recovery.summary,
                recovery_blocked=recovery.blocked,
            )

    def _refresh_florence_recovery(self):
        try:
            recovery = inspect_florence_recovery(
                self.delivery, Path(self.application_path.get()), Path(self.model_path.get()))
        except (OSError, ValueError, TypeError, KeyError):
            recovery = None
            self.component_facts["florence-captioning"] = ComponentFacts(
                ComponentPhase.REPAIR_REQUIRED,
                detail="Florence setup records could not be read safely. Repair may reacquire the disclosed approved files.")
        self.florence_recovery = recovery
        if recovery:
            self.component_facts["florence-captioning"] = ComponentFacts(
                ComponentPhase.PARTIAL,
                resumable=recovery.resumable,
                completed_bytes=recovery.completed_steps,
                total_bytes=recovery.total_steps or None,
                detail=recovery.summary,
                recovery_blocked=recovery.blocked,
            )

    def restore_recorded_locations(self):
        if not self.recovery:
            return
        application, models = restored_recovery_locations(self.recovery)
        self.application_path.set(str(application))
        if self.recovery.model_identity_bound:
            self.model_path.set(str(models))
        try:
            self.model_evidence = inspect_model_storage(self.delivery, models)
        except (OSError, ValueError):
            self.model_evidence = None
        self._refresh_recovery_state()
        self.show_page("Install & Update")

    def begin_new_installation(self):
        parent = filedialog.askdirectory(title="Choose a parent folder for a new LoRA Image Curator installation")
        if not parent:
            return
        candidate = Path(parent) / "LoRA Image Curator"
        try:
            if self.recovery:
                candidate = validate_new_recovery_target(candidate, self.recovery)
            validate_root(candidate, delivery=self.delivery, resume=False)
        except (OSError, ValueError) as error:
            messagebox.showerror("Choose a new installation location", str(error), parent=self.window)
            return
        self.application_path.set(str(candidate))
        self.model_path.set(str(default_model_root(candidate)))
        self.root = candidate
        self.recovery = None
        self.recovery_journal_path = None
        self.model_evidence = None
        self.component_facts["lic-core"] = ComponentFacts()
        self.show_page("Install & Update")

    def show_component_details(self, definition):
        facts = self.component_facts[definition.component_id]
        storage = (self.model_path.get() if definition.component_id == "florence-captioning" else
                   self.component_paths.get(definition.component_id, tk.StringVar(value="")).get())
        plan = self.plan if definition.component_id == "lic-core" else None
        if definition.component_id == "face-analysis":
            try:
                plan = operation_download_summary(self.delivery, self.root, definition.component_id)
            except (OSError, ValueError, KeyError):
                pass
        details = component_detail_contract(definition, facts, storage, plan)
        DetailsDialog(self.window, details["title"], details["summary"], details["sections"], details["advanced"])

    def show_help(self, anchor: str):
        self.help_anchor = anchor
        self.show_page("Help")

    def choose_component_path(self, definition):
        contract = picker_contract(definition)
        initial = self._picker_initial_directory(definition)
        if definition.selector_type == "file":
            selected = filedialog.askopenfilename(title=contract["title"], filetypes=contract["filetypes"],
                                                  initialdir=initial)
        else:
            selected = filedialog.askdirectory(title=contract["title"], initialdir=initial)
        if not selected:
            return
        self._commit_component_path(definition, Path(selected))

    def commit_component_path(self, definition):
        """Commit typed/pasted provider folders with the exact Browse validation path."""
        variable = self.model_path if definition.component_id == "florence-captioning" else self.component_paths[definition.component_id]
        value = variable.get().strip()
        if value:
            self._commit_component_path(definition, Path(value))
        self.window.focus_set()

    def _commit_component_path(self, definition, selected_root: Path):
        variable = (self.model_path if definition.component_id == "florence-captioning" else
                    self.component_paths[definition.component_id])
        selected = str(selected_root)
        try:
            if definition.component_id in {"florence-captioning", "body-analysis", "face-analysis", "video-extraction"}:
                candidates = discover_provider_candidates(self.delivery, definition, selected_root)
                if not candidates:
                    raise ValueError(discovery_message(definition, selected_root.resolve()))
                if len(candidates) > 1:
                    options = "\n".join(f"{index + 1}. {item.provider_root}" for index, item in enumerate(candidates))
                    choice = simpledialog.askinteger("Choose provider folder",
                                                     f"Found {len(candidates)} valid provider folders. Choose one:\n\n{options}",
                                                     parent=self.window, minvalue=1, maxvalue=len(candidates))
                    if choice is None:
                        return
                    candidate = candidates[choice - 1]
                else:
                    candidate = candidates[0]
                variable.set(str(candidate.provider_root))
            else:
                candidate = None
                variable.set(selected)
            if definition.component_id == "florence-captioning":
                self.model_evidence = inspect_model_storage(self.delivery, candidate.resource_path)
                expected = int(self.model_evidence.get("expected_bytes") or 0)
                facts = ComponentFacts(ComponentPhase.PARTIAL, verified=False,
                                       completed_bytes=expected, total_bytes=expected or None,
                                       selected_path=str(candidate.provider_root), resource_path=str(candidate.resource_path),
                                       detail=("Compatible Florence model files found. No model download is required. "
                                               "Install required libraries to add and verify the optional feature."))
            else:
                facts = validate_existing_selection(definition, candidate.resource_path if candidate else Path(selected))
        except (OSError, ValueError) as error:
            facts = ComponentFacts(ComponentPhase.INCOMPATIBLE, selected_path=str(selected_root),
                                   detail=f"The selected files could not be validated: {error}")
        self.component_facts[definition.component_id] = facts
        if (definition.component_id in OPTIONAL_PROVIDER_IDS and
                facts.phase in {ComponentPhase.PARTIAL, ComponentPhase.INSTALLED}):
            # Only a validation-accepted provider root becomes a user preference.
            appdata = getattr(self, "lic_appdata", Path(os.environ.get("APPDATA", Path.home() / ".config")))
            write_provider_location(appdata, definition.component_id,
                                    Path(facts.selected_path or selected_root))
        if (facts.phase in {ComponentPhase.PARTIAL, ComponentPhase.INSTALLED} and
                getattr(self, "record", None) and
                getattr(self, "record_existing_component", None) is not None):
            try:
                self.record_existing_component(definition.component_id, self.root,
                                               Path(facts.resource_path or facts.selected_path or selected_root), facts)
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.component_facts[definition.component_id] = ComponentFacts(
                    ComponentPhase.ERROR, selected_path=selected,
                    detail=(f"The selected {definition.capability} files were verified, but the manager could not "
                            "save that choice. The files were not changed. Try again or review Details."),
                    diagnostic=f"{type(error).__name__}: {error}")
        if getattr(self, "current_page", None) == "Install & Update":
            self._update_component_card(definition.component_id)
        else:
            self.show_page("Install & Update")

    def choose_face_location(self):
        """Commit LIC's shared path only after an explicit Manager action."""
        selected = filedialog.askdirectory(title="Choose Face Analysis model location",
                                           initialdir=self._picker_initial_directory(
                                               self.component_by_id["face-analysis"]))
        if not selected:
            return
        target = Path(selected).resolve()
        if not messagebox.askyesno("Use this Face Analysis location?",
                                   f"LoRA Image Curator and this Manager will use:\n\n{target}\n\n"
                                   "Models may be missing until you install them.", parent=self.window):
            return
        self.component_paths["face-analysis"].set(str(target))
        self.component_facts["face-analysis"] = ComponentFacts(
            ComponentPhase.NOT_INSTALLED, selected_path=str(target),
            detail="Face Analysis will use this folder only after its managed installation validates the models.")
        self.show_page("Install & Update")

    def _picker_initial_directory(self, definition):
        value = (self.model_path.get() if definition.component_id == "florence-captioning" else
                 self.component_paths[definition.component_id].get())
        if value:
            path = Path(value)
            return str(path if path.is_dir() else path.parent)
        return str(Path.home())

    def discard_component_attempt(self, definition) -> None:
        """Archive only a cancelled operation after an explicit customer action."""
        try:
            archived = discard_cancelled_attempt(self.root, definition.component_id)
        except (OSError, ValueError) as error:
            messagebox.showerror("Discard cancelled attempt", str(error), parent=self.window)
            return
        self.component_recoveries.pop(definition.component_id, None)
        facts = self.component_facts[definition.component_id]
        facts.detail = ("The cancelled setup record was preserved for diagnostics. You can now start a new "
                        "explicit provider setup attempt.")
        if getattr(self, "current_page", None) == "Install & Update":
            self._update_component_card(definition.component_id)
        else:
            self.show_page("Install & Update")

    def _offer_unfinished_file_choice(self, component_id: str) -> None:
        """Offer cleanup only for explicit, journal-owned unvalidated temporary paths."""
        root = getattr(self, "root", None)
        if root is None:
            return
        journal_path = (Path(root) / "State/operations/bootstrap.json" if component_id == "lic-core" else
                        Path(root) / "State/operations/florence.json" if component_id == "florence-captioning" else
                        Path(root) / f"State/operations/component-{component_id}.json")
        if not journal_path.is_file():
            return
        try:
            journal = OperationJournal.load(journal_path)
            leftovers = unfinished_acquisitions(journal)
        except (OSError, ValueError, KeyError, TypeError):
            return
        if not leftovers:
            return
        delete = messagebox.askyesno(
            "Installation canceled",
            "Some unfinished files from this installation attempt have not been validated.\n\n"
            "Verified downloads are kept automatically so they can be reused later.\n\n"
            "Choose Yes to delete unfinished files, or No to keep unfinished files.",
            parent=self.window)
        if delete:
            deleted = delete_unfinished_acquisitions(journal)
            detail = f"Installation canceled. Deleted {len(deleted)} unfinished unvalidated file(s)."
        else:
            journal.add_cleanup_action("keep-unvalidated-acquisition-files", performed=False)
            detail = "Installation canceled. Unfinished unvalidated files were kept; verified downloads remain reusable."
        facts = self.component_facts.get(component_id)
        if facts is not None:
            facts.detail = detail

    def open_component_directory(self, definition):
        selected = Path(self.component_facts[definition.component_id].selected_path)
        target = selected if selected.is_dir() else selected.parent
        if not target.is_dir():
            messagebox.showerror("Folder unavailable", "The selected folder is no longer available.", parent=self.window)
            return
        os.startfile(target)

    def component_primary_action(self, definition):
        if definition.component_id == "lic-core" and self.recovery_journal_path is not None:
            self._refresh_recovery_state()
            if self.recovery and self.recovery.blocked:
                self.show_page("Install & Update")
                return
        if definition.component_id == "florence-captioning":
            self._refresh_florence_recovery()
        elif definition.component_id != "lic-core" and definition.managed_install:
            variable = self.component_paths.get(definition.component_id)
            selected = Path(variable.get()) if variable is not None and variable.get() else None
            recovery = self._load_component_recovery(definition.component_id, selected)
            if recovery is not None:
                self.component_recoveries[definition.component_id] = recovery
        facts = self.component_facts[definition.component_id]
        action = self._primary_action_for(definition, facts)
        if action == ComponentAction.USE_EXISTING and definition.selector_type != "none":
            self.choose_component_path(definition)
            return
        if action in {ComponentAction.CANCEL, ComponentAction.CANCEL_QUEUE}:
            result = self.operation_queue.cancel(definition.component_id)
            if result == "canceling":
                facts.phase, facts.detail = ComponentPhase.CANCELING, "Canceling at the next safe boundary…"
            elif result == "queue-canceled":
                facts.phase, facts.detail = ComponentPhase.NOT_INSTALLED, "Queued operation canceled."
            self.show_page("Install & Update")
            return
        if action == ComponentAction.CHECK_UPDATES:
            facts.detail = "No newer manager-approved compatible release is currently available."
            self.show_page("Install & Update")
            return
        if not definition.managed_install:
            self.choose_component_path(definition)
            return
        if definition.component_id != "lic-core":
            missing = [dependency for dependency in definition.dependencies
                       if not self.component_facts[dependency].verified]
            if missing:
                facts.detail = "Install and verify LoRA Image Curator Core before installing this optional capability."
                self.show_page("Install & Update")
                return
        request, start_now = self.operation_queue.submit(definition.component_id, action.value)
        if not start_now:
            facts.phase, facts.detail = ComponentPhase.QUEUED, "Queued behind the active component operation."
            self.show_page("Install & Update")
            return
        if definition.component_id == "lic-core":
            self._start_core_operation(
                request, resume=action in {ComponentAction.RESUME, ComponentAction.REPAIR,
                                           ComponentAction.UPDATE})
        else:
            self._start_optional_operation(
                definition, request, resume=action == ComponentAction.RESUME)

    def _start_optional_operation(self, definition, request, *, resume: bool):
        component_id = definition.component_id
        app = Path(self.application_path.get())
        facts = self.component_facts[component_id]
        selected = (Path(facts.resource_path or self.model_path.get()) if component_id == "florence-captioning" else
                    Path(facts.resource_path or self.component_paths[component_id].get())
                    if self.component_paths.get(component_id) and (facts.resource_path or self.component_paths[component_id].get())
                    else None)
        try:
            if component_id == "florence-captioning":
                evidence = inspect_model_storage(self.delivery, selected)
                if evidence["status"] in {"incompatible", "ambiguous"}:
                    raise ValueError(evidence["message"])
                if evidence["status"] == "missing":
                    validate_model_root(selected)
            if self.install_component is None:
                raise RuntimeError("Managed component installation is unavailable in this build")
        except Exception as error:
            self.component_facts[component_id] = ComponentFacts(ComponentPhase.ERROR, detail=str(error))
            self.operation_queue.complete(component_id)
            self.show_page("Install & Update")
            return
        facts.phase = ComponentPhase.PREPARING
        facts.detail = ("Plan: verify Core, acquire and install any missing required libraries, then validate "
                        "provider readiness. Verified local resource files will be reused without downloading.")
        self.busy = True
        self.show_page("Install & Update")

        def worker():
            try:
                record = self.install_component(
                    component_id, app, selected, resume,
                    lambda event: self.events.put(("component-progress", component_id, event)),
                    request.token.requested)
                self.events.put(("component-installed", component_id, record))
            except AcquisitionCancelled as error:
                self.events.put(("component-canceled", component_id, str(error)))
            except Exception as error:
                self.events.put(("component-failed", component_id, str(error)))
        threading.Thread(target=worker, daemon=False).start()

    def _start_core_operation(self, request, *, resume: bool):
        app, models = Path(self.application_path.get()), Path(self.model_path.get())
        if resume and self.recovery_journal_path is not None:
            self._refresh_recovery_state()
            if self.recovery and self.recovery.blocked:
                self.operation_queue.complete("lic-core")
                self.show_page("Install & Update")
                return
        try:
            validate_root(app, delivery=self.delivery, resume=resume)
            if resume and self.recovery and self.recovery.model_identity_bound:
                validate_model_root(models)
                evidence = inspect_model_storage(self.delivery, models)
                if evidence["status"] == "incompatible":
                    raise ValueError(evidence["message"])
        except Exception as error:
            missing_target = isinstance(error, FileNotFoundError) and "previous installation folder" in str(error)
            self.recovery = None
            self.recovery_journal_path = None if missing_target else self.recovery_journal_path
            self.component_facts["lic-core"] = ComponentFacts(
                ComponentPhase.NOT_INSTALLED if missing_target else ComponentPhase.ERROR,
                detail=("The previous installation folder no longer exists and cannot be resumed. "
                        "Start setup again; verified downloads remain available for reuse."
                        if missing_target else str(error)))
            self.operation_queue.complete("lic-core")
            self.show_page("Install & Update")
            return
        facts = self.component_facts["lic-core"]
        facts.phase, facts.detail = ComponentPhase.PREPARING, "Preparing installation…"
        self.root = app
        self.recovery_journal_path = app / "State/operations/bootstrap.json"
        self.busy = True
        self.show_page("Install & Update")
        def worker():
            try:
                journal = app / "State/operations/bootstrap.json"
                ready = resume and journal.is_file() and json.loads(journal.read_text(encoding="utf-8")).get("status") == "succeeded"
                if not ready:
                    self.prepare(app, models, resume,
                                 lambda event: self.events.put(("component-progress", "lic-core", event)),
                                 request.token.requested)
                if request.token.requested():
                    raise AcquisitionCancelled("Cancellation requested before activation")
                record = self.activate(app, models, self.start_menu.get(), self.desktop.get())
                self.events.put(("component-installed", "lic-core", record))
            except AcquisitionCancelled as error:
                self.events.put(("component-canceled", "lic-core", str(error)))
            except BootstrapIdentityMismatch as error:
                self.events.put(("component-preflight-blocked", "lic-core", str(error)))
            except Exception as error:
                self.events.put(("component-failed", "lic-core", str(error)))
        threading.Thread(target=worker, daemon=False).start()

    def page_overview(self):
        if self.mode == "installed":
            self.title(PRODUCT_NAME, "Installed and ready to use.")
            row = ttk.Frame(self.content)
            row.pack(fill="x", pady=(0, 18))
            ttk.Button(row, text="Launch LoRA Image Curator", style="Accent.TButton",
                       command=self.launch_lic, state="disabled" if self.review_mode else "normal").pack(side="left")
            self.card("Installation status", "Application files and AI models are managed automatically.",
                      status="Ready")
            self.card("Application location", str(self.root))
            self.card("AI model location", str(self.model_path.get()))
            return
        self.title("Install LoRA Image Curator",
                   "Create and manage image-training datasets with local AI-assisted analysis.")
        ttk.Label(self.content, text="Required functionality", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        descriptions = {
            "catalog-workspace": "Browse, organize, review, caption and export image collections.",
            "captioning": "Generate useful image descriptions locally.",
        }
        for capability in lic_capabilities():
            self.card(capability.name, descriptions.get(capability.capability_id, capability.summary),
                      status="Included", action=lambda item=capability: self.show_capability_details(item))
        self.card("Local AI-assisted analysis",
                  "Everything needed for local image analysis is included. No administrator access is needed.",
                  status="Included")
        row = ttk.Frame(self.content)
        row.pack(anchor="e", pady=(18, 0))
        ttk.Button(row, text="Continue to Features", style="Accent.TButton",
                   command=lambda: self.show_page("Features")).pack()

    def show_capability_details(self, capability):
        details = capability_detail_contract(capability, self.plan, Path(self.application_path.get()),
                                             Path(self.model_path.get()))
        DetailsDialog(self.window, details["title"], details["summary"],
                      details["sections"], details["advanced"])

    def page_features(self):
        self.title("Features", "See what is included with LoRA Image Curator.")
        frame = ttk.Frame(self.content, style="Card.TFrame", padding=18)
        frame.pack(fill="x")
        ttk.Label(frame, text="All currently supported features are included.",
                  style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Additional features can be added here when available.",
                  style="CardBody.TLabel", wraplength=700, justify="left").pack(anchor="w", pady=(7, 0))
        if self.mode == "first-run":
            ttk.Button(self.content, text="Continue to Storage", style="Accent.TButton",
                       command=lambda: self.show_page("Storage")).pack(anchor="e", pady=(22, 0))

    def path_control(self, title: str, variable, command, info_text: str | None = None,
                     *, editable: bool = True):
        header = ttk.Frame(self.content)
        header.pack(fill="x", pady=(10, 4))
        ttk.Label(header, text=title, style="Section.TLabel").pack(side="left")
        if info_text:
            self.info(header, info_text)
        row = ttk.Frame(self.content)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=variable,
                  state="normal" if editable else "readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=command,
                   state="normal" if editable else "disabled").pack(side="left", padx=(10, 0))

    def choose_application(self):
        if self.component_facts["lic-core"].phase in IDENTITY_LOCKED_PHASES:
            return
        parent = filedialog.askdirectory(title="Choose a parent folder for LoRA Image Curator")
        if parent:
            self.application_path.set(str(Path(parent) / "LoRA Image Curator"))
            self._refresh_recovery_state()
            self.show_page("Install & Update")

    def choose_models(self):
        if self.component_facts["lic-core"].phase in IDENTITY_LOCKED_PHASES:
            return
        current = Path(self.model_path.get())
        selected = filedialog.askdirectory(
            title="Select existing Florence model folder or parent Hugging Face directory",
            initialdir=str(current if current.is_dir() else current.parent))
        if selected:
            self.model_path.set(selected)
            try:
                self.model_evidence = inspect_model_storage(self.delivery, Path(selected))
            except (OSError, ValueError) as error:
                self.model_evidence = {"status": "invalid", "reusable": False,
                                       "message": str(error), "reason": str(error)}
            self._refresh_recovery_state()
            self.show_page("Install & Update")

    def check_models(self):
        self.model_status.set("Checking the selected model location…")
        self.window.update_idletasks()
        try:
            evidence = inspect_model_storage(self.delivery, Path(self.model_path.get()))
            self.model_evidence = evidence
            self.model_status.set(model_status_presentation(evidence))
            self.model_status_label.configure(foreground=GREEN if evidence["reusable"] else
                                               (RED if evidence["status"] == "incompatible" else AMBER))
        except Exception as error:
            self.model_evidence = {"status": "invalid", "reusable": False, "message": friendly_error(error),
                                   "reason": str(error)}
            self.model_status.set(str(error))
            self.model_status_label.configure(foreground=RED)

    def page_storage(self):
        self.title("Storage", "Choose where to keep the application and its AI model files.")
        self.path_control("Application location", self.application_path, self.choose_application,
                          "The manager, LIC application, private runtime, state and logs live under this location.")
        self.path_control("AI model location", self.model_path, self.choose_models,
                          "Models can be shared independently and do not have to move with the application.")
        self.model_status = tk.StringVar(value="Check this location to see whether a compatible model is already available.")
        status_row = ttk.Frame(self.content)
        status_row.pack(fill="x", pady=(10, 0))
        self.model_status_label = ttk.Label(status_row, textvariable=self.model_status, style="Body.TLabel",
                                            wraplength=600)
        self.model_status_label.pack(side="left", fill="x", expand=True)
        ttk.Button(status_row, text="Check model location", command=self.check_models).pack(side="right")
        self.card("What happens next",
                  "The manager keeps supporting files with the application. Your chosen AI model location stays separate.")
        if self.mode == "first-run":
            ttk.Button(self.content, text="Review installation", style="Accent.TButton",
                       command=lambda: self.show_page("Review")).pack(anchor="e", pady=(18, 0))
        else:
            ttk.Button(self.content, text="Move the application", style="Accent.TButton",
                       command=lambda: self.show_page("Move Installation")).pack(anchor="e", pady=(18, 0))

    def _review(self):
        review = storage_review(self.delivery, Path(self.application_path.get()), Path(self.model_path.get()))
        self.model_evidence = review["model"]
        return review

    def page_review(self):
        self.title("Review installation", "Confirm these choices. You can return to any section before installing.")
        try:
            review = self._review()
        except Exception as error:
            ttk.Label(self.content, text=str(error), foreground=RED, wraplength=720).pack(anchor="w")
            ttk.Button(self.content, text="Return to Storage", command=lambda: self.show_page("Storage")).pack(anchor="w", pady=12)
            return
        self.card("LoRA Image Curator", "Catalog and curate image sets with local AI-assisted captioning.", status="Required")
        rows = [("Application location", review["application_root"]),
                ("AI model location", review["model_root"]),
                ("Existing model", model_status_presentation(review["model"], concise=True)),
                ("Expected download", "About " + human_size(review["download_bytes"])),
                ("Disk requirement", "At least 12 GiB free for the managed NVIDIA profile"),
                ("Start Menu shortcut", "Create" if self.start_menu.get() else "Do not create"),
                ("Desktop shortcut", "Create" if self.desktop.get() else "Do not create")]
        card = ttk.Frame(self.content, style="Card.TFrame", padding=18)
        card.pack(fill="x", pady=7)
        for label, value in rows:
            line = ttk.Frame(card, style="Card.TFrame")
            line.pack(fill="x", pady=4)
            ttk.Label(line, text=label, style="Field.TLabel", width=22).pack(side="left", anchor="n")
            shown = compact_path(value) if label in {"Application location", "AI model location"} else value
            value_label = ttk.Label(line, text=shown, style="CardBody.TLabel", wraplength=560,
                                    justify="left")
            value_label.pack(side="left", fill="x", expand=True)
            if shown != value:
                Tooltip(value_label, value)
        options = ttk.Frame(self.content)
        options.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(options, text="Create Start Menu shortcuts", variable=self.start_menu).pack(anchor="w")
        ttk.Checkbutton(options, text="Create Desktop shortcut", variable=self.desktop).pack(anchor="w")
        actions = ttk.Frame(self.content)
        actions.pack(fill="x", pady=(18, 0))
        ttk.Button(actions, text="Back to Storage", command=lambda: self.show_page("Storage")).pack(side="left")
        ttk.Button(actions, text="Install LoRA Image Curator", style="Accent.TButton",
                   state="disabled" if self.review_mode else "normal", command=self.begin_install).pack(side="right")

    def begin_install(self):
        if self.busy:
            return
        if self.recovery_journal_path is not None:
            self._refresh_recovery_state()
            if self.recovery and self.recovery.blocked:
                self.show_page("Install & Update")
                return
        app = Path(self.application_path.get())
        models = Path(self.model_path.get())
        resume = (app / "State/operations/bootstrap.json").is_file()
        try:
            validate_root(app, delivery=self.delivery, resume=resume)
            validate_model_root(models)
            evidence = inspect_model_storage(self.delivery, models)
            if evidence["status"] == "incompatible":
                raise ValueError(evidence["message"])
        except Exception as error:
            messagebox.showerror("Review storage", str(error), parent=self.window)
            return
        self.busy = True
        self.show_progress("Installing LoRA Image Curator", "Preparing the managed installation…")
        def worker():
            try:
                journal = app / "State/operations/bootstrap.json"
                ready = resume and journal.is_file() and json.loads(journal.read_text(encoding="utf-8")).get("status") == "succeeded"
                if not ready:
                    self.prepare(app, models, resume, lambda event: self.events.put(("progress", event)))
                self.activate(app, models, self.start_menu.get(), self.desktop.get())
                self.events.put(("installed", {"message": "LoRA Image Curator is installed and ready to use.",
                                                "terminal": "success"}))
            except Exception as error:
                self.events.put(("failed", {"message": friendly_error(error), "detail": str(error), "terminal": "error"}))
        threading.Thread(target=worker, daemon=False).start()

    def show_progress(self, heading: str, message: str):
        self.clear()
        self.title(heading, "You can leave this window open while the manager works.")
        self.progress_message = tk.StringVar(value=message)
        self.progress_phase = tk.StringVar(value="")
        ttk.Label(self.content, textvariable=self.progress_message, style="Section.TLabel",
                  wraplength=720).pack(anchor="w", pady=(12, 8))
        ttk.Label(self.content, textvariable=self.progress_phase, style="Muted.TLabel").pack(anchor="w")
        self.progress = ttk.Progressbar(self.content, maximum=100, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 18))
        self.progress.start(12)
        ttk.Label(self.content, text="Detailed records are stored under Logs, State and Validation in the installation folder.",
                  style="Muted.TLabel", wraplength=700).pack(anchor="w")

    def show_completion(self, record: dict | None = None):
        self.busy = False
        if record:
            self.record = record
        self.clear()
        self.title("LoRA Image Curator is ready", "Installation completed successfully.")
        self.card("Ready to use", "LoRA Image Curator will run from its verified private environment and can use the managed model offline.",
                  status="Installed")
        actions = ttk.Frame(self.content)
        actions.pack(anchor="w", pady=(18, 0))
        ttk.Button(actions, text="Launch LoRA Image Curator", style="Accent.TButton",
                   command=self.launch_lic).pack(side="left")
        ttk.Button(actions, text="Open Install Manager", command=self.to_installed).pack(side="left", padx=(10, 0))

    def to_installed(self):
        path = Path(self.application_path.get()) / "State/installations/lic-lite.json"
        if path.is_file():
            self.record = json.loads(path.read_text(encoding="utf-8"))
        self.mode = "installed"
        self.root = Path(self.record["root"]) if self.record else Path(self.application_path.get())
        self.sections = list(INSTALLED_SECTIONS)
        self.nav_buttons = {}
        self._navigation(self.sections)
        self.show_page("Install & Update")

    def launch_lic(self):
        if self.review_mode:
            return
        try:
            self.launch(self.root)
        except Exception as error:
            messagebox.showerror("LoRA Image Curator could not be launched", friendly_error(error) +
                                 "\n\nOpen Help for diagnostic locations.", parent=self.window)

    def page_move_installation(self):
        if not self.record:
            self.title("Move Installation", "Move becomes available after LoRA Image Curator is installed and verified.")
            self.card("No managed installation yet",
                      "Install core functionality first. Existing historical installations are never moved automatically.")
            return
        self.title("Move Installation", "Set up and check a new copy before changing where LoRA Image Curator opens from.")
        self.current_application = tk.StringVar(value=str(self.root))
        self.path_control("Current application location", self.current_application, lambda: None,
                          editable=False)
        self.move_destination = tk.StringVar(value=str(self.root.parent / "LoRA Image Curator — Moved"))
        self.path_control("New application location", self.move_destination, self.choose_move_destination)
        current_models = Path(self.record.get("model_root") or default_model_root(self.root))
        self.move_model_choice = tk.StringVar(value="keep")
        ttk.Label(self.content, text="AI model handling", style="Section.TLabel").pack(anchor="w", pady=(18, 4))
        ttk.Radiobutton(self.content, text=f"Keep AI models at {current_models}", variable=self.move_model_choice,
                        value="keep", command=self.update_move_plan).pack(anchor="w")
        ttk.Radiobutton(self.content, text="Copy verified models to a new location", variable=self.move_model_choice,
                        value="copy", command=self.update_move_plan).pack(anchor="w", pady=(4, 0))
        self.move_model_destination = tk.StringVar(value=str(current_models))
        row = ttk.Frame(self.content)
        row.pack(fill="x", pady=(6, 0))
        ttk.Entry(row, textvariable=self.move_model_destination).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.choose_move_models).pack(side="left", padx=(10, 0))
        self.move_summary = tk.StringVar()
        card = ttk.Frame(self.content, style="Card.TFrame", padding=18)
        card.pack(fill="x", pady=(18, 0))
        ttk.Label(card, text="Move plan", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=self.move_summary, style="CardBody.TLabel", wraplength=680,
                  justify="left").pack(anchor="w", pady=(8, 0))
        self.update_move_plan()
        ttk.Button(self.content, text="Move Installation", style="Accent.TButton",
                   state="disabled" if self.review_mode else "normal", command=self.begin_move).pack(anchor="e", pady=(18, 0))

    def choose_move_destination(self):
        parent = filedialog.askdirectory(title="Choose the new parent folder")
        if parent:
            self.move_destination.set(str(Path(parent) / "LoRA Image Curator"))
            self.update_move_plan()

    def choose_move_models(self):
        selected = filedialog.askdirectory(title="Choose the new AI model folder")
        if selected:
            self.move_model_destination.set(selected)
            self.move_model_choice.set("copy")
            self.update_move_plan()

    def update_move_plan(self):
        if not hasattr(self, "move_summary"):
            return
        models = Path(self.record.get("model_root") or default_model_root(self.root))
        copy = self.move_model_choice.get() == "copy"
        model_text = (f"Copy and verify AI models to {self.move_model_destination.get()}" if copy else
                      f"Keep AI models at {models}")
        self.move_summary.set(f"LoRA Image Curator\n{self.root}\n→ {self.move_destination.get()}\n\nAI models\n{model_text}\n\n"
                              "The manager checks the new copy before using it. Your current installation stays available.")

    def begin_move(self):
        if self.busy:
            return
        destination = Path(self.move_destination.get())
        copy = self.move_model_choice.get() == "copy"
        new_models = Path(self.move_model_destination.get()) if copy else None
        try:
            models = Path(self.record.get("model_root") or default_model_root(self.root))
            move_plan(self.root, destination, models, copy_models=copy, new_model_root=new_models)
        except Exception as error:
            messagebox.showerror("Review move", str(error), parent=self.window)
            return
        self.busy = True
        self.show_progress("Moving LoRA Image Curator", "Preparing the verified destination…")
        def worker():
            try:
                moved = self.move(self.root, destination, copy, new_models,
                                  lambda event: self.events.put(("progress", event)))
                self.events.put(("moved", {"message": "Move completed. The previous installation was retained.",
                                           "terminal": "success", "record": moved}))
            except Exception as error:
                self.events.put(("failed", {"message": "LoRA Image Curator could not be moved. Your existing installation is still available.",
                                             "detail": str(error), "terminal": "error"}))
        threading.Thread(target=worker, daemon=False).start()

    def page_repair_and_update(self):
        self.title("Repair & Update", "Maintenance actions will appear only when their safety checks are available.")
        self.card("Current installation", "LoRA Image Curator is installed and ready to open.", status="Ready")
        self.card("Repair", "The verified repair workflow is not part of this GUI review candidate.", status="Unavailable")
        self.card("Updates", "Automatic update checking is disabled. No update service is configured.", status="Not configured")

    def page_help(self):
        self.title("Help", "Practical guidance for installing and managing LoRA Image Curator.")
        about = ttk.Frame(self.content, style="Card.TFrame", padding=12)
        about.pack(fill="x", pady=(0, 10))
        ttk.Label(about, text="ABOUT", style="Field.TLabel").pack(anchor="w")
        ttk.Label(about, text=(f"{MANAGER_NAME}\n{PRODUCT_EXPANDED_NAME}\n"
                               f"Version {PRODUCT_VERSION}\nDependency profile {DEPENDENCY_PROFILE_ID}"),
                  style="CardBody.TLabel", justify="left").pack(anchor="w", pady=(4, 0))
        selected_heading = HELP_ANCHORS.get(self.help_anchor, "")
        if selected_heading:
            ttk.Label(self.content, text=f"Showing help for: {selected_heading}",
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(self.content, text="Jump to", style="Field.TLabel").pack(anchor="w")
        toc = ttk.Frame(self.content)
        toc.pack(fill="x", pady=(3, 12))
        topics = [
            ("About LoRA Image Curator", "LoRA Image Curator organizes, reviews, captions and exports local image datasets."),
            ("How installation works", "Install & Update shows current state and starts only the selected component. Core setup creates a private Python environment, verifies every approved artifact and activates only after readiness checks pass."),
            ("Core functionality", "Core includes the application, private Python runtime and only the packages required for catalog, review, editing, readiness and export."),
            ("Optional features and providers", "Florence captioning, Face Analysis with OpenCV YuNet + SFace, Google MediaPipe body analysis and FFmpeg video extraction are independent. They never block Core readiness."),
            ("Third-party downloads", "Nothing is downloaded by startup, status, Browse, Details or Help. A clearly labeled Install, Update, Repair or same-plan Resume action is required before acquisition."),
            ("Storage locations", "New installations default to Local AppData. Application and large AI-model locations remain independently selectable."),
            ("Using existing models", "Browse validates identity, exact revision/hash where available, completeness and compatibility. Unknown files are left unchanged."),
            ("Updates", "Only manager-approved compatible releases can become Update actions. A newer upstream release alone is never enough."),
            ("Interrupted downloads", "Restart or select Resume. Completed verified work is rechecked; generic partial transfers restart because trustworthy byte-range continuation is not yet established."),
            ("Cancellation and resume", "Cancel requests stop at the next safe boundary. Verified downloads are kept for reuse. If an attempt leaves unvalidated unfinished files, you can keep them or delete only those files. Partial work is never published as installed."),
            ("Moving an installation", "Move creates and validates a new installation, rebuilds path-bound components and keeps the old copy. AI models stay where they are unless copying is explicitly selected."),
            ("Repair and recovery", "Missing or damaged managed files become Repair required. Optional damage does not make core LoRA Image Curator unavailable."),
            ("Managing model files", "Go to directory opens the selected location. Some models may be shared; deleting them manually can disable a feature, which the next check will report."),
            ("Third-party licenses and notices", "Each Details view identifies practical terms and restrictions. InsightFace pretrained weights have separate, restricted terms from the MIT-licensed code."),
            ("Troubleshooting", f"Review logs under {self.root / 'Logs'} and operation records under {self.root / 'State/operations'}."),
        ]
        for heading, _body in topics[:6]:
            ttk.Button(toc, text=heading, command=lambda target=heading: self._jump_help(target)).pack(side="left", padx=(0, 6), pady=2)
        for heading, body in topics:
            frame = ttk.Frame(self.content)
            frame.pack(fill="x", pady=5)
            label = ttk.Label(frame, text=heading, style="Section.TLabel")
            label.pack(anchor="w")
            if selected_heading == heading:
                self.window.after_idle(lambda widget=label: self.canvas.yview_moveto(
                    max(0.0, min(1.0, widget.winfo_y() / max(1, self.content.winfo_reqheight())))))
            ttk.Label(frame, text=body, style="Body.TLabel", wraplength=850,
                      justify="left").pack(anchor="w", fill="x", pady=(2, 0))
            if heading == "Third-party downloads":
                links = ttk.Frame(frame)
                links.pack(anchor="w", pady=(5, 0))
                for name, url in (("Python.org", "https://www.python.org/"), ("PyPI", "https://pypi.org/"),
                                  ("PyTorch", "https://download.pytorch.org/"), ("Hugging Face", "https://huggingface.co/"),
                                  ("MediaPipe", "https://developers.google.com/mediapipe/"),
                                  ("OpenCV Zoo", "https://github.com/opencv/opencv_zoo"), ("FFmpeg", "https://ffmpeg.org/")):
                    ttk.Button(links, text=f"Open {name}", command=lambda address=url: open_official_source(address)).pack(side="left", padx=(0, 5), pady=2)

    def _jump_help(self, heading):
        for child in self.content.winfo_children():
            labels = child.winfo_children() if isinstance(child, ttk.Frame) else ()
            if labels and isinstance(labels[0], ttk.Label) and labels[0].cget("text") == heading:
                self.canvas.yview_moveto(max(0.0, min(1.0, child.winfo_y() / max(1, self.content.winfo_reqheight()))))
                return

    def poll(self):
        while not self.events.empty():
            item = self.events.get_nowait()
            if len(item) == 3 and item[0].startswith("component-"):
                kind, component_id, event = item
                facts = self.component_facts[component_id]
                if kind == "component-progress":
                    view = progress_view(event)
                    if event.get("kind") == "download":
                        facts.phase = ComponentPhase.DOWNLOADING
                        facts.completed_bytes = int(event.get("downloaded_bytes", 0))
                        facts.total_bytes = event.get("total_bytes")
                    else:
                        message = view["message"].casefold()
                        facts.phase = (ComponentPhase.VERIFYING if "verif" in message or "testing" in message
                                       else ComponentPhase.INSTALLING if "install" in message or "creating" in message
                                       else ComponentPhase.PREPARING)
                    facts.detail = view["message"]
                elif kind == "component-installed":
                    facts.phase, facts.verified, facts.detail = (ComponentPhase.INSTALLED, True,
                                                                  "✓ Installed and verified")
                    if component_id == "lic-core":
                        self.record, self.mode, self.root = event, "installed", Path(event["root"])
                        # A succeeded bootstrap journal is historical evidence, not
                        # an active Resume condition after activation completes.
                        self.recovery = None
                        self.recovery_journal_path = None
                    elif component_id == "florence-captioning":
                        facts.selected_path = str(event.get("snapshot") or facts.selected_path)
                        self.model_evidence = inspect_model_storage(self.delivery, Path(event["model_root"]))
                        write_provider_location(self.lic_appdata, component_id, Path(event["model_root"]))
                    else:
                        facts.selected_path = str(provider_root_from_resource(
                            component_id, event.get("resource") or facts.selected_path))
                        if component_id in self.component_paths:
                            self.component_paths[component_id].set(facts.selected_path)
                        if component_id in OPTIONAL_PROVIDER_IDS and facts.selected_path:
                            write_provider_location(self.lic_appdata, component_id,
                                                    Path(facts.selected_path))
                    self.busy = False
                    self.operation_queue.complete(component_id)
                elif kind == "component-canceled":
                    self.busy = False
                    self.operation_queue.complete(component_id)
                    if component_id == "florence-captioning":
                        self._refresh_florence_recovery()
                    elif component_id != "lic-core":
                        variable = self.component_paths.get(component_id)
                        selected = Path(variable.get()) if variable is not None and variable.get() else None
                        recovery = self._load_component_recovery(component_id, selected)
                        if recovery:
                            self.component_recoveries[component_id] = recovery
                            facts.phase, facts.resumable = ComponentPhase.PARTIAL, recovery.resumable
                            facts.recovery_blocked, facts.detail = recovery.blocked, recovery.summary
                    else:
                        self._refresh_recovery_state()
                    self._offer_unfinished_file_choice(component_id)
                elif kind == "component-preflight-blocked":
                    self.busy = False
                    self.operation_queue.complete(component_id)
                    self._refresh_recovery_state()
                    if not self.recovery:
                        self.component_facts[component_id] = ComponentFacts(
                            ComponentPhase.PARTIAL, recovery_blocked=True,
                            detail=("This interrupted setup belongs to a different approved installer "
                                    "profile. Begin a new installation in a different empty location."))
                elif kind == "component-failed":
                    self.busy = False
                    self.operation_queue.complete(component_id)
                    if component_id == "florence-captioning":
                        self._refresh_florence_recovery()
                    elif component_id != "lic-core":
                        variable = self.component_paths.get(component_id)
                        selected = Path(variable.get()) if variable is not None and variable.get() else None
                        recovery = self._load_component_recovery(component_id, selected)
                        if recovery:
                            self.component_recoveries[component_id] = recovery
                            facts.phase, facts.resumable = ComponentPhase.PARTIAL, recovery.resumable
                            facts.recovery_blocked, facts.detail = recovery.blocked, recovery.summary
                    else:
                        self._refresh_recovery_state()
                    recovered = (self.florence_recovery if component_id == "florence-captioning" else
                                 self.recovery if component_id == "lic-core" else
                                 self.component_recoveries.get(component_id))
                    if component_id == "lic-core" and recovered and recovered.status == "succeeded":
                        # Bootstrap succeeded, but the separate activation transaction
                        # failed.  Do not hide that failure behind a generic Resume card.
                        self.component_facts[component_id] = ComponentFacts(
                            ComponentPhase.PARTIAL, resumable=True,
                            detail=("Core files are ready, but activation could not finish. "
                                    "Resume setup retries activation. Details contains diagnostic information."))
                    elif not recovered:
                        self.component_facts[component_id] = ComponentFacts(
                            ComponentPhase.ERROR,
                            detail=component_failure_message(self.component_by_id[component_id], str(event)),
                            diagnostic=str(event))
                if self.current_page == "Install & Update":
                    if kind == "component-progress":
                        self._update_component_card(component_id)
                    else:
                        # Completion/failure changes available actions and may
                        # need structural recovery controls; progress never does.
                        self.show_page("Install & Update")
                continue
            kind, event = item
            view = progress_view(event)
            if hasattr(self, "progress_message"):
                self.progress_message.set(view["message"])
                if view["step"] and view["total_steps"]:
                    self.progress_phase.set(f"Step {view['step']} of {view['total_steps']}")
                if view["mode"] == "determinate":
                    self.progress.stop(); self.progress.configure(mode="determinate", value=view["value"])
            if kind == "installed":
                self.progress.stop(); self.show_completion()
            elif kind == "moved":
                self.progress.stop(); self.record = event["record"]
                self.root = Path(self.record["root"]); self.application_path.set(str(self.root))
                self.model_path.set(self.record["model_root"]); self.show_completion(self.record)
            elif kind == "failed":
                self.busy = False
                if hasattr(self, "progress"):
                    self.progress.stop()
                messagebox.showerror("Operation stopped safely", event["message"] +
                                     "\n\nUse Help or the Logs folder for technical details.", parent=self.window)
        self.window.after(100, self.poll)

    def _write_probe(self, path: Path):
        contract = first_run_contract(str(self.root), model_root=self.model_path.get(),
                                      start_menu=self.start_menu.get(), desktop=self.desktop.get())
        evidence = {"visible": bool(self.window.winfo_viewable()), "title": self.window.title(),
                    "product_version": PRODUCT_VERSION, "dependency_profile": DEPENDENCY_PROFILE_ID,
                    "mode": self.mode, "navigation": self.sections, "active_section": self.current_page,
                    "persistent_left_navigation": True, "consent_default": False,
                    "installation_started": self.busy, "ux_contract": contract,
                    "details_available": True, "help_available": "Help" in self.sections,
                    "model_storage_visible": True, "independent_storage": True,
                    "review_mode": self.review_mode, "quiet": self.quiet,
                    "component_states": {key: value.phase.value for key, value in self.component_facts.items()},
                    "component_actions": {item.component_id:
                                          component_action(item, self.component_facts[item.component_id]).value
                                          for item in self.components},
                    "core_identity_controls_editable": identity_controls_editable(
                        self.component_facts["lic-core"].phase),
                    "recovery": ({"status": self.recovery.status,
                                  "blocked": self.recovery.blocked,
                                  "resumable": self.recovery.resumable,
                                  "recorded_install_root": str(self.recovery.recorded_install_root),
                                  "recorded_model_root": str(self.recovery.recorded_model_root),
                                  "current_install_root": str(self.recovery.current_install_root),
                                  "current_model_root": str(self.recovery.current_model_root)}
                                 if self.recovery else None),
                    "product_ready": product_ready(self.components, self.component_facts),
                    "screen": {"width": self.window.winfo_screenwidth(),
                               "height": self.window.winfo_screenheight(),
                               "tk_scaling": float(self.window.tk.call("tk", "scaling"))},
                    "window": {"width": self.window.winfo_width(), "height": self.window.winfo_height(),
                               "geometry": self.window.geometry()}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        self.window.destroy()

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Keep this window open while the current step finishes. "
                                "If interrupted, reopen the manager to inspect recovery.", parent=self.window)
        else:
            self.window.destroy()

    def run(self):
        self.window.mainloop()


def show_first_run(delivery: Path, root: Path, prepare, activate, launch, **kwargs):
    ManagerShell(delivery, root, prepare=prepare, activate=activate, launch=launch, **kwargs).run()


def show_installed(delivery: Path, root: Path, launch, move, *, record: dict | None = None, **kwargs):
    if record is None:
        record = json.loads((root / "State/installations/lic-lite.json").read_text(encoding="utf-8"))
    ManagerShell(delivery, root, launch=launch, move=move, installed_record=record, **kwargs).run()
