"""Trusted artifact descriptors and measured acquisition results."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """Immutable identity and trust policy for one downloadable artifact."""

    artifact_id: str
    version: str
    filename: str
    url: str
    expected_sha256: str
    publisher: str
    source_name: str
    license_id: str
    expected_size: int | None = None

    def validate(self, allowed_hosts: tuple[str, ...]) -> None:
        """Fail closed on unsafe identifiers, sources or incomplete identity."""
        for label, value in (("artifact_id", self.artifact_id), ("version", self.version),
                             ("filename", self.filename)):
            if not _SAFE_COMPONENT.fullmatch(value):
                raise ValueError(f"unsafe {label}: {value!r}")
        parsed = urlparse(self.url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            raise ValueError("artifact source must be an HTTPS URL without credentials")
        if parsed.hostname.lower() not in {host.lower() for host in allowed_hosts}:
            raise ValueError(f"artifact source host is not allowlisted: {parsed.hostname}")
        if unquote(Path(parsed.path).name) != self.filename:
            raise ValueError("artifact filename does not match its source URL")
        digest = self.expected_sha256.lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("artifact requires an exact SHA-256")
        if self.expected_size is not None and self.expected_size <= 0:
            raise ValueError("artifact expected_size must be positive")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ArtifactDescriptor":
        return cls(
            artifact_id=str(data["artifact_id"]), version=str(data["version"]),
            filename=str(data["filename"]), url=str(data["url"]),
            expected_sha256=str(data["expected_sha256"]).lower(),
            publisher=str(data["publisher"]), source_name=str(data["source_name"]),
            license_id=str(data["license_id"]),
            expected_size=int(data["expected_size"]) if data.get("expected_size") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class AcquiredArtifact:
    """A verified local artifact; reuse is explicit and observable."""

    descriptor: ArtifactDescriptor
    cache_path: str
    actual_sha256: str
    actual_size: int
    state: str
    verified: bool
    reused: bool
    attempts: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def load_artifact_descriptor(path: Path) -> ArtifactDescriptor:
    """Load declarative artifact metadata; no source action is performed."""
    return ArtifactDescriptor.from_dict(json.loads(path.read_text(encoding="utf-8")))
