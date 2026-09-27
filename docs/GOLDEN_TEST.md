# Golden-Build Verification

`tests/test_golden_build.py` is the authoritative release and workspace-handoff
command for LoRA Image Curator v0.28.4.

```powershell
python -X dev -m tests.test_golden_build
```

It uses only temporary synthetic images and a generated schema-current catalog.
It does not locate, open, migrate, copy, or edit the user's catalogs or image
datasets.

## What a passing run establishes

- every supported non-GUI contract from Milestone 6B through v0.28.4 passes;
  `tools/run_regressions.py` names and explains nine retired assertions whose
  old UI, setup, packaging, or README contracts were deliberately replaced;
  the remaining functions in those historical modules still run;
- every project-owned Python file named by the signed release manifest
  compiles, while the adjacent virtual environment and user-managed folders
  remain outside the release boundary;
- schema migration, catalog edits, undo/redo, tags, search, image sets, import,
  export, quality/readiness, culling, video planning, provider orchestration,
  file-action services, settings, performance boundaries, and current UI
  contracts retain their tested behavior, including trigger-first preview and
  written-sidecar parity for current export profiles and selection of an
  available source from multiple cataloged file locations;
- source/documentation audit rules pass;
- user-managed catalogs, backups, and reports under the installed `output`
  folder remain outside source audit and release collection;
- arbitrary unmanifested local archive folders remain outside compilation,
  audit, and packaging while every shipped file retains the full audit;
- every direct SQLite connection in maintained source has explicit close
  ownership, including failed catalog initialization on Python 3.14/Windows;
- the deterministic full-source archive builds twice with identical bytes; archive CRC, member manifest, clean extraction, and a synthetic overwrite-in-place overlay pass without copying the installed workspace;
- the supported Windows/Tk GUI sequence checks the focused v0.28.1 Florence
  preflight, v0.28.3 detection-only completion, and v0.28.4 catalog/workflow
  controls without stderr diagnostics; older versioned GUI files remain in the
  archive as historical evidence but are not replayed by this current gate;
- the reported project-source folder owns the imported application identity,
  while the separately reported Python runtime may safely come from an external
  virtual environment.

The final line must read:

```text
GOLDEN BUILD PASSED — LoRA Image Curator v0.28.4
```

`--no-gui` runs the complete headless portion. It is useful during development,
but it cannot establish the final Windows golden-build result.

## Honest limits

The gate protects established application behavior; it is not proof against
every possible image, catalog size, Windows configuration, or user action.
Tests use synthetic provider evidence rather than downloading or running
Florence, YuNet/SFace, MediaPipe, or FFmpeg on the workstation. They verify
provider/file-action orchestration and safety contracts, but not model accuracy,
GPU-driver compatibility, third-party package behavior, or visual output
quality. Large-catalog performance measurements and the real dataset/training
trial remain active roadmap work.

The user separately confirmed the packaged v0.27.17 Windows gate. The current
source gate validates v0.28.4 with synthetic data. Real CUDA tensor, Florence
inference/resume, and optional model accuracy remain separate live-machine
qualification work.
