"""Console diagnostic for LoRA Image Curator's optional face provider."""

from __future__ import annotations

import sys

from face_analyzer import DEFAULT_MODEL_NAME, inspect_face_setup


def main() -> int:
    """Print a read-only face-provider diagnostic and return a process status."""
    status = inspect_face_setup(DEFAULT_MODEL_NAME, "")

    print("LoRA Image Curator — Face Analysis Setup Check")
    print("=" * 48)
    print(f"Python: {sys.executable}")
    print("Provider: OpenCV YuNet + SFace")
    print(f"OpenCV contrib: {status.opencv_version}")
    print("Execution providers:")

    if status.available_execution_providers:
        for provider in status.available_execution_providers:
            print(f"  - {provider}")
    else:
        print("  - none")

    print(f"Recommended: {status.recommended_execution_provider}")
    print(f"Model folder: {status.model_path}")
    print(f"Qualified pair ready: {'yes' if status.model_installed else 'no'}")
    print("Notes:")

    if status.notes:
        for note in status.notes:
            print(f"  - {note}")
    else:
        print("  - No warnings")

    return 0 if status.opencv_installed and status.model_installed else 1


if __name__ == "__main__":
    raise SystemExit(main())
