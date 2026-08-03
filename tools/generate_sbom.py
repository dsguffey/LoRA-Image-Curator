"""Generate the deterministic SPDX component inventory for the source release.

The current SBOM describes the application and every direct/provider component
declared by ``provider_registry.json``. Platform-specific wheel dependencies
are intentionally not invented here; the future portable Windows build must
augment this document from its exact locked wheel/runtime payload and hashes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # Direct ``python tools/generate_sbom.py`` execution places only ``tools``
    # on sys.path. The explicit project root keeps the generator usable from a
    # clean extracted source archive without depending on the caller's shell.
    sys.path.insert(0, str(PROJECT_ROOT))

from provider_registry import load_provider_registry


def _spdx_id(key: str) -> str:
    """Return a deterministic SPDX-safe identifier for one registry key."""
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]", "-", key)


def build_sbom() -> dict[str, Any]:
    """Compose one deterministic SPDX 2.3 source/provider inventory."""
    registry = load_provider_registry()
    application = registry["application"]
    version = str(application["version"])
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": "SPDXRef-Application",
            "name": application["name"],
            "versionInfo": version,
            "downloadLocation": application["source"],
            "filesAnalyzed": False,
            "licenseConcluded": application["license"],
            "licenseDeclared": application["license"],
            "copyrightText": "Copyright David Scott Guffey",
        }
    ]
    relationships: list[dict[str, str]] = []
    for component in registry["components"]:
        package_id = _spdx_id(str(component["key"]))
        package: dict[str, Any] = {
            "SPDXID": package_id,
            "name": component["artifact"],
            "versionInfo": component["tested_version"],
            "supplier": f"Organization: {component['publisher']}",
            "downloadLocation": component["source_url"],
            "filesAnalyzed": False,
            "licenseConcluded": component["license"],
            "licenseDeclared": component["license"],
            "copyrightText": "NOASSERTION",
            "comment": (
                f"Function: {component['function']}. "
                f"Restriction: {component['usage_restriction']} "
                f"Integrity: {component['integrity_scope']}. "
                f"Bundled in source release: {component['bundled']}."
            ),
        }
        if component["sha256"]:
            package["checksums"] = [
                {"algorithm": "SHA256", "checksumValue": component["sha256"]}
            ]
        packages.append(package)
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Application",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
                "comment": "Direct, optional, or external component boundary recorded by the release registry.",
            }
        )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"LoRA Image Curator v{version} source/provider SBOM",
        "documentNamespace": (
            "https://github.com/dsguffey/LoRA-Image-Curator/"
            f"releases/tag/v{version}/sbom"
        ),
        "creationInfo": {
            "created": "2026-08-03T00:00:00Z",
            "creators": ["Tool: LoRA Image Curator tools/generate_sbom.py"],
            "comment": (
                "Source/provider scope. The future portable build augments this "
                "inventory from its exact private runtime and locked wheels."
            ),
        },
        "documentDescribes": ["SPDXRef-Application"],
        "packages": packages,
        "relationships": relationships,
    }


def main() -> int:
    """Write the canonical formatted SBOM and report its destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "SBOM.spdx.json",
    )
    arguments = parser.parse_args()
    output = arguments.output.expanduser().resolve()
    output.write_text(
        json.dumps(build_sbom(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"SPDX SBOM written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
