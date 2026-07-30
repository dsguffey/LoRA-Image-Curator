"""Dependency-light regressions for Milestone 8H video source import."""

from __future__ import annotations

import io
import tempfile

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from catalog_import import CatalogImportOptions, import_catalog_folder
from settings_manager import AppSettings, load_settings, save_settings
from video_extraction import (
    VideoExtractionOptions,
    build_ffmpeg_command,
    discover_ffmpeg,
    probe_ffmpeg,
    run_video_extraction,
)


def _options(root: Path, *, sampling_mode: str = "interval") -> VideoExtractionOptions:
    executable = root / "ffmpeg.exe"
    executable.write_bytes(b"fixture executable")
    source = root / "source video.mp4"
    source.write_bytes(b"fixture video")
    return VideoExtractionOptions(
        ffmpeg_path=executable,
        source_video=source,
        destination_folder=root / "frames with spaces",
        sampling_mode=sampling_mode,  # type: ignore[arg-type]
        interval_seconds=2.5,
        scene_threshold=0.32,
        max_frames=25,
        output_format="jpg",
        filename_prefix="action_scene",
    )


def test_probe_and_discovery_validate_identity() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8h_probe_") as temporary:
        executable = Path(temporary) / "ffmpeg.exe"
        executable.write_bytes(b"fixture")
        successful = SimpleNamespace(
            returncode=0,
            stdout="ffmpeg version 8.0-test Copyright\nconfiguration: fixture\n",
            stderr="",
        )
        with patch("video_extraction.subprocess.run", return_value=successful) as run:
            status = probe_ffmpeg(executable, source="manual")
            assert status.available
            assert status.executable == executable.resolve()
            assert status.version_line.startswith("ffmpeg version 8.0-test")
            assert run.call_args.args[0] == [str(executable.resolve()), "-version"]
            assert run.call_args.kwargs["shell"] is False

            with patch(
                "video_extraction.shutil.which",
                side_effect=lambda value: (
                    str(executable) if value == "ffmpeg" else None
                ),
            ):
                discovered = discover_ffmpeg(str(Path(temporary) / "missing.exe"))
            assert discovered.available
            assert discovered.source == "path"

        impostor = SimpleNamespace(
            returncode=0,
            stdout="Python 3.12.0\n",
            stderr="",
        )
        with patch("video_extraction.subprocess.run", return_value=impostor):
            status = probe_ffmpeg(executable)
        assert not status.available
        assert "did not identify itself" in status.error


def test_command_is_argument_safe_and_supports_both_sampling_modes() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8h_command_") as temporary:
        root = Path(temporary)
        interval = build_ffmpeg_command(_options(root))
        assert interval[0] == str((root / "ffmpeg.exe").resolve())
        assert "fps=1/2.500000" in interval
        assert str((root / "source video.mp4").resolve()) in interval
        assert interval[-2] == "-n"
        assert interval[-1].endswith("action_scene_%06d.jpg")

        scene = build_ffmpeg_command(_options(root, sampling_mode="scene"))
        assert "select=eq(n\\,0)+gt(scene\\,0.320000)" in scene
        assert "-frames:v" in scene
        assert scene[scene.index("-frames:v") + 1] == "25"


class _SuccessfulFakeProcess:
    """Small Popen substitute that writes two valid images immediately."""

    def __init__(self, command, **kwargs) -> None:
        assert kwargs["shell"] is False
        pattern = Path(command[-1])
        for index, color in ((1, "#557799"), (2, "#995577")):
            output = Path(str(pattern).replace("%06d", f"{index:06d}"))
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (640, 480), color).save(output)
        self.stdout = io.StringIO(
            "frame=1\nprogress=continue\nframe=2\nprogress=end\n"
        )
        self.stderr = io.StringIO("")
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def test_extraction_counts_outputs_and_frames_can_enter_a_catalog() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8h_extract_") as temporary:
        root = Path(temporary)
        options = _options(root)
        progress: list[tuple[int, int]] = []
        with patch(
            "video_extraction.subprocess.Popen",
            _SuccessfulFakeProcess,
        ):
            summary = run_video_extraction(
                options,
                progress_callback=lambda completed, maximum: progress.append(
                    (completed, maximum)
                ),
            )
        assert summary.output_count == 2
        assert summary.skipped_existing_files == 0
        assert summary.failed_outputs == 0
        assert progress[-1] == (2, 25)
        assert all(path.exists() for path in summary.output_files)

        database = root / "catalog" / "dataset_tools.db"
        imported = import_catalog_folder(
            CatalogImportOptions(
                source_folder=summary.destination_folder,
                target_database=database,
                mode="create",
                recursive=False,
                create_image_set=True,
                image_set_name="Action frames",
            )
        )
        assert imported.cataloged_files == 2
        assert imported.image_set_image_count == 2

        # A second run with the same prefix must stop before FFmpeg can replace
        # or mix files from the verified first run.
        with patch("video_extraction.subprocess.Popen") as second_process:
            try:
                run_video_extraction(options)
            except FileExistsError as error:
                assert "will not overwrite or mix" in str(error)
            else:
                raise AssertionError("Expected collision-safe extraction refusal.")
        second_process.assert_not_called()


def test_video_preferences_round_trip_without_schema_or_catalog_state() -> None:
    with tempfile.TemporaryDirectory(prefix="dataset_tools_8h_settings_") as temporary:
        appdata = Path(temporary) / "appdata"
        with patch.dict("os.environ", {"APPDATA": str(appdata)}):
            settings = AppSettings(
                video_ffmpeg_path=r"C:\Tools\FFmpeg\bin\ffmpeg.exe",
                video_last_source=r"D:\Video Files\movie.mkv",
                video_last_destination=r"D:\Datasets\movie_frames",
                video_sampling_mode="scene",
                video_interval_seconds=3.5,
                video_scene_threshold=0.28,
                video_max_frames=750,
                video_output_format="png",
            )
            save_settings(settings)
            loaded = load_settings()
        assert loaded.video_ffmpeg_path == settings.video_ffmpeg_path
        assert loaded.video_last_source == settings.video_last_source
        assert loaded.video_last_destination == settings.video_last_destination
        assert loaded.video_sampling_mode == "scene"
        assert loaded.video_interval_seconds == 3.5
        assert loaded.video_scene_threshold == 0.28
        assert loaded.video_max_frames == 750
        assert loaded.video_output_format == "png"


def run() -> None:
    test_probe_and_discovery_validate_identity()
    test_command_is_argument_safe_and_supports_both_sampling_modes()
    test_extraction_counts_outputs_and_frames_can_enter_a_catalog()
    test_video_preferences_round_trip_without_schema_or_catalog_state()
    print(
        "Milestone 8H tests passed: FFmpeg discovery/validation, shell-free "
        "interval and scene commands, collision-safe extraction, staged catalog "
        "handoff, and persistent local video preferences."
    )


if __name__ == "__main__":
    run()
