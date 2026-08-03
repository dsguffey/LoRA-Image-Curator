"""Portable Windows setup and launch assistant for the source distribution.

The public GitHub release is Python source, so it needs a Python interpreter and
third-party packages.  This module keeps those implementation details behind a
plain numbered menu: it creates a project-local virtual environment, installs
only user-selected components, reports required and optional readiness, and
always invokes pip through the exact local interpreter it manages.

PyTorch remains an explicit choice because the correct official wheel depends
on the workstation's CPU/NVIDIA driver stack.  The assistant can install the
official CPU build automatically or safely translate a command copied from
PyTorch's official selector so it installs into ``.\venv`` rather than the
user's system Python.  It never downloads model weights or FFmpeg.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from app_identity import APP_NAME, APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_ROOT = PROJECT_ROOT / "venv"
VENV_PYTHON = VENV_ROOT / "Scripts" / "python.exe"
PYTORCH_SELECTOR_URL = "https://pytorch.org/get-started/locally/"

REQUIRED_DISTRIBUTIONS = (
    ("torch", "PyTorch"),
    ("numpy", "NumPy"),
    ("Pillow", "Pillow"),
    ("transformers", "Transformers"),
    ("einops", "Einops"),
    ("timm", "timm"),
    ("Send2Trash", "Recycle Bin safety"),
)
REQUIRED_EXACT_VERSIONS = {
    # Native Florence-2 support begins after the project's former 4.49 line.
    # Exact matching keeps setup readiness aligned with the loader's reviewed
    # no-remote-code execution boundary.
    "transformers": "4.56.2",
}
FACE_DISTRIBUTIONS = (
    ("insightface", "InsightFace"),
    ("onnxruntime-gpu", "ONNX Runtime GPU"),
    ("onnxruntime", "ONNX Runtime CPU"),
)
BODY_DISTRIBUTIONS = (
    ("mediapipe", "MediaPipe"),
)


@dataclass(frozen=True, slots=True)
class SetupStatus:
    """Summarize one local source installation without changing it."""

    environment_exists: bool
    required_packages: dict[str, str]
    torch_runtime: dict[str, object]
    face_packages: dict[str, str]
    face_providers: tuple[str, ...]
    body_packages: dict[str, str]
    ffmpeg_path: str

    @property
    def required_ready(self) -> bool:
        """Return whether the application has every launch-time package."""
        unavailable = {"not installed", "check failed"}
        packages_available = self.environment_exists and all(
            value not in unavailable for value in self.required_packages.values()
        )
        exact_versions_match = all(
            self.required_packages.get(name) == expected
            for name, expected in REQUIRED_EXACT_VERSIONS.items()
        )
        return (
            packages_available
            and exact_versions_match
            and not self.torch_runtime.get("error")
        )


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one visible argument-list command from the project directory."""
    normalized = [os.fspath(argument) for argument in command]
    print("\n>", subprocess.list2cmdline(normalized), flush=True)
    return subprocess.run(
        normalized,
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
    )


def _venv_command(*arguments: str) -> list[str]:
    """Build a command for the managed interpreter or fail actionably."""
    if not VENV_PYTHON.is_file():
        raise RuntimeError(
            "The project-local environment does not exist yet. Choose "
            "First-time setup or Install/repair required app dependencies first."
        )
    return [os.fspath(VENV_PYTHON), *arguments]


