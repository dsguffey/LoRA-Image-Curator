"""Report the execution device each bundled analysis provider will use.

This module performs read-only capability inspection.  It never installs
packages, downloads models, or treats a detected GPU as proof that an
individual inference succeeded.  The GUI uses these facts to make automatic
CPU/GPU selection visible before a long run begins; run logs remain the final
record of the provider actually selected at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass

from face_analyzer import inspect_face_setup


@dataclass(slots=True, frozen=True)
class ProviderDeviceStatus:
    """Presentation-ready device labels for the three built-in providers."""

    florence: str
    face: str
    body: str


def inspect_provider_devices(
    *,
    face_model_name: str,
    face_model_root: str,
) -> ProviderDeviceStatus:
    """Inspect PyTorch and ONNX Runtime without loading analysis models."""
    try:
        import torch

        if torch.cuda.is_available():
            florence = (
                "Device: GPU via PyTorch CUDA — "
                f"{torch.cuda.get_device_name(0)}"
            )
        else:
            florence = "Device: CPU — PyTorch CUDA is unavailable"
    except Exception as error:
        florence = f"Device check unavailable: {type(error).__name__}: {error}"

    try:
        face_setup = inspect_face_setup(
            model_name=face_model_name,
            model_root=face_model_root,
        )
        provider = face_setup.recommended_execution_provider
        if provider == "CUDAExecutionProvider":
            face = "Device: GPU via ONNX Runtime CUDAExecutionProvider"
        elif provider == "CPUExecutionProvider":
            face = (
                "Device: CPU via ONNX Runtime — CUDAExecutionProvider "
                "is unavailable"
            )
        else:
            face = "Device: unavailable — run Check Setup for details"
    except Exception as error:
        face = f"Device check unavailable: {type(error).__name__}: {error}"

    # The vetted MediaPipe Tasks Python route used by this Windows desktop
    # release is deliberately kept on its stable CPU path.  GPU delegate
    # availability is platform/backend-specific and is not silently forced.
    body = "Device: CPU via the vetted MediaPipe Python task backend"
    return ProviderDeviceStatus(
        florence=florence,
        face=face,
        body=body,
    )
