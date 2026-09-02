"""Verified metadata-only wheel transformation using standard wheel unpack/pack."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from .profile import PROFILE
from .upstream import acquire, release_metadata

TOOL_VERSION = "1"
SUBSTITUTIONS = {"onnxruntime": f"onnxruntime-gpu=={PROFILE['onnxruntime-gpu']}",
                 "opencv-python": f"opencv-contrib-python=={PROFILE['opencv-contrib-python']}"}
EPOCH = "1767225600"  # 2026-01-01; no build-time bytes inside the wheel.


def inspect_wheel(path: Path) -> tuple[dict[str, bytes], str, object]:
    """Validate all archive paths and RECORD hashes before unpacking anything."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 64_000_000:
            raise ValueError("Duplicate members or excessive expanded wheel size")
        windows_names = set()
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
        for info in archive.infolist():
            name = info.filename
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                    or ":" in name or stat.S_ISLNK(info.external_attr >> 16)
                    or relative.as_posix() != name.rstrip("/")
                    or any(ord(c) < 32 for c in name)
                    or any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved
                           for part in relative.parts)):
                raise ValueError(f"Unsafe wheel member: {name}")
            key = name.rstrip("/").casefold()
            if key in windows_names:
                raise ValueError(f"Windows wheel path collision: {name}")
            windows_names.add(key)
        files = {name: archive.read(name) for name in names if not name.endswith("/")}
    metas = [n for n in files if n.endswith(".dist-info/METADATA")]
    if len(metas) != 1:
        raise ValueError("Expected one wheel distribution")
    info = metas[0].rsplit("/", 1)[0]
    record = f"{info}/RECORD"
    rows = list(csv.reader(io.StringIO(files[record].decode())))
    if len({r[0] for r in rows}) != len(rows) or {r[0] for r in rows} != set(files):
        raise ValueError("RECORD does not exactly inventory the wheel")
    for name, encoded, length in rows:
        if name == record:
            if encoded or length:
                raise ValueError("RECORD self-entry must be unhashed")
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
        if encoded != f"sha256={digest}" or int(length) != len(files[name]):
            raise ValueError(f"Wheel RECORD mismatch: {name}")
    meta = BytesParser(policy=policy.compat32).parsebytes(files[metas[0]])
    if canonicalize_name(meta["Name"]) != "insightface":
        raise ValueError("Upstream artifact is not InsightFace")
    return files, info, meta


def dependency_decision(requirements: list[str]) -> dict:
    """Inspect the Windows base install, preserving markers and optional extras."""
    env = {**default_environment(), "sys_platform": "win32", "platform_system": "Windows",
           "os_name": "nt", "platform_machine": "AMD64", "python_version": "3.14",
           "python_full_version": "3.14.6", "extra": ""}
    changes = []
    for raw in requirements:
        req = Requirement(raw)
        if req.url:
            raise ValueError("Direct-reference upstream dependencies require source/provenance review")
        active = req.marker is None or req.marker.evaluate(env)
        name = canonicalize_name(req.name)
        if name in SUBSTITUTIONS and active:
            replacement = SUBSTITUTIONS[name]
            if req.marker:
                replacement += f"; {req.marker}"
            changes.append({"from": raw, "to": replacement})
        elif active and name in {"onnxruntime-gpu", "opencv-contrib-python"}:
            if not req.specifier.contains(PROFILE[name]):
                raise ValueError(f"Upstream requires incompatible {raw}; planning review required")
        elif active and name in {"opencv-python-headless", "opencv-contrib-python-headless", "onnxruntime-directml"}:
            raise ValueError(f"New upstream backend requirement {raw}; planning review required")
    return {"patch_required": bool(changes), "substitutions": changes,
            "message": "downstream metadata patch required" if changes else
            "downstream patch no longer required; candidate official package should be tested directly"}


