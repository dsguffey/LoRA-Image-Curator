"""Read and atomically persist local LoRA Image Curator preferences.

Settings are stored under
``%APPDATA%\\LoRAImageCurator\\settings.json``. Version 0.25.0 intentionally
starts a clean per-user application-data home after the product rename; image
catalogs remain independent and backward compatible.

Catalog data is never stored here. Each selected output folder owns its
portable ``dataset_tools.db`` catalog.
"""

from __future__ import annotations

import json
import os

from dataclasses import asdict, dataclass, field
from pathlib import Path

from app_identity import APP_DATA_DIRECTORY_NAME
from ui_theme import DEFAULT_THEME_KEY, normalize_theme_key


APPLICATION_FOLDER_NAME = APP_DATA_DIRECTORY_NAME
SETTINGS_FILENAME = "settings.json"


@dataclass(slots=True)
class AppSettings:
    """Represent GUI preferences saved between sessions."""

    # This records only which informational notice the user continued past.
    # It is not telemetry and does not purport to expand any upstream license.
    third_party_notice_version: str = ""
    remember_paths: bool = True
    last_input_folder: str = ""
    last_output_folder: str = ""
    include_triage: bool = True
    reuse_stored_analysis: bool = True
    appearance_theme: str = DEFAULT_THEME_KEY
    # Folder traversal is explicit per workflow.  Providers and metadata-only
    # catalog import can therefore evolve independently without silently
    # changing one another's source scope.
    catalog_import_include_subfolders: bool = True
    caption_include_subfolders: bool = True
    face_include_subfolders: bool = True
    face_reference_include_subfolders: bool = True

    # File-removal settings deliberately keep reversible quarantine separate
    # from operating-system trash.  The path is user-controlled because large
    # datasets may not fit comfortably on the system drive.
    quarantine_directory: str = ""
    # Conservative default: deleting a physical file leaves its catalog record
    # and analysis metadata available for recovery or later cleanup.
    delete_catalog_record_with_file: bool = False
    # Retained for settings-file compatibility. v0.27.2 applies confirmation
    # according to the active browser page size instead of this legacy toggle.
    confirm_trash_deletion: bool = True

    # Optional body-analysis provider settings.  MediaPipe Pose Landmarker is
    # the first vetted provider, but the persisted fields describe application
    # capabilities rather than hard-coding GUI behavior to one implementation.
    body_provider_key: str = "mediapipe_pose"
    body_model_path: str = ""
    body_detection_threshold: float = 0.50
    body_landmark_visibility_threshold: float = 0.50
    body_full_body_threshold_percent: int = 70

    # No application telemetry is implemented.  This permission remains false
    # by default and is intentionally separate from explicit model downloads.
    # A provider may only consult it after presenting its own named disclosure.
    allow_provider_telemetry: bool = False

    # Video-source settings store only local workflow conveniences.  FFmpeg is
    # never bundled or downloaded by LoRA Image Curator; ``video_ffmpeg_path`` is a
    # user-approved executable location validated before every extraction.
    video_ffmpeg_path: str = ""
    video_last_source: str = ""
    video_last_destination: str = ""
    video_sampling_mode: str = "interval"
    video_interval_seconds: float = 2.0
    video_scene_threshold: float = 0.35
    video_max_frames: int = 500
    video_output_format: str = "jpg"

    # Optional face-provider settings. ``face_identity_name`` is retained as an
    # internal compatibility name; the GUI presents it as the training set
    # keyword. Model weights and identity embeddings remain outside this JSON.
    run_face_analysis: bool = False
    face_identity_name: str = ""
    face_reference_folder: str = ""
    face_model_name: str = "buffalo_l"
    face_model_root: str = ""
    face_similarity_threshold: float = 0.48
    face_detection_threshold: float = 0.50

    # Catalog Browser preferences are intentionally compact and local-only.
    browser_sort: str = "Filename (A–Z)"
    browser_filter: str = "All images"
    browser_last_catalog: str = ""
    # Search text is not persisted as general window state.  History is a
    # separate, user-controllable convenience with an explicit clear action.
    browser_search_history_enabled: bool = True
    browser_search_history_max: int = 50
    browser_search_history: list[str] = field(default_factory=list)
    # Thumbnail pages stay bounded because Windows/Tk can clip widgets at very
    # large canvas coordinates.  The user may trade density for shorter pages,
    # but the safe upper boundary remains 100 images.
    browser_images_per_page: int = 100

    # Quality thresholds are explicit user preferences rather than activity
    # history. Measurements remain in the selected catalog; these values only
    # control how the interface interprets them.
    readiness_profile_key: str = "flux_character_lora"
    quality_blur_threshold: float = 100.0
    quality_duplicate_similarity_percent: int = 96
    overlay_coverage_threshold_percent: int = 5
    overlay_spatial_mode: str = "either"
    run_quality_analysis: bool = True

    # Dataset export preferences are local workflow conveniences. They never
    # contain catalog records, provider output, or a copy of training data.
    export_last_directory: str = ""
    export_profile_key: str = "flux_lora"
    export_copy_images: bool = True
    export_create_sidecars: bool = True
    export_create_manifest: bool = True
    export_create_readme: bool = True
    export_collision_policy: str = "rename"
    export_custom_include_trigger: bool = True
    export_custom_include_manual_tags: bool = True
    export_custom_include_ai_tags: bool = True
    export_custom_include_raw_caption: bool = False


