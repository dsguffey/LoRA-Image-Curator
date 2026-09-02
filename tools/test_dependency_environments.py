"""Install both provider orders in disposable venvs and retain review evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(work: Path, wheel: Path) -> dict:
    """Never reuse or remove an existing environment, especially LIC's venv."""
    work = work.resolve()
    wheel = wheel.resolve()
    work.mkdir(parents=True, exist_ok=True)
    results = {}
    for label, order in (("face-body", ("face", "body", "face", "body")),
                         ("body-face", ("body", "face", "body", "face"))):
        folder = work / label
        if folder.exists() or folder.resolve() == ROOT / "venv":
            raise RuntimeError(f"Refusing to reuse existing environment: {folder}")
        venv.EnvBuilder(with_pip=True).create(folder)
        python = folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        # Pinned parser used for developer candidate verification before first install.
        subprocess.run([str(python), "-m", "pip", "install", "packaging==26.2"], check=True)
        for component in order:
            args = [str(python), "-B", "-m", "lic_dependencies.installer", component,
                    "--profile", "windows-nvidia-cu130"]
            if component == "face":
                args += ["--candidate", str(wheel)]
            subprocess.run(args, cwd=ROOT, check=True)
        output = work / f"{label}.json"
        subprocess.run([str(python), "-B", "-m", "lic_dependencies.validation", "--output", str(output)],
                       cwd=ROOT, check=True)
        results[label] = json.loads(output.read_text())
    report = {"wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
              "cpu_metadata_test_valid": all(r["cpu_metadata_test_valid"] for r in results.values()),
              "gpu_validated": False, "approved_for_lic": False,
              "orders": {k: v["cpu_metadata_test_valid"] for k, v in results.items()}}
    (work / "compatibility-summary.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.work, arguments.wheel), indent=2))
