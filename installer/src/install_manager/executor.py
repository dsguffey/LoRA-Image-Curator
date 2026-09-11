"""Journaled M1.2 runtime and base-environment executor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .acquisition import AcquisitionPolicy, acquire_artifact
from .dependency_lock import DependencyLock
from .dependencies import acquire_locked_wheels, install_locked_wheels
from .environment import EnvironmentPlan, create_final_path_venv, extract_runtime
from .journal import OperationJournal
from .validation import validate_environment


def execute_environment_plan(plan: EnvironmentPlan, dependency_lock: DependencyLock,
                             policy: AcquisitionPolicy, *,
                             lic_source_root: Path | None = None,
                             require_cuda: bool = True) -> dict[str, Any]:
    """Execute only M1.2 into its disposable operation root; activation is impossible."""
    operation_root = Path(plan.operation_root).resolve()
    journal_root = Path(plan.journal_path).resolve().parent
    artifacts = [plan.runtime.as_dict(),
                 *[wheel.artifact.as_dict() for wheel in dependency_lock.wheels]]
    journal = OperationJournal.create(
        journal_root, plan.operation_id, target_path=Path(plan.final_venv_path),
        plan_digest=plan.digest(), artifacts=artifacts, steps=plan.steps,
    )
    current_step = ""
    try:
        journal.set_status("running")
        if plan.blockers:
            raise FileExistsError("; ".join(plan.blockers))

        current_step = "acquire_runtime"
        journal.set_step(current_step, "running")
        runtime_artifact = acquire_artifact(plan.runtime, Path(plan.cache_path), policy)
        journal.set_step(current_step, "completed", evidence=runtime_artifact.as_dict())

        current_step = "extract_runtime"
        journal.set_step(current_step, "running")
        runtime_path = extract_runtime(runtime_artifact, Path(plan.runtime_path))
        runtime_python = runtime_path / "python.exe"
        journal.set_step(current_step, "completed", runtime_path=runtime_path.as_posix())

        current_step = "create_final_path_venv"
        journal.set_step(current_step, "running")
        venv_python = create_final_path_venv(
            runtime_python, Path(plan.final_venv_path), approved_root=operation_root,
            log_path=operation_root / "logs" / "venv-create.log",
        )
        journal.set_step(current_step, "completed", interpreter=venv_python.as_posix())

        current_step = "acquire_dependencies"
        journal.set_step(current_step, "running")
        wheels = acquire_locked_wheels(dependency_lock, Path(plan.cache_path), policy)
        journal.set_step(current_step, "completed", wheel_count=len(wheels),
                         reused_count=sum(artifact.reused for artifact in wheels))

        current_step = "install_dependencies"
        journal.set_step(current_step, "running")
        install_evidence = install_locked_wheels(
            venv_python, dependency_lock, wheels,
            operation_root / "logs" / "dependency-install.log",
        )
        journal.set_step(current_step, "completed", evidence=install_evidence)

        current_step = "validate_environment"
        journal.set_step(current_step, "running")
        validation = validate_environment(
            venv_python, Path(plan.final_venv_path), runtime_path, dependency_lock,
            lic_source_root=lic_source_root, require_cuda=require_cuda,
        )
        journal.set_validation(validation)
        if not validation["passed"]:
            raise RuntimeError("environment validation failed")
        journal.set_step(current_step, "completed")
        journal.set_status("succeeded")
        return {"operation_id": plan.operation_id, "status": "succeeded",
                "journal": journal.path.as_posix(), "validation": validation,
                "activation_performed": False}
    except Exception as error:
        if current_step:
            try:
                journal.set_step(current_step, "failed", error=f"{type(error).__name__}: {error}")
            except (KeyError, OSError):
                pass
        journal.add_cleanup_action("Preserve verified artifacts for an explicit retry", performed=True)
        journal.add_cleanup_action("Review and remove only operation-owned incomplete output", performed=False)
        journal.set_status("failed", failure=f"{type(error).__name__}: {error}")
        raise
