"""Single reviewed profile with an explicit, unqualified CPU-face boundary."""
from __future__ import annotations

import importlib.metadata as metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads(Path(__file__).with_name("profile.json").read_text(encoding="utf-8"))
ORT_NAMES = {"onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"}
CV_NAMES = {"opencv-python", "opencv-contrib-python", "opencv-python-headless", "opencv-contrib-python-headless"}


def inventory() -> dict[str, str]:
    """Read installed distribution versions without importing native providers."""
    return {d.metadata["Name"].lower().replace("_", "-"): d.version for d in metadata.distributions()}


def conflicts(installed: dict[str, str], *, nvidia: bool = False) -> list[str]:
    """Report namespace collisions and forbidden NVIDIA-profile distributions."""
    errors = []
    for label, family in (("ONNX Runtime", ORT_NAMES), ("OpenCV", CV_NAMES)):
        present = sorted(family & installed.keys())
        if len(present) > 1:
            errors.append(f"Duplicate {label} distributions: {', '.join(present)}")
    forbidden = set(PROFILE["forbidden"]) if nvidia else CV_NAMES - {"opencv-contrib-python"}
    for name in sorted(forbidden & installed.keys()):
        errors.append(f"Noncanonical distribution: {name}")
    return errors


def profile_for_cuda(cuda: str | None) -> str:
    """Do not reinterpret an unsupported GPU stack as CPU or force NVIDIA."""
    if not cuda or cuda == "CPU-only":
        return "cpu"
    if cuda.split(".")[0] == "13":
        return PROFILE["profile"]
    raise RuntimeError(f"CUDA {cuda} is outside the reviewed CUDA 13 profile; no packages changed.")
