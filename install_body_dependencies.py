"""Install MediaPipe and Google's recommended local pose model.

This helper runs only when the user launches it directly.  It prints the exact
package/model sources and asks before each network operation.  It never enables
telemetry and never installs arbitrary provider code.
"""

from __future__ import annotations

import os
import hashlib
import subprocess
import sys
import tempfile
import urllib.request

from pathlib import Path
from urllib.parse import urlparse

from provider_registry import get_component
from settings_manager import get_default_body_model_path


MODEL_COMPONENT = get_component("mediapipe_pose_full_v1")
MODEL_URL = str(MODEL_COMPONENT["source_url"])
MODEL_SHA256 = str(MODEL_COMPONENT["sha256"])
MODEL_SIZE_BYTES = int(MODEL_COMPONENT["approx_download_bytes"])
MODEL_DOWNLOAD_HOSTS = frozenset(MODEL_COMPONENT["download_hosts"])


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
    """Download, verify, and atomically publish the pinned Google model.

    A failed or interrupted replacement never removes the prior working file.
    The exact versioned URL, host allowlist, byte length, and SHA-256 all come
    from the release registry rather than from a moving provider alias.
    """
    parsed = urlparse(MODEL_URL)
    if parsed.scheme != "https" or parsed.hostname not in MODEL_DOWNLOAD_HOSTS:
        raise RuntimeError("The registered MediaPipe model source is not allowed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="pose_landmarker_full-",
        suffix=".task.partial",
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
            final_url = response.geturl()
            final_host = urlparse(final_url).hostname
            if final_host not in MODEL_DOWNLOAD_HOSTS:
                raise RuntimeError(
                    f"MediaPipe download redirected to an unapproved host: {final_host}"
                )
            digest = hashlib.sha256()
            byte_count = 0
            with temporary.open("wb") as target:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
        if byte_count != MODEL_SIZE_BYTES:
            raise RuntimeError(
                "MediaPipe model size verification failed; the existing model "
                f"was preserved. Expected {MODEL_SIZE_BYTES} bytes, received "
                f"{byte_count}."
            )
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError(
                "MediaPipe model SHA-256 verification failed; the existing "
                "model was preserved."
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
    print(f"Version: {MODEL_COMPONENT['tested_version']}")
    print(f"License: {MODEL_COMPONENT['license']}")
    print(f"Source: {MODEL_URL}")
    print(f"SHA-256: {MODEL_SHA256}")
    print(f"Download size: {MODEL_SIZE_BYTES / (1024 * 1024):.1f} MiB")
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
