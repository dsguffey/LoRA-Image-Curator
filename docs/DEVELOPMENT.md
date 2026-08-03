# Development and Release Verification

## Environment

Windows 11 is the primary target. Python 3.11+ with Tk is required. Install the
appropriate PyTorch build for the workstation first, then install
`requirements.txt`. Optional InsightFace/ONNX Runtime packages are selected by
the included installer because the correct runtime depends on the installed
CUDA generation.

Florence uses the native implementation in exactly Transformers 4.56.2 and the
pinned Microsoft model revision recorded in `florence_analyzer.py`. Do not add
`trust_remote_code=True`, loosen that dependency, remove safetensors-only
loading, or advance the model revision without a focused security and real
inference compatibility review.

Native Trash/Recycle Bin support is a standard safety dependency in
`requirements.txt`. Optional local body analysis uses `requirements-body.txt`
and `Install Body Analysis Dependencies.bat`. The recommended MediaPipe
model lives outside the source tree at
`%APPDATA%\LoRAImageCurator\models\body\pose_landmarker_full.task`.

Do not commit or package a virtual environment, model weights, catalogs,
thumbnail caches, logs, dependency snapshots, or real dataset material.

## Documentation standard

Document intent and risk boundaries rather than narrating syntax. A useful
docstring answers:

- What responsibility does this module/function own?
- What durable state or external resource can it change?
- What assumptions or compatibility constraints must future edits preserve?
- What failure behavior is deliberate?

Comments should explain non-obvious threading, transaction, path-safety,
performance, or UI-lifecycle decisions. Obvious assignments and control flow do
not need commentary.

## Focused checks

```powershell
python -m tools.compile_project
python -X dev -m tests.test_v0250_regression
python -X dev -m tests.test_v0252_regression
python -X dev -m tests.test_v0260_regression
python -X dev -m tests.test_v02721_regression
python tools\audit_project.py
```

## Golden-build release gate

The authoritative handoff command creates its own synthetic catalog and images,
then runs the complete maintained regression chain, audit, deterministic
release build, clean-extraction/overlay checks, and the current cumulative GUI
chain:

```powershell
python -X dev -m tests.test_golden_build
```

It never opens or edits a real catalog. The GUI phase requires a live Windows
desktop. `--no-gui` is available for headless development checks, but that
result is not sufficient to record a golden handoff.

## Historical regression chain

The oldest four milestone tests require a schema-compatible fixture catalog:

```powershell
python tools\run_regressions.py --fixture C:\path\to\fixture.db
```

Later regressions create isolated temporary catalogs. The runner stops at the
first failure and preserves each test's normal console output.

All maintained smoke, regression, and GUI checkpoints live under `tests/`.
Run an individual test as a module from the project root so application and
sibling-test imports resolve consistently on Windows and hosted automation.

## Release build

```powershell
python tools\build_release.py
```

The builder:

- runs the static project audit;
- includes source, tests, docs, launchers, and build tooling;
- excludes local/runtime/generated data;
- writes files in stable sorted order with deterministic ZIP metadata;
- adds a SHA-256 manifest;
- verifies archive CRC and forbidden-member rules.

Validate the resulting flat archive in two ways: extract it into an isolated
audit directory for CRC/manifest and dependency-light checks, then overlay it
onto a copy of the previous release to reproduce the supported DatasetTools
upgrade. The obsolete-file preflight must either pass or report only the exact
files documented for manual removal. The final GUI smoke test remains a Windows
release gate.

## Version discipline

- Update `app_identity.py`, `VERSION.txt`, `CHANGELOG.md`, and launchers together.
- Update `BUGS.md` whenever application files change.
- Keep speculative work in `WISHLIST.md`, not the active bug list.
- Do not change schema version, catalog identity, app-data location, or export
  formats without documenting a compatibility plan.
