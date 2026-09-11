"""Run inside a created venv to verify installed RECORD ownership and hashes."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


def main() -> int:
    prefix = Path(sys.prefix).resolve()
    owners: dict[str, list[str]] = {}
    missing: list[str] = []
    mismatched: list[str] = []
    escaped: list[str] = []
    hashed_files = 0
    distributions = 0
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        record = distribution.read_text("RECORD")
        if record is None:
            missing.append(f"{name}:RECORD")
            continue
        distributions += 1
        for relative, encoded_hash, _size in csv.reader(record.splitlines()):
            path = Path(distribution.locate_file(relative)).resolve()
            if path != prefix and prefix not in path.parents:
                escaped.append(f"{name}:{relative}")
                continue
            key = str(path).casefold()
            owners.setdefault(key, []).append(name)
            if not path.is_file():
                missing.append(f"{name}:{relative}")
                continue
            if not encoded_hash:
                continue
            algorithm, separator, expected = encoded_hash.partition("=")
            if not separator or algorithm not in hashlib.algorithms_available:
                mismatched.append(f"{name}:{relative}:unsupported-hash")
                continue
            with path.open("rb") as stream:
                actual = base64.urlsafe_b64encode(hashlib.file_digest(stream, algorithm).digest()).rstrip(b"=").decode()
            hashed_files += 1
            if actual != expected:
                mismatched.append(f"{name}:{relative}")
    duplicates = {path: names for path, names in owners.items() if len(set(names)) > 1}
    report = {
        "passed": distributions > 0 and hashed_files > 0 and not missing and not mismatched
                  and not escaped and not duplicates,
        "distributions": distributions, "recorded_paths": len(owners),
        "hashed_files": hashed_files, "missing": missing, "mismatched": mismatched,
        "escaped": escaped, "duplicate_ownership": duplicates,
    }
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
