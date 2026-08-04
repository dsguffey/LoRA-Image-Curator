# Golden-Build Verification

`tests/test_golden_build.py` is the authoritative release and workspace-handoff
command for LoRA Image Curator v0.28.2.

```powershell
python -X dev -m tests.test_golden_build
```

It uses only temporary synthetic images and a generated schema-current catalog.
It does not locate, open, migrate, copy, or edit the user's catalogs or image
datasets.

## What a passing run establishes

- every maintained non-GUI regression from Milestone 6B through v0.28.2 passes;
- every project-owned Python file named by the signed release manifest
  compiles, while the adjacent virtual environment and user-managed folders
  remain outside the release boundary;
- schema migration, catalog edits, undo/redo, tags, search, image sets, import,
  export, quality/readiness, culling, video planning, provider orchestration,
  file-action services, settings, performance boundaries, and current UI
  contracts retain their tested behavior;
- source/documentation audit rules pass;
- user-managed catalogs, backups, and reports under the installed `output`
  folder remain outside source audit and release collection;
- arbitrary unmanifested local archive folders remain outside compilation,
  audit, and packaging while every shipped file retains the full audit;
- every direct SQLite connection in maintained source has explicit close
  ownership, including failed catalog initialization on Python 3.14/Windows;
- the flat full-source archive and slim Portable Source archive each build
  twice with identical bytes;
- archive CRC, member manifest, clean extraction, and a synthetic
  overwrite-in-place overlay pass without copying the installed workspace;
- the Portable Source extraction contains the complete runtime/setup payload
  but no repository tests, build tools, developer docs, GitHub metadata, or
  user/runtime data;
- the current cumulative Windows/Tk GUI chain through v0.28.2 passes without
  unraisable Tk finalizers, orphaned delayed callbacks, or background Tcl/Tk
  diagnostics on stderr; the v0.27.10-and-earlier history runs in a strict
  isolated process so its destroyed interpreters cannot affect newer checks;
- the reported project-source folder owns the imported application identity,
  while the separately reported Python runtime may safely come from an external
  virtual environment.

The final line must read:

```text
GOLDEN BUILD PASSED — LoRA Image Curator v0.28.2
```

`--no-gui` runs the complete headless portion. It is useful during development,
but it cannot establish the final Windows golden-build result.

## Honest limits

The gate protects established application behavior; it is not proof against
every possible image, catalog size, Windows configuration, or user action.
Tests use synthetic provider evidence rather than downloading or running
Florence, InsightFace, MediaPipe, or FFmpeg on the workstation. They verify
provider/file-action orchestration and safety contracts, but not model accuracy,
GPU-driver compatibility, third-party package behavior, or visual output
quality. Large-catalog performance measurements and the real dataset/training
trial remain active roadmap work.

The user has separately confirmed the complete packaged v0.27.17 Windows
golden-build gate. v0.28.2 retains the catalog/UI and Florence recovery runtime
while adding explicit provider-download and shared setup paths to the v0.28.0
provenance foundation and the separately tested slim Portable Source payload.
The v0.28.2 GUI, real CUDA tensor, Florence inference/resume, optional ONNX
Runtime endpoints, and setup from the extracted Portable Source ZIP still
require a fresh live-Windows pass before the release may be called golden.
