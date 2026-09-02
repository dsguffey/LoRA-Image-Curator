"""
Install optional face-analysis dependencies into LoRA Image Curator's active Python.

This helper delegates to LIC's reviewed CUDA 13 dependency profile and builds
the explicitly versioned metadata-only InsightFace wheel. CPU-only and other
CUDA installation profiles require separate qualification.

The script installs software libraries only.  It does not download InsightFace
model weights; LoRA Image Curator requests explicit license approval before a model
pack is downloaded only after the user approves the model-specific prompt from
a Face Run command.
"""

from __future__ import annotations

import subprocess
import sys

from lic_dependencies.profile import PROFILE


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
            f"onnxruntime-gpu=={PROFILE['onnxruntime-gpu']}",
            "CUDA 13-compatible ONNX Runtime",
        )

    raise RuntimeError("This installer qualifies CUDA 13 only. CPU/other-CUDA face packaging needs separate review; existing CPU inference is unchanged.")


def main() -> int:
    """Install the reviewed optional face stack without automatic cleanup."""
    print("LoRA Image Curator — Face Analysis Dependency Installer")
    print("=" * 56)
    print(f"Python: {sys.executable}")

    torch_version, cuda_version = detect_torch_cuda()
    print(f"PyTorch: {torch_version}")
    print(f"PyTorch CUDA: {cuda_version}")

    try:
        from lic_dependencies.installer import install_component
        ort_requirement, explanation = choose_onnxruntime_requirement(cuda_version)
        print(f"Selected: {ort_requirement} ({explanation})")
        install_component("face")
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print("\nINSTALLATION FAILED")
        print(str(error))
        return int(getattr(error, "returncode", 1) or 1)

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
                "Tools can still use CPU face analysis. Run Setup and Launch "
                "LoRA Image Curator.bat and choose Check optional face "
                "analysis to review the complete status."
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
