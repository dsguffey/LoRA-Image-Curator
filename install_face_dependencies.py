"""
Install optional face-analysis dependencies into LoRA Image Curator's active Python.

This helper chooses an ONNX Runtime GPU range compatible with the CUDA major
version reported by the existing PyTorch installation.  That matters because
ONNX Runtime 1.27 and later use CUDA 13 by default, while many current PyTorch
builds still ship CUDA 12 libraries.

The script installs software libraries only.  It does not download InsightFace
model weights; LoRA Image Curator requests explicit license approval before a model
pack is downloaded on first use.
"""

from __future__ import annotations

import subprocess
import sys

from datetime import datetime
from pathlib import Path


def run_pip(*arguments: str) -> None:
    """Run pip through the exact Python interpreter executing this script."""
    command = [sys.executable, "-m", "pip", *arguments]
    print("\n>", " ".join(command))
    subprocess.run(command, check=True)



def save_environment_snapshot() -> Path:
    """Save the pre-install package list beside the project for recovery."""
    backup_directory = Path(__file__).resolve().parent / "dependency_backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = backup_directory / f"before_face_install_{timestamp}.txt"

    with snapshot_path.open("w", encoding="utf-8") as snapshot_file:
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            check=True,
            stdout=snapshot_file,
            text=True,
        )

    return snapshot_path


def detect_torch_cuda() -> tuple[str, str]:
    """Return the installed PyTorch version and its bundled CUDA version."""
    try:
        import torch
    except Exception as error:
        return "unavailable", f"error: {type(error).__name__}: {error}"

    return str(torch.__version__), str(torch.version.cuda or "CPU-only")


def choose_onnxruntime_requirement(cuda_version: str) -> tuple[str, str]:
    """Choose a documented ORT line that matches PyTorch's CUDA major."""
    if cuda_version.startswith("13"):
        return (
            "onnxruntime-gpu>=1.27,<1.30",
            "CUDA 13-compatible ONNX Runtime",
        )

    if cuda_version.startswith("12"):
        return (
            "onnxruntime-gpu>=1.21,<1.27",
            "CUDA 12-compatible ONNX Runtime (pinned below 1.27)",
        )

    if cuda_version.startswith("11"):
        # Recent CUDA 11 builds are distributed through Microsoft's package
        # feed rather than the default PyPI line.  The user's current system is
        # expected to be CUDA 12+, but this branch keeps older installations
        # functional using the last broadly available compatible release.
        return (
            "onnxruntime-gpu==1.18.1",
            "legacy CUDA 11-compatible ONNX Runtime",
        )

    return (
        "onnxruntime>=1.21,<1.30",
        "CPU ONNX Runtime fallback",
    )


def main() -> int:
    """Install a CUDA-compatible optional face stack with recovery evidence."""
    print("LoRA Image Curator — Face Analysis Dependency Installer")
    print("=" * 56)
    print(f"Python: {sys.executable}")

    try:
        snapshot_path = save_environment_snapshot()
        print(f"Environment backup: {snapshot_path}")
    except Exception as error:
        print(
            "WARNING: Could not save the pre-install package list: "
            f"{type(error).__name__}: {error}"
        )

    torch_version, cuda_version = detect_torch_cuda()
    print(f"PyTorch: {torch_version}")
    print(f"PyTorch CUDA: {cuda_version}")

    ort_requirement, explanation = choose_onnxruntime_requirement(cuda_version)
    print(f"Selected: {ort_requirement}")
    print(f"Reason: {explanation}")

    try:
        run_pip("install", "--upgrade", "pip", "setuptools", "wheel")

        # Having CPU and GPU packages installed together can make provider
        # selection unpredictable, so remove either variant before reinstalling
        # the one chosen for this environment.
        run_pip(
            "uninstall",
            "-y",
            "onnxruntime",
            "onnxruntime-gpu",
        )
        run_pip("install", ort_requirement)
        run_pip("install", "insightface==1.0.1")
        run_pip("check")

    except subprocess.CalledProcessError as error:
        print("\nINSTALLATION FAILED")
        print(f"pip exited with code {error.returncode}.")
        print("The existing Florence environment was not deleted.")
        return int(error.returncode or 1)

    print("\nVerifying imports...")

    try:
        import insightface
        import onnxruntime as ort

        print(f"InsightFace: {getattr(insightface, '__version__', 'unknown')}")
        print(f"ONNX Runtime: {ort.__version__}")
        print("Execution providers:")
        for provider in ort.get_available_providers():
            print(f"  - {provider}")

        if cuda_version not in {"CPU-only"} and "CUDAExecutionProvider" not in (
            ort.get_available_providers()
        ):
            print(
                "\nWARNING: CUDAExecutionProvider is not available. Dataset "
                "Tools can still use CPU face analysis, but run Check Face "
                "Analysis Setup.bat and review the output."
            )

    except Exception as error:
        print("\nPACKAGES INSTALLED, BUT VERIFICATION FAILED")
        print(f"{type(error).__name__}: {error}")
        return 2

    print("\nInstallation complete.")
    print("No model weights were downloaded.")
    print("Start LoRA Image Curator and click Check Setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
