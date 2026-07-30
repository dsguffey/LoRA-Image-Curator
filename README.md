# LoRA Image Curator

LoRA Image Curator is a local-first Windows desktop application for turning
large image collections into reviewed, documented, training-ready LoRA
datasets. It combines cataloging, local AI-assisted analysis, visual curation,
quality review, and non-destructive export in one workflow.

The application prepares image datasets; it does not train a LoRA itself.

| Project at a glance | |
|---|---|
| Current release | v0.27.18 pre-1.0 repository candidate |
| Primary platform | Windows 11, Python 3.11+ |
| Data model | Versioned SQLite catalog with SHA-256 content identity |
| Local analysis | Florence-2, optional InsightFace and MediaPipe |
| Tested scale | Approximately 14,000–17,000 local images |
| License | MIT for project source; third-party components retain their licenses |

> **Status:** Active pre-1.0 stabilization. v0.27.17 passed the complete
> live-Windows golden-build gate. v0.27.18 changes repository presentation,
> contributor guidance, and automated repository checks; application runtime
> behavior and catalog schema remain unchanged. Large-catalog measurements,
> active-provider shutdown/quarantine stress testing, and the first complete
> exported-dataset training trial remain on the roadmap.

## Why this project exists

Preparing a character LoRA dataset is not just a folder-sorting problem. Useful
curation requires durable image identity, provenance, review decisions,
repeatable analysis, transparent quality checks, and a safe way to export only
the chosen training material.

LoRA Image Curator treats the SQLite catalog as the source of truth while
keeping provider output, user decisions, and original files separate. Source
images remain outside destructive application control during analysis and
export.

## Core workflow

1. Create a catalog from one or more image folders.
2. Run local caption, face, and optional body/pose analysis.
3. Search, filter, compare, and review images in a paged thumbnail browser.
4. Prune a named image set with duplicate, quality, identity, and readiness
   evidence.
5. Quarantine questionable files reversibly or send confirmed deletions to the
   operating-system Recycle Bin.
6. Validate the final selection and export images, sidecars, provenance, and a
   training handoff without moving or modifying the sources.

## Engineering highlights

- **Content-addressed catalog:** SHA-256 identity keeps duplicate file paths
  separate from unique image content and supports compatible analysis reuse.
- **Transactional persistence:** schema migrations, catalog replacement,
  edits, undo/redo, quarantine metadata, and export history have explicit
  SQLite ownership.
- **Provider boundaries:** Florence-2, InsightFace/ONNX Runtime, MediaPipe, and
  FFmpeg remain attributable optional components rather than being hidden
  behind generic “AI” behavior.
- **Responsive desktop lifecycle:** long analysis, thumbnail, quality, export,
  and video tasks run outside Tk's event thread; GUI objects and callbacks are
  created and finalized on the GUI thread.
- **Non-destructive defaults:** analysis and export read or copy source images;
  file-moving actions require confirmation and recovery-aware handling.
- **Large-catalog controls:** paged thumbnails, bounded caches, indexed
  duplicate grouping, result-wide selection, and persisted image sets avoid
  catalog-wide widget growth and ordinary all-pairs work.
- **Reproducible releases:** a signed member manifest, static privacy/security
  audit, historical regression chain, deterministic ZIP builds, clean
  extraction, overwrite-preservation checks, and live GUI checkpoints define
  the release boundary.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module ownership,
invariants, and concurrency rules.

## Feature summary

- Local Florence-2 captioning, object detection, OCR, and triage evidence.
- Optional InsightFace detection and identity suggestions.
- Optional MediaPipe pose/body analysis with vetted model compatibility checks.
- Independent inclusive-by-default subfolder scopes for catalog import,
  captioning, face scanning, and face-reference folders.
- Boolean search, named searches, image sets, transactional undo, and combined
  face/body/catalog/readiness filters.
- Cached paged thumbnails, enlarged review with zoom/pan, and source-scene
  context for extracted video frames.
- Explicit duplicate review and preview-first “Remove Unnecessary Images”
  curation; the user retains final control.
- Readiness profiles for Flux, SDXL, SD 1.5, and general LoRA targets.
- FFmpeg frame extraction with fixed-interval estimates, scene-change support,
  provenance timestamps, and collision handling.
- Reversible quarantine, native Recycle Bin deletion, validation, and
  non-destructive export with manifests and handoff documentation.

The comprehensive user reference is [README.txt](README.txt).

## Installation from a clean checkout

### Requirements

- Windows 11 is the supported and tested platform.
- Python 3.11 or newer with Tk support.
- A PyTorch build appropriate for the computer's CPU/CUDA environment.
- FFmpeg only if video-frame extraction is needed.

Florence-2, InsightFace, and MediaPipe model weights are not bundled. Review
[MODEL_LICENSES.txt](MODEL_LICENSES.txt) before downloading or using models.

### Setup

1. Clone the repository or extract a release into a clean folder.
2. Open PowerShell in that folder and create a virtual environment:

   ```powershell
   py -m venv venv
   ```

