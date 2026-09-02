"""Shared installer entry points; no edits to installed package metadata."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .profile import PROFILE, ROOT, conflicts, profile_for_cuda


def target_state(python: str) -> dict:
    """Inspect the target in a fresh process so native modules are never stale."""
    script = """
import sys,json,importlib.metadata as m
d={x.metadata['Name'].lower().replace('_','-'):x.version for x in m.distributions()}
cuda=None
if 'torch' in d:
    import torch
    cuda=torch.version.cuda
print(json.dumps({'venv':sys.prefix!=sys.base_prefix,'packages':d,'cuda':cuda}))
"""
    result = subprocess.run([python, "-I", "-B", "-c", script], check=True,
                            capture_output=True, text=True, timeout=90)
    return json.loads(result.stdout.strip().splitlines()[-1])


def pip_command(python: str, *args: str) -> None:
    """Execute pip via the exact target interpreter with version checks disabled."""
    subprocess.run([python, "-m", "pip", "--disable-pip-version-check", *args], check=True)


def install_component(component: str, *, python: str = sys.executable,
                      profile: str | None = None, candidate: Path | None = None) -> None:
    """Install a reviewed component; CI candidates are explicit and never persisted."""
    state = target_state(python)
    if not state["venv"]:
        raise RuntimeError("LIC dependencies must be installed in a virtual environment")
    selected = profile or profile_for_cuda(state["cuda"])
    if selected not in {"cpu", PROFILE["profile"]}:
        raise ValueError(f"Unknown profile: {selected}")
    if component == "face" and selected == "cpu":
        raise RuntimeError("CPU-only face packaging is not yet qualified. Existing CPU inference remains supported; no packages changed.")
    errors = conflicts(state["packages"], nvidia=selected != "cpu")
    if errors:
        raise RuntimeError("; ".join(errors) + ". Use a fresh environment; automatic destructive cleanup is disabled.")
    if component == "base" and "torch" not in state["packages"]:
        raise RuntimeError("Select/install PyTorch before base dependencies (timm depends on Torchvision)")
    constraints = ROOT / ("constraints-nvidia.txt" if selected != "cpu" else "constraints-lic.txt")
    # Exact tools, not an unbounded upgrade. No dependency removal is performed.
    pip_command(python, "install", "--only-binary=:all:", *PROFILE["build_tools"])
    if component == "face":
        with tempfile.TemporaryDirectory(prefix="lic-face-package-") as temporary:
            folder = Path(temporary)
            if candidate is None:
                subprocess.run([python, "-m", "lic_dependencies.insightface_build", "--output", str(folder)],
                               cwd=ROOT, check=True)
                report = json.loads((folder / "build-metadata.json").read_text())
                wheel = folder / report["wheel"]
            else:
                # --candidate is a developer-only CLI path. It never changes profile.json.
                from .insightface_build import inspect_wheel
                inspect_wheel(candidate)
                wheel = candidate.resolve()
            # Candidate discovery deliberately tests new InsightFace versions against
            # the same stack without overriding the production version constraint.
            constraint_path = constraints
            if candidate is not None:
                filtered = (ROOT / "constraints-lic.txt").read_text().splitlines()
                constraint_path = folder / "candidate-constraints.txt"
                if selected != "cpu":
                    filtered += [f"torch=={PROFILE['torch']}", f"torchvision=={PROFILE['torchvision']}"]
                constraint_path.write_text("\n".join(x for x in filtered if not x.startswith("insightface==")) + "\n")
            pip_command(python, "install", "--only-binary=:all:", "-c", str(constraint_path), str(wheel),
                        f"onnxruntime-gpu=={PROFILE['onnxruntime-gpu']}",
                        f"opencv-contrib-python=={PROFILE['opencv-contrib-python']}")
    elif component in {"base", "body"}:
        filename = "requirements.txt" if component == "base" else "requirements-body.txt"
        pip_command(python, "install", "--only-binary=:all:", "-c", str(constraints), "-r", str(ROOT / filename))
    else:
        raise ValueError(f"Unknown component: {component}")
    errors = conflicts(target_state(python)["packages"], nvidia=selected != "cpu")
    if errors:
        raise RuntimeError("; ".join(errors))
    pip_command(python, "check")


def main() -> None:
    """Developer/CI entry point; explicit profile avoids pretending CPU CI has CUDA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("base", "face", "body"))
    parser.add_argument("--profile", choices=("cpu", PROFILE["profile"]))
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    install_component(args.component, profile=args.profile, candidate=args.candidate)


if __name__ == "__main__":
    main()
