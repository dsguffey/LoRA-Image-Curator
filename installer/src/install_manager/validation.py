"""Validate exact identity, inventory and base LIC import readiness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .dependency_lock import DependencyLock, normalize_distribution
from .delivery_paths import probe_file
from .child_process import run as child_run


def _run_json(command: list[str], *, environment: dict[str, str]) -> dict[str, Any]:
    result = child_run(command, env=environment, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")
    return json.loads(result.stdout)


def validate_environment(venv_python: Path, final_venv: Path, runtime_path: Path,
                         lock: DependencyLock, *, lic_source_root: Path | None = None,
                         require_cuda: bool = True) -> dict[str, Any]:
    """Prove the created environment is at its final path and contains only the lock."""
    venv_python = venv_python.expanduser().resolve()
    final_venv = final_venv.expanduser().resolve()
    runtime_path = runtime_path.expanduser().resolve()
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    identity_code = (
        "import json,platform,sys; print(json.dumps({'version':platform.python_version(),"
        "'machine':platform.machine(),'executable':sys.executable,'prefix':sys.prefix,"
        "'base_prefix':sys.base_prefix}))"
    )
    identity = _run_json([str(venv_python), "-I", "-B", "-c", identity_code], environment=environment)
    raw_inventory = _run_json([str(venv_python), "-I", "-B", "-c",
                               "import json,importlib.metadata as m; print(json.dumps({'items':sorted([(d.metadata['Name'],d.version) for d in m.distributions()])}))"],
                              environment=environment)["items"]
    inventory = {normalize_distribution(name): version for name, version in raw_inventory}
    expected = lock.expected_inventory
    unexpected = sorted(set(inventory) - set(expected))
    missing = sorted(set(expected) - set(inventory))
    mismatched = {name: {"expected": expected[name], "actual": inventory.get(name)}
                  for name in expected if inventory.get(name) != expected[name]}
    pip_check = child_run([str(venv_python), "-I", "-B", "-m", "pip", "check"],
                               env=environment, text=True, capture_output=True, check=False)
    imports = sorted({name for wheel in lock.wheels for name in wheel.import_names})
    import_code = "import " + ",".join(imports) if imports else "pass"
    child_run([str(venv_python), "-I", "-B", "-c", import_code], env=environment,
                   text=True, capture_output=True, check=True)
    integrity_probe = probe_file('installed_integrity_probe.py')
    integrity = _run_json([str(venv_python), "-I", "-B", str(integrity_probe)],
                          environment=environment)
    lic_modules: list[str] = []
    if lic_source_root is not None:
        source = lic_source_root.expanduser().resolve()
        modules = ("settings_manager", "provider_registry", "catalog")
        code = (f"import sys; sys.path.insert(0, {str(source)!r}); " +
                "; ".join(f"import {module}" for module in modules))
        child_run([str(venv_python), "-I", "-B", "-c", code], env=environment,
                       text=True, capture_output=True, check=True)
        lic_modules = list(modules)
    cuda = {"required": require_cuda, "available": False, "operation_passed": False}
    if require_cuda:
        cuda_code = (
            "import json,torch; ok=torch.cuda.is_available(); "
            "x=(torch.arange(16,device='cuda').reshape(4,4).float() if ok else None); "
            "passed=bool(ok and (x@x).sum().item()>0); print(json.dumps({'available':ok,"
            "'operation_passed':passed,'torch':torch.__version__,'cuda':torch.version.cuda,"
            "'device':torch.cuda.get_device_name(0) if ok else None}))"
        )
        cuda.update(_run_json([str(venv_python), "-I", "-B", "-c", cuda_code], environment=environment))
    cfg = (final_venv / "pyvenv.cfg").read_text(encoding="utf-8")
    activation = (final_venv / "Scripts" / "activate.bat").read_text(encoding="utf-8")
    path_ok = (Path(identity["executable"]).resolve() == venv_python
               and Path(identity["prefix"]).resolve() == final_venv
               and Path(identity["base_prefix"]).resolve() == runtime_path
               and str(final_venv).casefold() in activation.casefold())
    passed = (identity["version"] == lock.python_version and path_ok and not unexpected
              and not missing and not mismatched and pip_check.returncode == 0 and integrity["passed"]
              and (not require_cuda or cuda["operation_passed"]))
    return {
        "passed": passed, "identity": identity, "path_consistency": path_ok,
        "pyvenv_cfg": cfg.splitlines(), "inventory": inventory,
        "expected_inventory": expected, "missing": missing, "unexpected": unexpected,
        "mismatched": mismatched, "pip_check": {"exit_code": pip_check.returncode,
        "stdout": pip_check.stdout.strip(), "stderr": pip_check.stderr.strip()},
        "imports": imports, "lic_modules": lic_modules, "cuda": cuda,
        "installed_file_integrity": integrity,
        "global_path_modified": False, "activation_performed": False,
    }
