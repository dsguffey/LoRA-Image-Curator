"""Install MediaPipe and Google's recommended local pose model.

This helper runs only when the user launches it directly.  It prints the exact
package/model sources and asks before each network operation.  It never enables
telemetry and never installs arbitrary provider code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.request

from pathlib import Path

from settings_manager import get_default_body_model_path


MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)


def ask(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().casefold() in {"y", "yes"}


def install_packages() -> None:
    requirements = Path(__file__).with_name("requirements-body.txt")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--requirement",
            str(requirements),
        ],
        check=True,
    )


def download_model(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="pose_landmarker_full-",
        suffix=".task.download",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            MODEL_URL,
            headers={"User-Agent": "LoRA-Image-Curator-model-installer"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with temporary.open("wb") as target:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
        if temporary.stat().st_size < 1_000_000:
            raise RuntimeError(
                "The downloaded file is unexpectedly small and was not installed."
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    destination = get_default_body_model_path()
    print("LoRA Image Curator optional body-analysis setup")
    print()
    print("Package: mediapipe (Google MediaPipe Authors)")
    print("Purpose: local pose/body analysis")
    print("Telemetry: this installer does not enable application or provider telemetry")
    print()
    if ask("Install the vetted Python dependencies from PyPI?"):
        install_packages()
    else:
        print("Package installation skipped.")

    print()
    print("Recommended model: Google MediaPipe Pose Landmarker Full")
    print(f"Source: {MODEL_URL}")
    print(f"Destination: {destination}")
    if destination.exists() and not ask("The model already exists. Replace it?"):
        print("Model download skipped.")
    elif ask("Download the recommended model from Google?"):
        download_model(destination)
        print(f"Model installed: {destination}")
    else:
        print("Model download skipped.")

    print()
    print("Open LoRA Image Curator and use Tools > Check Body Analysis Setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
