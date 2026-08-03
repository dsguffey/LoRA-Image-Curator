"""Portable Windows setup and launch assistant for the source distribution.

The public GitHub release is Python source, so it needs a Python interpreter and
third-party packages.  This module keeps those implementation details behind a
plain numbered menu: it creates a project-local virtual environment, installs
only user-selected components, reports required and optional readiness, and
always invokes pip through the exact local interpreter it manages.

PyTorch remains an explicit choice because the correct official wheel depends
on the workstation's CPU/NVIDIA driver stack. The assistant can install and
verify the reviewed CUDA 13 pair on a compatible modern Windows/NVIDIA system,
install the official CPU build, or safely translate a command copied from
PyTorch's official selector so it installs into ``.\venv`` rather than the
user's system Python. It never downloads model weights or FFmpeg.
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
from datetime import datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from app_identity import APP_NAME, APP_VERSION
from provider_registry import get_component, load_provider_registry


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_ROOT = PROJECT_ROOT / "venv"
VENV_PYTHON = VENV_ROOT / "Scripts" / "python.exe"
PYTORCH_SELECTOR_URL = "https://pytorch.org/get-started/locally/"
NVIDIA_RUNTIME_COMPONENT = get_component("pytorch_nvidia")
TESTED_NVIDIA_TORCH_VERSION = "2.13.0"
TESTED_NVIDIA_TORCHVISION_VERSION = "0.28.0"
TESTED_NVIDIA_CUDA_VERSION = "13.0"
TESTED_NVIDIA_INDEX_URL = str(NVIDIA_RUNTIME_COMPONENT["source_url"])
# PyTorch's CUDA 13 guidance identifies this as the minimum Windows driver for
# current Blackwell-class wheels. The comparison is intentionally local and
# conservative: an older driver stops before pip changes the environment.
MINIMUM_CUDA_13_WINDOWS_DRIVER = (580, 88)

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
    """Import PyTorch in a child process and report its actual device path.

    When CUDA is visible, the probe also performs one tiny tensor operation and
    synchronizes it. ``torch.cuda.is_available()`` alone can be true even when
    a later DLL, architecture, or driver failure prevents real inference.
    """
    script = """
import json

try:
    import torch
    available = bool(torch.cuda.is_available())
    smoke_ok = False
    smoke_error = ""
    if available:
        try:
            value = torch.tensor([6.0], device="cuda") * 7.0
            torch.cuda.synchronize()
            smoke_ok = float(value.item()) == 42.0
        except Exception as smoke_exception:
            smoke_error = f"{type(smoke_exception).__name__}: {smoke_exception}"
    result = {
        "version": str(torch.__version__),
        "cuda": str(torch.version.cuda or "CPU-only"),
        "cuda_available": available,
        "device": str(torch.cuda.get_device_name(0)) if available else "CPU",
        "architectures": list(torch.cuda.get_arch_list()) if available else [],
        "smoke_ok": smoke_ok,
        "smoke_error": smoke_error,
        "error": "",
    }