def build_candidate(upstream: Path, *, version: str, source: str, expected_hash: str,
                    output: Path) -> dict:
    """Preserve upstream payload and licenses; produce a candidate, never approval."""
    digest = hashlib.sha256(upstream.read_bytes()).hexdigest()
    if digest != expected_hash:
        raise ValueError("Upstream hash differs from the selected artifact")
    files, info, metadata = inspect_wheel(upstream)
    if Version(metadata["Version"]) != Version(version) or Version(version).local:
        raise ValueError("Upstream version identity mismatch or unexpected local version")
    decision = dependency_decision(metadata.get_all("Requires-Dist", []))
    output.mkdir(parents=True, exist_ok=True)
    downstream = f"{version}+{PROFILE['downstream_suffix']}" if decision["patch_required"] else version
    provenance = {"schema_version": 1, "tool_version": TOOL_VERSION,
                  "profile": PROFILE["profile"], "upstream_version": version,
                  "upstream_source": source, "upstream_sha256": digest,
                  "downstream_version": downstream, **decision}
    if not decision["patch_required"]:
        result_path = output / upstream.name
        if upstream.resolve() != result_path.resolve():
            shutil.copyfile(upstream, result_path)
    else:
        expected_tools = {"wheel": "0.47.0", "packaging": "26.2"}
        for name, expected in expected_tools.items():
            if importlib.metadata.version(name) != expected:
                raise RuntimeError(f"Reproducible build requires {name}=={expected}")
        with tempfile.TemporaryDirectory(prefix="lic-insightface-") as temporary:
            work = Path(temporary)
            subprocess.run([sys.executable, "-m", "wheel", "unpack", str(upstream.resolve()),
                            "--dest", str(work)], check=True)
            unpacked = next(work.iterdir())
            old_info = unpacked / info
            new_info = unpacked / f"insightface-{downstream}.dist-info"
            old_info.rename(new_info)
            metadata.replace_header("Version", downstream)
            replacements = {c["from"]: c["to"] for c in decision["substitutions"]}
            requirements = metadata.get_all("Requires-Dist", [])
            del metadata["Requires-Dist"]
            for raw in requirements:
                metadata["Requires-Dist"] = replacements.get(raw, raw)
            (new_info / "METADATA").write_bytes(metadata.as_bytes(policy=policy.compat32.clone(max_line_length=0)))
            (new_info / "LIC_UPSTREAM_METADATA.txt").write_bytes(files[f"{info}/METADATA"])
            (new_info / "LIC_PROVENANCE.json").write_text(
                json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            env = {**os.environ, "SOURCE_DATE_EPOCH": EPOCH}
            subprocess.run([sys.executable, "-m", "wheel", "pack", str(unpacked),
                            "--dest-dir", str(output.resolve())], env=env, check=True)
        result_path = output / f"insightface-{downstream}-py3-none-any.whl"
    result_files, result_info, _ = inspect_wheel(result_path)
    # Includes license/entry-point preservation, not just importable Python.
    for name, data in files.items():
        if name in {f"{info}/METADATA", f"{info}/RECORD"}:
            continue
        target = name.replace(info + "/", result_info + "/", 1) if name.startswith(info + "/") else name
        if result_files.get(target) != data:
            raise ValueError(f"Non-metadata upstream member changed: {name}")
    report = {**provenance, "wheel": result_path.name,
              "wheel_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
              "built_at_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version.split()[0], "wheel_tool": importlib.metadata.version("wheel"),
              "packaging_tool": importlib.metadata.version("packaging"),
              "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "build_valid": True, "cpu_metadata_test_valid": False,
              "gpu_validated": False, "approved_for_lic": False}
    (output / "build-metadata.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    """Build the pinned input or discover a candidate; never edit supported pins."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=PROFILE["upstream_version"])
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.latest or args.version != PROFILE["upstream_version"]:
        meta = release_metadata(None if args.latest else args.version, args.output / "cache")
        version = meta["info"]["version"]
        if Version(version).is_prerelease:
            raise ValueError("Prerelease discovery is not enabled")
        wheels = [f for f in meta["urls"] if f["filename"] == f"insightface-{version}-py3-none-any.whl" and not f.get("yanked")]
        if len(wheels) != 1:
            raise ValueError("Expected a unique non-yanked universal upstream wheel; review source/build changes")
        url, digest = wheels[0]["url"], wheels[0]["digests"]["sha256"]
    else:
        version, url, digest = args.version, PROFILE["upstream_url"], PROFILE["upstream_sha256"]
    upstream = acquire(url, digest, args.output / "upstream" / f"insightface-{version}-py3-none-any.whl")
    report = build_candidate(upstream, version=version, source=url, expected_hash=digest, output=args.output)
    report["newer_than_supported"] = Version(version) > Version(PROFILE["upstream_version"])
    (args.output / "build-metadata.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