3. Install the appropriate PyTorch build for the workstation.
4. Install the base dependencies:

   ```powershell
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

5. Launch the application:

   ```powershell
   .\Run LoRA Image Curator.bat
   ```

   Or launch Python directly:

   ```powershell
   venv\Scripts\python.exe app.py
   ```

An external virtual environment is also supported. The golden-build test
reports the project-source folder and Python runtime separately and rejects
project imports that escape the checkout under test.

### Optional components

| Capability | Setup |
|---|---|
| Face analysis | Run `Install Face Analysis Dependencies.bat` |
| Body/pose analysis and Recycle Bin support | Run `Install Body and File Action Dependencies.bat` |
| Video extraction | Install FFmpeg separately or select its executable in the video dialog |

### Upgrading an existing installation

Close the application and extract the release over the existing application
folder, allowing release files to be replaced. Preserve the virtual
environment, `output`, models, settings, catalogs, image sources, and caches.
The release preflight reports an exact retired filename if manual removal is
ever necessary. No v0.27.17 files are obsolete in v0.27.18.

## Safety and privacy

- The application does not upload images, captions, embeddings, identity names,
  or catalogs.
- Application/provider telemetry permission is disabled by default.
- Model and dependency downloads are separate, explicit third-party actions.
- Source images are read-only during analysis and export.
- Catalog replacement is staged and validated before publication.
- Catalog deletion is limited to a validated SQLite file and its exact
  sidecars.
- FFmpeg is invoked with an argument list and `shell=False`.
- Model weights, FFmpeg, catalogs, caches, logs, private datasets, and virtual
  environments are excluded from release archives.

Third-party packages, models, executables, websites, terms, and privacy
practices remain outside the application's trust boundary. See
[SECURITY.md](SECURITY.md) and
[THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md).

## Verification

The authoritative Windows release gate uses temporary synthetic data and never
opens or edits a real catalog:

```powershell
python -X dev test_golden_build.py
```

A passing default run establishes the maintained regressions, source
compilation, bounded privacy/security audit, two byte-identical builds, signed
member verification, clean extraction, overwrite preservation, and cumulative
live Tk checkpoints.

For a quick dependency-free repository check:

```powershell
python -m tools.compile_project
python tools\audit_project.py
python -X dev test_v02718_regression.py
```

`python -X dev test_golden_build.py --no-gui` is useful in a headless
environment, but it does not qualify a release as golden. See
[docs/GOLDEN_TEST.md](docs/GOLDEN_TEST.md) for exact coverage and limitations.

The repository also runs the dependency-free checks on supported Python
versions through GitHub Actions. Model execution, GPU compatibility, and live
Tk behavior intentionally remain workstation tests.

## Repository map

| Area | Responsibility |
|---|---|
| `app.py`, dialogs, `ui_*` | Desktop composition, user intent, progress, and Tk lifecycle |
| `catalog*.py`, `image_sets.py` | Schema, migrations, durable catalog state, and saved sets |
| `florence_analyzer.py`, `face_analyzer.py`, `body_analysis*.py` | Local provider adapters and normalized results |
| `catalog_browser.py`, `browser_workflow.py`, `dataset_readiness.py` | Visual review, filtering, selection, and readiness evidence |
| `file_actions.py`, `dataset_export.py`, `video_extraction*.py` | Recovery-aware file actions, export, and video handoff |
| `test_*.py`, `tools/` | Historical regressions, audit, fixtures, and deterministic packaging |
| `docs/` | Architecture, development, and golden-gate documentation |

`BUGS.md`, `ROADMAP.md`, `WISHLIST.md`, and `CHANGELOG.md` keep confirmed
defects, planned work, deferred ideas, and completed history distinct.

## Known limitations

- Windows 11 is the primary tested platform; Linux/macOS portability is not yet
  a supported release claim.
- Provider evidence is probabilistic and requires human review.
- Large-catalog timing observations are workstation baselines, not performance
  guarantees.
- FFmpeg and model weights must be installed separately.
- Provider cancellation is cooperative at safe item/task boundaries.
- Readiness scoring evaluates preparation evidence; it does not predict final
  LoRA quality.
- The first complete exported-dataset training trial remains pre-1.0 work.

See [BUGS.md](BUGS.md), [ROADMAP.md](ROADMAP.md), and
[WISHLIST.md](WISHLIST.md) for the detailed current state.

## Contributing

The project is in focused stabilization, so reproducible bug reports and narrow
fixes are preferred over broad feature expansion. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change and do not include
private images, catalogs, model files, credentials, or sensitive local paths in
public reports.

## Project authorship

Product direction, workflow design, acceptance criteria, hands-on Windows QA,
and release decisions are led by **David Scott Guffey**. Implementation has
been developed iteratively with AI assistance and validated through explicit
regressions, catalog-integrity checks, security-boundary reviews, deterministic
packaging, and extracted-archive verification.

- [David Scott Guffey on LinkedIn](https://www.linkedin.com/in/davidsguffey/)

## License

Project source is licensed under the [MIT License](LICENSE). Third-party
packages, models, and separately installed tools retain their own licenses; see
[MODEL_LICENSES.txt](MODEL_LICENSES.txt) and
[THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md).