def get_settings_directory() -> Path:
    """Return the per-user directory used for settings and logs."""
    appdata_value = os.environ.get("APPDATA")

    if appdata_value:
        base_directory = Path(appdata_value)
    else:
        base_directory = Path.home() / ".config"

    return base_directory / APPLICATION_FOLDER_NAME


def get_settings_path() -> Path:
    """Return the complete settings-file path."""
    return get_settings_directory() / SETTINGS_FILENAME


def get_default_quarantine_directory() -> Path:
    """Return the default reversible quarantine folder for this user.

    The directory is not created merely by displaying Settings.  It is created
    only when the user performs a confirmed quarantine action.
    """
    return get_settings_directory() / "quarantine"


def get_default_body_model_path() -> Path:
    """Return the recommended local path for MediaPipe's vetted full model."""
    return (
        get_settings_directory()
        / "models"
        / "body"
        / "pose_landmarker_full.task"
    )


def load_settings() -> AppSettings:
    """
    Load settings safely.

    Older settings files remain compatible because every newer field has a
    default value.
    """
    settings_path = get_settings_path()

    if not settings_path.exists():
        return AppSettings()

    try:
        with settings_path.open("r", encoding="utf-8") as settings_file:
            raw_data = json.load(settings_file)

        if not isinstance(raw_data, dict):
            return AppSettings()

        history_max = max(1, min(200, int(raw_data.get("browser_search_history_max", 50))))
        raw_history = raw_data.get("browser_search_history", [])
        history: list[str] = []
        if isinstance(raw_history, list):
            seen: set[str] = set()
            for item in raw_history:
                query = " ".join(str(item).split()).strip()
                key = query.casefold()
                if query and key not in seen:
                    seen.add(key)
                    history.append(query)
                if len(history) >= history_max:
                    break

        return AppSettings(
            third_party_notice_version=str(
                raw_data.get("third_party_notice_version", "")
            ),
            remember_paths=bool(
                raw_data.get("remember_paths", True)
            ),
            last_input_folder=str(
                raw_data.get("last_input_folder", "")
            ),
            last_output_folder=str(
                raw_data.get("last_output_folder", "")
            ),
            include_triage=bool(
                raw_data.get("include_triage", True)
            ),
            reuse_stored_analysis=bool(
                raw_data.get("reuse_stored_analysis", True)
            ),
            appearance_theme=normalize_theme_key(raw_data.get("appearance_theme")),
            catalog_import_include_subfolders=bool(
                raw_data.get("catalog_import_include_subfolders", True)
            ),
            caption_include_subfolders=bool(
                raw_data.get("caption_include_subfolders", True)
            ),
            face_include_subfolders=bool(
                raw_data.get("face_include_subfolders", True)
            ),
            face_reference_include_subfolders=bool(
                raw_data.get("face_reference_include_subfolders", True)
            ),
            quarantine_directory=str(raw_data.get("quarantine_directory", "")),
            delete_catalog_record_with_file=bool(
                raw_data.get("delete_catalog_record_with_file", False)
            ),
            confirm_trash_deletion=bool(
                raw_data.get("confirm_trash_deletion", True)
            ),
            body_provider_key=str(
                raw_data.get("body_provider_key", "mediapipe_pose")
            ),
            body_model_path=str(raw_data.get("body_model_path", "")),
            body_detection_threshold=max(
                0.0,
                min(1.0, float(raw_data.get("body_detection_threshold", 0.50))),
            ),
            body_landmark_visibility_threshold=max(
                0.0,
                min(
                    1.0,
                    float(
                        raw_data.get(
                            "body_landmark_visibility_threshold",
                            0.50,
                        )
                    ),
                ),
            ),
            body_full_body_threshold_percent=max(
                60,
                min(
                    100,
                    int(raw_data.get("body_full_body_threshold_percent", 70)),
                ),
            ),
            allow_provider_telemetry=bool(
                raw_data.get("allow_provider_telemetry", False)
            ),
            video_ffmpeg_path=str(raw_data.get("video_ffmpeg_path", "")),
            video_last_source=str(raw_data.get("video_last_source", "")),
            video_last_destination=str(
                raw_data.get("video_last_destination", "")
            ),
            video_sampling_mode=(
                "scene"
                if str(raw_data.get("video_sampling_mode", "interval")) == "scene"
                else "interval"
            ),
            video_interval_seconds=max(
                0.05,
                min(
                    86_400.0,
                    float(raw_data.get("video_interval_seconds", 2.0)),
                ),
            ),
            video_scene_threshold=max(
                0.01,
                min(
                    1.0,
                    float(raw_data.get("video_scene_threshold", 0.35)),
                ),
            ),
            video_max_frames=max(
                1,
                min(100_000, int(raw_data.get("video_max_frames", 500))),
            ),
            video_output_format=(
                "png"
                if str(raw_data.get("video_output_format", "jpg")) == "png"
                else "jpg"
            ),
            run_face_analysis=bool(
                raw_data.get("run_face_analysis", False)
            ),
            face_identity_name=str(
                raw_data.get("face_identity_name", "")
            ),
            face_reference_folder=str(
                raw_data.get("face_reference_folder", "")
            ),
            face_model_name=str(
                raw_data.get("face_model_name", "buffalo_l")
            ),
            face_model_root=str(
                raw_data.get("face_model_root", "")
            ),
            face_similarity_threshold=float(
                raw_data.get("face_similarity_threshold", 0.48)
            ),
            face_detection_threshold=float(
                raw_data.get("face_detection_threshold", 0.50)
            ),
            browser_sort=str(raw_data.get("browser_sort", "Filename (A–Z)")),
            browser_filter=str(raw_data.get("browser_filter", "All images")),
            browser_last_catalog=str(raw_data.get("browser_last_catalog", "")),
            browser_search_history_enabled=bool(
                raw_data.get("browser_search_history_enabled", True)
            ),
            browser_search_history_max=history_max,
            browser_search_history=history,
            browser_images_per_page=max(
                25,
                min(100, int(raw_data.get("browser_images_per_page", 100))),
            ),
            readiness_profile_key=str(
                raw_data.get("readiness_profile_key", "flux_character_lora")
            ),
            quality_blur_threshold=max(
                0.0,
                min(10000.0, float(raw_data.get("quality_blur_threshold", 100.0))),
            ),
            quality_duplicate_similarity_percent=max(
                96,
                min(
                    100,
                    int(raw_data.get("quality_duplicate_similarity_percent", 96)),
                ),
            ),
            overlay_coverage_threshold_percent=max(
                1,
                min(
                    30,
                    int(raw_data.get("overlay_coverage_threshold_percent", 5)),
                ),
            ),
            overlay_spatial_mode=(
                str(
                    raw_data.get(
                        "overlay_spatial_mode",
                        raw_data.get("text_overlay_spatial_mode", "either"),
                    )
                )
                if str(
                    raw_data.get(
                        "overlay_spatial_mode",
                        raw_data.get("text_overlay_spatial_mode", "either"),
                    )
                )
                in {"none", "face", "body", "either", "both"}
                else "either"
            ),
            run_quality_analysis=bool(
                raw_data.get("run_quality_analysis", True)
            ),
            export_last_directory=str(raw_data.get("export_last_directory", "")),
            export_profile_key=str(raw_data.get("export_profile_key", "flux_lora")),
            export_copy_images=bool(raw_data.get("export_copy_images", True)),
            export_create_sidecars=bool(raw_data.get("export_create_sidecars", True)),
            export_create_manifest=bool(raw_data.get("export_create_manifest", True)),
            export_create_readme=bool(raw_data.get("export_create_readme", True)),
            export_collision_policy=str(raw_data.get("export_collision_policy", "rename")),
            export_custom_include_trigger=bool(
                raw_data.get("export_custom_include_trigger", True)
            ),
            export_custom_include_manual_tags=bool(
                raw_data.get("export_custom_include_manual_tags", True)
            ),
            export_custom_include_ai_tags=bool(
                raw_data.get("export_custom_include_ai_tags", True)
            ),
            export_custom_include_raw_caption=bool(
                raw_data.get("export_custom_include_raw_caption", False)
            ),
        )

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    """Save settings atomically."""
    settings_directory = get_settings_directory()
    settings_directory.mkdir(parents=True, exist_ok=True)

    settings_path = get_settings_path()
    temporary_path = settings_path.with_suffix(".json.tmp")

    with temporary_path.open("w", encoding="utf-8") as settings_file:
        json.dump(
            asdict(settings),
            settings_file,
            indent=4,
            ensure_ascii=False,
        )
        settings_file.write("\n")

    temporary_path.replace(settings_path)