def _read_package_versions(
    python_path: Path,
    distributions: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Read package metadata in one isolated child interpreter."""
    requested = [distribution for distribution, _label in distributions]
    # A small multi-line program is clearer and safer than importing optional
    # packages into the setup assistant's own process.
    script = """
import importlib.metadata
import json
import sys

values = {}
for name in json.loads(sys.argv[1]):
    try:
        values[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        values[name] = "not installed"
print(json.dumps(values, sort_keys=True))
"""
    completed = subprocess.run(
        [os.fspath(python_path), "-c", script, json.dumps(requested)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        return {name: "check failed" for name in requested}
    try:
        values = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {name: "check failed" for name in requested}
    return {str(name): str(values.get(name, "check failed")) for name in requested}


def _inspect_torch_runtime(python_path: Path) -> dict[str, object]:
    """Import PyTorch in a child process and report its actual device path."""
    script = """
import json

try:
    import torch
    available = bool(torch.cuda.is_available())
    result = {
        "version": str(torch.__version__),
        "cuda": str(torch.version.cuda or "CPU-only"),
        "cuda_available": available,
        "device": str(torch.cuda.get_device_name(0)) if available else "CPU",
        "error": "",
    }
except Exception as error:
    result = {
        "version": "not available",
        "cuda": "unknown",
        "cuda_available": False,
        "device": "unknown",
        "error": f"{type(error).__name__}: {error}",
    }
print(json.dumps(result, sort_keys=True))
"""
    try:
        completed = subprocess.run(
            [os.fspath(python_path), "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": f"{type(error).__name__}: {error}"}
    try:
        return dict(json.loads(completed.stdout.strip().splitlines()[-1]))
    except (IndexError, json.JSONDecodeError) as error:
        return {"error": f"PyTorch check failed: {error}"}


def _inspect_onnx_providers(python_path: Path) -> tuple[str, ...]:
    """Return ONNX Runtime providers without importing them into this process."""
    script = """
import json
try:
    import onnxruntime
    providers = onnxruntime.get_available_providers()
except Exception:
    providers = []
print(json.dumps(providers))
"""
    try:
        completed = subprocess.run(
            [os.fspath(python_path), "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return tuple(json.loads(completed.stdout.strip().splitlines()[-1]))
    except (OSError, subprocess.TimeoutExpired, IndexError, json.JSONDecodeError):
        return ()


def inspect_setup() -> SetupStatus:
    """Inspect required and optional components without installing anything."""
    if not VENV_PYTHON.is_file():
        missing_required = {
            name: "not installed" for name, _label in REQUIRED_DISTRIBUTIONS
        }
        missing_face = {
            name: "not installed" for name, _label in FACE_DISTRIBUTIONS
        }
        missing_body = {
            name: "not installed" for name, _label in BODY_DISTRIBUTIONS
        }
        return SetupStatus(
            environment_exists=False,
            required_packages=missing_required,
            torch_runtime={"error": "project-local environment is missing"},
            face_packages=missing_face,
            face_providers=(),
            body_packages=missing_body,
            ffmpeg_path=shutil.which("ffmpeg") or "",
        )

    return SetupStatus(
        environment_exists=True,
        required_packages=_read_package_versions(
            VENV_PYTHON, REQUIRED_DISTRIBUTIONS
        ),
        torch_runtime=_inspect_torch_runtime(VENV_PYTHON),
        face_packages=_read_package_versions(VENV_PYTHON, FACE_DISTRIBUTIONS),
        face_providers=_inspect_onnx_providers(VENV_PYTHON),
        body_packages=_read_package_versions(VENV_PYTHON, BODY_DISTRIBUTIONS),
        ffmpeg_path=shutil.which("ffmpeg") or "",
    )


def _format_package_group(
    distributions: Sequence[tuple[str, str]],
    versions: dict[str, str],
) -> list[str]:
    """Format labeled package versions for the console checklist."""
    return [
        f"    {label:<18} {versions.get(name, 'check failed')}"
        for name, label in distributions
    ]


def print_setup_status() -> SetupStatus:
    """Print a required/optional checklist and return the inspected facts."""
    status = inspect_setup()
    print(f"\n{APP_NAME} v{APP_VERSION} setup status")
    print("=" * 62)
    print("\nREQUIRED TO START THE SOURCE VERSION")
    print(f"  [{'OK' if status.environment_exists else 'MISSING'}] Local venv")
    for line in _format_package_group(
        REQUIRED_DISTRIBUTIONS, status.required_packages
    ):
        print(line)
    for name, expected in REQUIRED_EXACT_VERSIONS.items():
        installed = status.required_packages.get(name, "check failed")
        if installed not in {"not installed", "check failed", expected}:
            print(
                f"    UPDATE REQUIRED    {name} {expected} is required; "
                f"found {installed}"
            )
    if status.torch_runtime.get("error"):
        print(f"    PyTorch runtime    {status.torch_runtime['error']}")
    else:
        print(
            "    PyTorch device     "
            f"{status.torch_runtime.get('device')} "
            f"(CUDA {status.torch_runtime.get('cuda')})"
        )

    print("\nOPTIONAL FEATURES")
    print("  Face analysis:")
    for line in _format_package_group(FACE_DISTRIBUTIONS, status.face_packages):
        print(line)
    if status.face_providers:
        print(f"    Providers          {', '.join(status.face_providers)}")
    gpu_ort = status.face_packages.get("onnxruntime-gpu")
    cpu_ort = status.face_packages.get("onnxruntime")
    if gpu_ort not in {None, "not installed", "check failed"} and cpu_ort not in {
        None, "not installed", "check failed"
    }:
        print("    WARNING            CPU and GPU ONNX Runtime are both installed")
    print("  Body/pose analysis:")
    for line in _format_package_group(BODY_DISTRIBUTIONS, status.body_packages):
        print(line)
    print(
        "  FFmpeg:             "
        + (status.ffmpeg_path if status.ffmpeg_path else "not found on PATH")
    )
    print(
        "\nRESULT: "
        + ("Required app setup is ready." if status.required_ready else
           "Required app setup is incomplete.")
    )
    print("Optional items may remain missing until you need those features.")
    return status


def ensure_local_environment() -> None:
    """Create ``.\venv`` using the current supported bootstrap interpreter."""
    if VENV_PYTHON.is_file():
        supported = subprocess.run(
            [
                os.fspath(VENV_PYTHON),
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if supported.returncode:
            raise RuntimeError(
                "The existing venv uses Python older than 3.11. Rename that "
                "venv for backup, then rerun setup with Python 3.11 or newer."
            )
        print(f"Using existing project-local environment: {VENV_ROOT}")
        return
    if sys.version_info < (3, 11):
        raise RuntimeError(
            f"Python 3.11 or newer is required; found {sys.version.split()[0]}."
        )
    print(f"Creating project-local environment: {VENV_ROOT}")
    _run([sys.executable, "-m", "venv", VENV_ROOT])
    if not VENV_PYTHON.is_file():
        raise RuntimeError("Python reported success, but venv was not created.")


def _pip_install(*arguments: str) -> None:
    """Run pip only inside the managed environment."""
    _run(_venv_command("-m", "pip", *arguments))


def install_required_packages(*, offer_pytorch: bool = True) -> None:
    """Create/update the local environment and install base requirements."""
    ensure_local_environment()
    _pip_install("install", "--upgrade", "pip", "setuptools", "wheel")
    _pip_install("install", "--requirement", os.fspath(PROJECT_ROOT / "requirements.txt"))

    versions = _read_package_versions(VENV_PYTHON, (("torch", "PyTorch"),))
    if versions["torch"] == "not installed" and offer_pytorch:
        print(
            "\nPyTorch is the remaining required component. Its correct wheel "
            "depends on this computer's CPU/NVIDIA setup."
        )
        pytorch_install_menu()
    elif versions["torch"] != "not installed":
        print(f"\nKeeping installed PyTorch {versions['torch']}.")
    print_setup_status()


def normalize_official_pytorch_command(command: str) -> list[str]:
    """Validate a copied official pip command and return pip arguments.

    Only PyTorch packages and download.pytorch.org wheel indexes are accepted.
    The returned arguments are later run through the managed venv interpreter;
    shell metacharacters and arbitrary package/install commands are rejected.
    """
    raw = command.strip()
    if not raw:
        raise ValueError("No command was entered.")
    if re.search(r"[&|<>;`]", raw):
        raise ValueError("Shell operators are not accepted.")
    tokens = shlex.split(raw, posix=False)
    tokens = [token.strip('"') for token in tokens]
    try:
        install_index = tokens.index("install")
    except ValueError as error:
        raise ValueError("The copied command does not contain 'install'.") from error
    prefix = [token.casefold() for token in tokens[:install_index]]
    if prefix not in (["pip"], ["pip3"], ["python", "-m", "pip"],
                      ["python3", "-m", "pip"], ["py", "-m", "pip"]):
        raise ValueError("Copy the pip command exactly as shown by pytorch.org.")

    arguments = tokens[install_index + 1:]
    if not arguments:
        raise ValueError("The copied command contains no packages.")
    allowed_packages = ("torch", "torchvision", "torchaudio")
    saw_torch = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        lowered = token.casefold()
        if lowered in {"--index-url", "--extra-index-url"}:
            index += 1
            if index >= len(arguments):
                raise ValueError(f"{token} is missing its URL.")
            parsed = urlparse(arguments[index])
            if parsed.scheme != "https" or parsed.hostname != "download.pytorch.org":
                raise ValueError("Only the official download.pytorch.org index is accepted.")
        elif lowered == "--pre":
            pass
        elif lowered.startswith("-"):
            raise ValueError(f"Unsupported pip option: {token}")
        else:
            package = re.split(r"[<>=!~\[]", lowered, maxsplit=1)[0]
            if package not in allowed_packages:
                raise ValueError(f"Unexpected package in PyTorch command: {token}")
            saw_torch = saw_torch or package == "torch"
        index += 1
    if not saw_torch:
        raise ValueError("The official command must install torch.")
    return ["install", *arguments]


def install_pytorch_from_selector() -> None:
    """Open the official selector and safely install its copied command."""
    ensure_local_environment()
    print(
        "\nThe official PyTorch selector is opening in your browser.\n"
        "Choose Windows, Pip, Python, and the compute platform appropriate for "
        "your computer. Copy the one-line command it displays."
    )
    webbrowser.open(PYTORCH_SELECTOR_URL)
    command = input("\nPaste the official command here (blank cancels): ").strip()
    if not command:
        print("PyTorch installation cancelled.")
        return
    arguments = normalize_official_pytorch_command(command)
    _pip_install(*arguments)
    print_setup_status()


def install_cpu_pytorch() -> None:
    """Install the current stable official CPU-only PyTorch wheel."""
    ensure_local_environment()
    _pip_install(
        "install",
        "torch",
        "--index-url",
        "https://download.pytorch.org/whl/cpu",
    )
    print_setup_status()


def pytorch_install_menu() -> None:
    """Offer safe PyTorch choices without guessing the user's CUDA wheel."""
    while True:
        print(
            "\nPYTORCH SETUP\n"
            "  1. NVIDIA or advanced setup — use official PyTorch selector\n"
            "  2. CPU-only setup — install automatically\n"
            "  3. Check current PyTorch setup\n"
            "  0. Return to main menu"
        )
        choice = input("Choose an option: ").strip().casefold()
        if choice == "1":
            install_pytorch_from_selector()
        elif choice == "2":
            install_cpu_pytorch()
        elif choice == "3":
            print_setup_status()
        elif choice == "0":
            return
        else:
            print("Please choose 0, 1, 2, or 3.")


def install_face_dependencies() -> None:
    """Run the CUDA-aware InsightFace/ONNX installer in the local venv."""
    status = inspect_setup()
    if not status.required_ready:
        raise RuntimeError("Complete required app setup before installing face analysis.")
    _run(_venv_command(os.fspath(PROJECT_ROOT / "install_face_dependencies.py")))


def check_face_dependencies() -> int:
    """Run the existing detailed face provider check."""
    completed = _run(
        _venv_command(os.fspath(PROJECT_ROOT / "face_setup_check.py")),
        check=False,
    )
    return int(completed.returncode)


def install_body_dependencies() -> None:
    """Run the consent-based body-analysis installer in the local venv."""
    status = inspect_setup()
    if not status.required_ready:
        raise RuntimeError("Complete required app setup before optional body setup.")
    _run(_venv_command(os.fspath(PROJECT_ROOT / "install_body_dependencies.py")))


def check_ffmpeg() -> int:
    """Report PATH discovery while preserving the app's manual-path option."""
    executable = shutil.which("ffmpeg")
    print("\nFFmpeg setup")
    print("=" * 62)
    if executable:
        print(f"Found on PATH: {executable}")
        _run([executable, "-version"], check=False)
        return 0
    print(
        "FFmpeg was not found on PATH. It is optional and needed only for "
        "video-frame extraction. You can install it separately or select "
        "ffmpeg.exe in Settings > Video Extraction."
    )
    return 1


def launch_application() -> int:
    """Launch only after the required source-distribution setup is ready."""
    status = inspect_setup()
    if not status.required_ready:
        print_setup_status()
        print("\nChoose First-time setup before launching.")
        return 1
    completed = _run(
        _venv_command(os.fspath(PROJECT_ROOT / "app.py")),
        check=False,
    )
    return int(completed.returncode)


def first_time_setup() -> None:
    """Install required components, then offer optional feature setup."""
    install_required_packages(offer_pytorch=True)
    if not inspect_setup().required_ready:
        print(
            "\nRequired setup is not complete yet. Use PyTorch setup, then "
            "return to this menu to install optional features."
        )
        return
    if input("\nSet up optional face analysis now? [y/N]: ").strip().casefold() in {
        "y", "yes"
    }:
        install_face_dependencies()
    if input(
        "\nSet up optional body/pose analysis now? [y/N]: "
    ).strip().casefold() in {"y", "yes"}:
        install_body_dependencies()
    check_ffmpeg()
    print("\nFirst-time setup checklist finished.")


def menu() -> int:
    """Run the double-click-friendly setup and launch checklist."""
    print(f"\n{APP_NAME} v{APP_VERSION} — Setup & Launch")
    print("=" * 62)
    while True:
        print(
            "\nMAIN MENU\n"
            "  1. First-time setup (recommended)\n"
            "  2. Check setup status\n"
            "  3. Install/repair required app dependencies\n"
            "  4. Install/check PyTorch\n"
            "  5. Install optional face analysis (InsightFace / ONNX Runtime)\n"
            "  6. Check optional face analysis\n"
            "  7. Install optional body/pose analysis\n"
            "  8. Check optional FFmpeg\n"
            "  9. Run LoRA Image Curator\n"
            "  0. Exit"
        )
        choice = input("Choose an option: ").strip().casefold()
        try:
            if choice == "1":
                first_time_setup()
            elif choice == "2":
                print_setup_status()
            elif choice == "3":
                install_required_packages(offer_pytorch=True)
            elif choice == "4":
                pytorch_install_menu()
            elif choice == "5":
                install_face_dependencies()
            elif choice == "6":
                check_face_dependencies()
            elif choice == "7":
                install_body_dependencies()
            elif choice == "8":
                check_ffmpeg()
            elif choice == "9":
                launch_application()
            elif choice == "0":
                return 0
            else:
                print("Please choose a number from 0 through 9.")
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(f"\nSETUP ACTION FAILED\n{type(error).__name__}: {error}")
            print("No virtual environment, model, catalog, or dataset was deleted.")


def main() -> int:
    """Dispatch menu and compatibility entry points for Windows wrappers."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--check-required", action="store_true")
    actions.add_argument("--install-base", action="store_true")
    actions.add_argument("--install-face", action="store_true")
    actions.add_argument("--check-face", action="store_true")
    actions.add_argument("--install-body", action="store_true")
    actions.add_argument("--check-ffmpeg", action="store_true")
    actions.add_argument("--run", action="store_true")
    arguments = parser.parse_args()

    if arguments.check:
        print_setup_status()
        return 0
    if arguments.check_required:
        return 0 if inspect_setup().required_ready else 1
    if arguments.install_base:
        install_required_packages(offer_pytorch=True)
        return 0
    if arguments.install_face:
        install_face_dependencies()
        return 0
    if arguments.check_face:
        return check_face_dependencies()
    if arguments.install_body:
        install_body_dependencies()
        return 0
    if arguments.check_ffmpeg:
        return check_ffmpeg()
    if arguments.run:
        return launch_application()
    return menu()


def cli_entrypoint() -> int:
    """Convert setup failures into concise messages for batch-file users."""
    try:
        return main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"\nSETUP ACTION FAILED\n{type(error).__name__}: {error}")
        print("No virtual environment, model, catalog, or dataset was deleted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_entrypoint())