except Exception as error:
    result = {
        "version": "not available",
        "cuda": "unknown",
        "cuda_available": False,
        "device": "unknown",
        "architectures": [],
        "smoke_ok": False,
        "smoke_error": "",
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


def _driver_version_key(version: str) -> tuple[int, ...]:
    """Convert an NVIDIA dotted driver version into a comparable integer key."""
    pieces = re.findall(r"\d+", version)
    return tuple(int(piece) for piece in pieces)


def inspect_nvidia_runtime() -> tuple[tuple[str, str], ...]:
    """Return NVIDIA GPU names and driver versions reported by ``nvidia-smi``.

    The helper is read-only and deliberately avoids registry probing or CUDA
    toolkit assumptions. PyTorch wheels carry their own CUDA runtime; the
    installed display/compute driver is the relevant machine-wide boundary.
    """
    executable = shutil.which("nvidia-smi")
    if not executable:
        return ()
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=name,driver_version",
                "--format=csv,noheader",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode:
        return ()

    devices: list[tuple[str, str]] = []
    for raw_line in completed.stdout.splitlines():
        name, separator, driver = raw_line.partition(",")
        if separator and name.strip() and driver.strip():
            devices.append((name.strip(), driver.strip()))
    return tuple(devices)


def save_dependency_snapshot(label: str) -> Path:
    """Record the managed environment before a focused runtime change."""
    ensure_local_environment()
    backup_directory = PROJECT_ROOT / "dependency_backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = backup_directory / f"before_{label}_{timestamp}.txt"
    with snapshot_path.open("w", encoding="utf-8") as snapshot_file:
        subprocess.run(
            _venv_command("-m", "pip", "freeze"),
            cwd=PROJECT_ROOT,
            check=True,
            stdout=snapshot_file,
            text=True,
        )
    return snapshot_path


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
        if status.torch_runtime.get("cuda_available"):
            result = "passed" if status.torch_runtime.get("smoke_ok") else "FAILED"
            print(f"    CUDA tensor check  {result}")
            if status.torch_runtime.get("smoke_error"):
                print(
                    "    CUDA error         "
                    f"{status.torch_runtime.get('smoke_error')}"
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


def print_provider_provenance_summary() -> None:
    """Show release-owned third-party identities without installing anything."""
    registry = load_provider_registry()
    print("\nTHIRD-PARTY COMPONENT RECORD")
    print(f"  Notice revision: {registry['notice_version']}")
    for key in (
        "pytorch_nvidia",
        "transformers",
        "florence_model",
        "mediapipe_pose_full_v1",
        "insightface_buffalo_l",
        "ffmpeg_external",
    ):
        component = get_component(key)
        print(
            f"  {component['artifact']}: {component['tested_version']} — "
            f"{component['license']}"
        )
    print("  Full record: provider_registry.json and SBOM.spdx.json")


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
    """Create/update the local environment and install base requirements.

    PyTorch must be selected *before* installing ``requirements.txt``. ``timm``
    depends on Torch/Torchvision, so the old order allowed pip to choose a CPU
    wheel transitively and then made the assistant preserve that accidental
    choice as though the user had selected it.
    """
    ensure_local_environment()
    _pip_install("install", "--upgrade", "pip", "setuptools", "wheel")

    versions = _read_package_versions(VENV_PYTHON, (("torch", "PyTorch"),))
    if versions["torch"] == "not installed" and offer_pytorch:
        print(
            "\nChoose PyTorch before the remaining packages are installed. "
            "This prevents a dependency from silently selecting CPU-only "
            "PyTorch on an NVIDIA computer."
        )
        pytorch_install_menu()
        versions = _read_package_versions(VENV_PYTHON, (("torch", "PyTorch"),))
        if versions["torch"] == "not installed":
            print(
                "\nBase installation paused before requirements.txt. Choose a "
                "PyTorch build, then run this installer again."
            )
            return
    elif versions["torch"] != "not installed":
        runtime = _inspect_torch_runtime(VENV_PYTHON)
        nvidia_devices = inspect_nvidia_runtime()
        if (
            offer_pytorch
            and nvidia_devices
            and not runtime.get("cuda_available")
        ):
            names = ", ".join(name for name, _driver in nvidia_devices)
            print(
                f"\nWARNING: NVIDIA hardware was found ({names}), but installed "
                f"PyTorch {versions['torch']} cannot use CUDA. Choose the "
                "tested NVIDIA repair or another official PyTorch build."
            )
            pytorch_install_menu()
        else:
            print(f"\nKeeping installed PyTorch {versions['torch']}.")

    _pip_install(
        "install",
        "--requirement",
        os.fspath(PROJECT_ROOT / "requirements.txt"),
    )
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


def install_tested_nvidia_pytorch() -> None:
    """Install and verify the reviewed CUDA 13 runtime for modern NVIDIA GPUs.

    This is a focused environment repair, not a machine-wide CUDA installation.
    The official wheel carries its CUDA runtime inside ``.\venv``. Existing
    catalogs, provider models, settings, images, outputs, and ComfyUI's separate
    environment are outside this operation.
    """
    if os.name != "nt":
        raise RuntimeError(
            "The automatic NVIDIA repair is currently qualified for Windows. "
            "Use the official PyTorch selector on another operating system."
        )
    ensure_local_environment()
    devices = inspect_nvidia_runtime()
    if not devices:
        raise RuntimeError(
            "nvidia-smi did not report an NVIDIA GPU. No packages were changed."
        )
    for name, driver in devices:
        if _driver_version_key(driver) < MINIMUM_CUDA_13_WINDOWS_DRIVER:
            minimum = ".".join(str(part) for part in MINIMUM_CUDA_13_WINDOWS_DRIVER)
            raise RuntimeError(
                f"{name} reports driver {driver}; CUDA 13 PyTorch requires "
                f"Windows NVIDIA driver {minimum} or newer. Update the NVIDIA "
                "driver, restart Windows, then run this repair again. No "
                "packages were changed."
            )

    torch_versions = _read_package_versions(
        VENV_PYTHON,
        (("torch", "PyTorch"), ("torchvision", "Torchvision")),
    )
    replacing_existing_pair = torch_versions["torch"] != "not installed"
    face_versions = _read_package_versions(VENV_PYTHON, FACE_DISTRIBUTIONS)
    face_stack_present = any(
        version not in {"not installed", "check failed"}
        for version in face_versions.values()
    )
    snapshot_path = save_dependency_snapshot("nvidia_pytorch_repair")
    print(f"Environment backup: {snapshot_path}")
    for name, driver in devices:
        print(f"NVIDIA GPU: {name} (driver {driver})")
    print(
        f"Installing tested PyTorch {TESTED_NVIDIA_TORCH_VERSION} / "
        f"Torchvision {TESTED_NVIDIA_TORCHVISION_VERSION} with CUDA "
        f"{TESTED_NVIDIA_CUDA_VERSION}."
    )
    install_arguments = ["install", "--upgrade"]
    if replacing_existing_pair:
        # A CPU local-version build satisfies ``torch==2.13.0`` under Python
        # package-version rules, so force replacement. The established base
        # environment already owns the dependencies; ``--no-deps`` prevents a
        # focused CPU-to-CUDA swap from churning unrelated packages.
        install_arguments.extend(("--force-reinstall", "--no-deps"))
    install_arguments.extend(
        (
            f"torch=={TESTED_NVIDIA_TORCH_VERSION}",
            f"torchvision=={TESTED_NVIDIA_TORCHVISION_VERSION}",
            "--index-url",
            TESTED_NVIDIA_INDEX_URL,
        )
    )
    _pip_install(*install_arguments)

    runtime = _inspect_torch_runtime(VENV_PYTHON)
    if runtime.get("error"):
        raise RuntimeError(
            "The CUDA PyTorch wheel installed, but import verification failed: "
            f"{runtime['error']}. The pre-repair package list is {snapshot_path}."
        )
    if not runtime.get("cuda_available") or not runtime.get("smoke_ok"):
        detail = runtime.get("smoke_error") or "CUDA remained unavailable"
        raise RuntimeError(
            "The CUDA PyTorch wheel installed, but the real tensor check failed: "
            f"{detail}. The pre-repair package list is {snapshot_path}."
        )
    if not str(runtime.get("version", "")).startswith(
        TESTED_NVIDIA_TORCH_VERSION
    ) or not str(runtime.get("cuda", "")).startswith("13"):
        raise RuntimeError(
            "PyTorch ran on the GPU, but the installed version did not match "
            "the reviewed runtime pair. Expected PyTorch 2.13.0 with CUDA 13.x; "
            f"found {runtime.get('version')} with CUDA {runtime.get('cuda')}."
        )

    print(
        "\nCUDA verification passed: "
        f"{runtime.get('device')} using PyTorch {runtime.get('version')} "
        f"(CUDA {runtime.get('cuda')})."
    )
    print(f"Architectures: {', '.join(runtime.get('architectures', []))}")

    if face_stack_present:
        print(
            "\nExisting face-analysis packages were detected. Realigning ONNX "
            "Runtime to the CUDA 13 line now."
        )
        _run(
            _venv_command(
                os.fspath(PROJECT_ROOT / "install_face_dependencies.py")
            )
        )
    else:
        print(
            "\nOptional face-analysis packages are not installed; no ONNX "
            "Runtime packages were added."
        )
    print_setup_status()


def pytorch_install_menu() -> None:
    """Offer safe PyTorch choices without guessing the user's CUDA wheel."""
    while True:
        print(
            "\nPYTORCH SETUP\n"
            "  1. Modern NVIDIA GPU — tested CUDA 13.0 automatic repair\n"
            "  2. NVIDIA/advanced — use official PyTorch selector\n"
            "  3. CPU-only setup — install automatically\n"
            "  4. Check current PyTorch setup\n"
            "  0. Return to main menu"
        )
        choice = input("Choose an option: ").strip().casefold()
        if choice == "1":
            install_tested_nvidia_pytorch()
        elif choice == "2":
            install_pytorch_from_selector()
        elif choice == "3":
            install_cpu_pytorch()
        elif choice == "4":
            print_setup_status()
        elif choice == "0":
            return
        else:
            print("Please choose 0, 1, 2, 3, or 4.")


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


def launch_application(*, setup_verified: bool = False) -> int:
    """Launch after setup is ready, reusing a completed smart-launch check.

    ``smart_launch_application`` performs the expensive package and CUDA
    inspection before it calls this function. Repeating that same inspection
    made the ordinary launcher appear stalled twice, so the verified path now
    proceeds directly to Tk while menu/direct callers retain the safety check.
    """
    if not setup_verified:
        print("\nChecking required packages before launch...", flush=True)
        status = inspect_setup()
        if not status.required_ready:
            print_setup_status()
            print("\nChoose First-time setup before launching.")
            return 1
    print(f"Starting {APP_NAME}...", flush=True)
    completed = _run(
        _venv_command(os.fspath(PROJECT_ROOT / "app.py")),
        check=False,
    )
    return int(completed.returncode)


def smart_launch_application() -> int:
    """Validate the managed runtime before the ordinary launcher starts Tk.

    A missing/incompatible required stack returns a dedicated code so the
    batch wrapper can open guided setup. NVIDIA hardware paired with CPU-only
    PyTorch is treated as repair-needed rather than silently starting a run
    that may be orders of magnitude slower. The diagnostic launcher remains a
    direct escape hatch for troubleshooting.
    """
    print("Checking required packages...", flush=True)
    status = inspect_setup()
    if not status.required_ready:
        print_setup_status()
        print("\nRequired setup is incomplete. Opening guided setup is recommended.")
        return 2
    print("Checking graphics runtime...", flush=True)
    nvidia_devices = inspect_nvidia_runtime()
    if nvidia_devices and not status.torch_runtime.get("cuda_available"):
        names = ", ".join(name for name, _driver in nvidia_devices)
        print(
            "\nNVIDIA hardware was found, but PyTorch cannot use CUDA: "
            f"{names}. The ordinary launcher will not silently fall back to "
            "CPU. Use the tested NVIDIA repair in guided setup."
        )
        return 2
    if status.torch_runtime.get("cuda_available") and not status.torch_runtime.get(
        "smoke_ok"
    ):
        print("\nThe CUDA tensor check failed. Open guided setup before launching.")
        return 2
    print("Runtime check passed.", flush=True)
    return launch_application(setup_verified=True)


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
            "  9. View third-party component record\n"
            "  10. Run LoRA Image Curator\n"
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
                print_provider_provenance_summary()
            elif choice == "10":
                launch_application()
            elif choice == "0":
                return 0
            else:
                print("Please choose a number from 0 through 10.")
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
    actions.add_argument("--install-nvidia", action="store_true")
    actions.add_argument("--install-face", action="store_true")
    actions.add_argument("--check-face", action="store_true")
    actions.add_argument("--install-body", action="store_true")
    actions.add_argument("--check-ffmpeg", action="store_true")
    actions.add_argument("--smart-launch", action="store_true")
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
    if arguments.install_nvidia:
        install_tested_nvidia_pytorch()
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
    if arguments.smart_launch:
        return smart_launch_application()
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
