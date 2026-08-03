"""Load and validate the release-owned third-party component registry.

The JSON document is the portability provenance boundary: setup code, model
downloaders, notices, tests, and the generated SBOM all refer to the same
component identities. Runtime inspection may add installed facts, but it must
not silently replace a release-owned publisher, license, revision, host, or
integrity expectation.
"""

from __future__ import annotations

import json

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REGISTRY_PATH = Path(__file__).with_name("provider_registry.json")
REQUIRED_COMPONENT_FIELDS = frozenset(
    {
        "key",
        "category",
        "function",
        "publisher",
        "artifact",
        "tested_version",
        "revision",
        "source_url",
        "download_hosts",
        "license",
        "usage_restriction",
        "approx_download_bytes",
        "sha256",
        "integrity_scope",
        "redistribution",
        "bundled",
        "acquisition",
    }
)


def _validate_sha256(value: object, *, key: str) -> None:
    """Reject malformed integrity values before any installer trusts them."""
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"Registry component {key!r} has an invalid SHA-256.")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Registry component {key!r} has an invalid SHA-256.")


def validate_registry(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the stable registry schema and security-critical URL fields."""
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported provider-registry schema version.")
    notice_version = data.get("notice_version")
    if not isinstance(notice_version, str) or not notice_version.strip():
        raise ValueError("Provider registry is missing its notice version.")
    application = data.get("application")
    if not isinstance(application, dict):
        raise ValueError("Provider registry is missing application identity.")
    components = data.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("Provider registry contains no components.")

    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Provider registry component is not an object.")
        missing = REQUIRED_COMPONENT_FIELDS - component.keys()
        if missing:
            raise ValueError(
                "Provider registry component is missing fields: "
                + ", ".join(sorted(missing))
            )
        key = component["key"]
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError(f"Invalid or duplicate provider key: {key!r}")
        seen.add(key)
        parsed = urlparse(str(component["source_url"]))
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"Registry component {key!r} requires an HTTPS source.")
        hosts = component["download_hosts"]
        if (
            not isinstance(hosts, list)
            or not hosts
            or any(not isinstance(host, str) or not host for host in hosts)
        ):
            raise ValueError(f"Registry component {key!r} has invalid hosts.")
        if parsed.hostname not in hosts:
            raise ValueError(
                f"Registry component {key!r} does not allow its source host."
            )
        _validate_sha256(component["sha256"], key=key)
    return data


@lru_cache(maxsize=1)
def load_provider_registry() -> dict[str, Any]:
    """Return the validated release registry without repeated disk parsing."""
    with REGISTRY_PATH.open("r", encoding="utf-8") as registry_file:
        data = json.load(registry_file)
    if not isinstance(data, dict):
        raise ValueError("Provider registry root is not an object.")
    return validate_registry(data)


def get_component(key: str) -> dict[str, Any]:
    """Return one named component or fail rather than guessing an identity."""
    for component in load_provider_registry()["components"]:
        if component["key"] == key:
            return dict(component)
    raise KeyError(f"Unknown provider-registry component: {key}")


def notice_version() -> str:
    """Return the disclosure revision persisted after user acknowledgement."""
    return str(load_provider_registry()["notice_version"])
