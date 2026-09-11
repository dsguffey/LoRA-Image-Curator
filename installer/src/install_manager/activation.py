"""Evaluate explicit activation prerequisites without creating an active installation."""
from __future__ import annotations


CORE_CRITERIA = ('application_verified', 'runtime_verified', 'final_venv_valid',
                 'dependencies_exact', 'pip_check', 'required_imports', 'target_safe',
                 'recovery_available', 'journal_complete')


def evaluate_criteria(evidence: dict[str, bool], *, provider_criteria: tuple[str, ...] = ()) -> dict:
    checks = {name: evidence.get(name) is True for name in CORE_CRITERIA + provider_criteria}
    blockers = [name for name, passed in checks.items() if not passed]
    return {'criteria_met': not blockers, 'checks': checks, 'blockers': blockers,
            'state': 'testing-ready' if not blockers else 'testing-failed',
            'activation_authorized': False, 'activation_performed': False}
