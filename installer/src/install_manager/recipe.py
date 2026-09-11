"""Load a declarative LIC package/install definition without executing it."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LicRecipe:
    """The small reviewed contract consumed by M1.1."""

    application_id: str
    display_name: str
    version: str
    artifact_id: str
    source: str
    expected_sha256: str
    required_archive_files: tuple[str, ...]
    required_runtime_inputs: tuple[str, ...]
    activation_allowed: bool
    install_execution_allowed: bool
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LicRecipe":
        """Validate the declarative shape and reject executable recipe fields."""
        required = ("application_id", "display_name", "version", "artifact_id", "source",
                    "expected_sha256", "required_archive_files", "required_runtime_inputs")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"LIC recipe missing fields: {missing}")
        forbidden = {key for key in data if key in {"command", "script", "hook", "shell"}}
        if forbidden:
            raise ValueError(f"Executable recipe fields are forbidden: {sorted(forbidden)}")
        if int(data.get("schema_version", 1)) != 1:
            raise ValueError("Unsupported LIC recipe schema_version")
        digest = str(data["expected_sha256"]).lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("LIC recipe requires an exact SHA-256")
        files = tuple(str(item).replace("\\", "/") for item in data["required_archive_files"])
        runtime = tuple(str(item).replace("\\", "/") for item in data["required_runtime_inputs"])
        if len(files) != len(set(files)) or len(runtime) != len(set(runtime)):
            raise ValueError("LIC recipe contains duplicate required paths")
        return cls(
            application_id=str(data["application_id"]), display_name=str(data["display_name"]),
            version=str(data["version"]), artifact_id=str(data["artifact_id"]),
            source=str(data["source"]), expected_sha256=digest,
            required_archive_files=files, required_runtime_inputs=runtime,
            activation_allowed=bool(data.get("activation_allowed", False)),
            install_execution_allowed=bool(data.get("install_execution_allowed", False)),
            schema_version=1,
        )


def load_recipe(path: Path) -> LicRecipe:
    """Load one JSON recipe; it is data only and cannot invoke operations."""
    return LicRecipe.from_dict(json.loads(path.read_text(encoding="utf-8")))
