"""Shared source/test path helpers for relocated historical checks."""

from __future__ import annotations

from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent


def project_file(relative_name: str) -> Path:
    """Return one application/repository file beneath the project root."""
    return PROJECT_ROOT / relative_name


def test_file(filename: str) -> Path:
    """Return one maintained test module beneath the dedicated test folder."""
    return TEST_ROOT / filename


def source_text(relative_name: str) -> str:
    """Read project source or a sibling test using its repository-relative name."""
    root = TEST_ROOT if relative_name.startswith("test_") else PROJECT_ROOT
    return (root / relative_name).read_text(encoding="utf-8")
