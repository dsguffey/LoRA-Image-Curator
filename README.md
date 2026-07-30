# LoRA Image Curator

LoRA Image Curator is a local-first Windows desktop application for turning
large image collections into reviewed, documented, training-ready LoRA
datasets.

It combines a versioned SQLite catalog, local Florence-2 captioning, optional
InsightFace and MediaPipe body analysis, visual curation, duplicate and quality
review, reversible quarantine, native Recycle Bin deletion, dataset readiness
checks, video-frame extraction, and non-destructive export in one workflow.

> **Project status:** v0.27.17 is the current Milestone 11A Git-ready candidate.
> It has been exercised with roughly 14,000–17,000 local images; first launch is
> about five seconds and the first 14,000-image Browser load about three seconds
> on the primary test workstation. Function-organized Settings now make catalog,
> Florence, InsightFace, MediaPipe, and FFmpeg ownership explicit. Independent
> subfolder scopes default on, temporary green provider markers identify the
> shared progress bar, and provider/tool diagnostics are distinguished from
> application defects. Browser thumbnail and Tk cleanup are deterministic after
> the first v0.27.14 live-Windows gate exposed a shutdown race. v0.27.16 added
> repository-readiness documentation and public issue templates; v0.27.17
> isolates the long historical Tk replay and explicitly verifies the tested
> source path separately from its Python environment. Broader stress
> measurements, risky active-provider shutdown/quarantine QA, and final
> live-Windows golden verification remain.

## Why this project exists

Preparing a character LoRA dataset is not just a folder-sorting problem. Useful
curation requires durable identity, provenance, review decisions, repeatable
analysis, transparent quality checks, and a safe way to export only the chosen
training material. LoRA Image Curator treats the SQLite catalog as the source
of truth while keeping original images outside destructive application control.

## Highlights

- Catalogs images by SHA-256 content identity while preserving multiple paths.
- Reuses compatible analysis instead of recomputing unchanged images.
- Keeps catalog-import, Florence input, face input, and face-reference subfolder
  scopes independent; each defaults to including subfolders.
- Organizes Settings by function while naming the active third-party
  provider/tool on each page.
- Shows checked/total, successful, failed, and remaining catalog coverage for
  Florence, InsightFace, and MediaPipe.
- Runs all configured providers together or Florence, face, and body analysis
  independently, with safe Pause/Resume and compatible-result reuse.
- Shows whether Florence, InsightFace, and MediaPipe are using a detected GPU
  or CPU path; logs record the actual device chosen for each run.
- Uses one shared provider progress bar, a named Current work heading, and a
  temporary green marker on only the provider that is actively running.
- Runs Florence-2 caption, object-detection, and text-overlay analysis locally.
- Supports optional InsightFace detection and identity suggestions.
- Browses installed compatible InsightFace model packs with explicit validation.
- Supports optional local MediaPipe pose/body analysis with model compatibility
  checks and user-adjustable evidence thresholds.
- Can omit no-body and/or no-visible-face candidates during folder import.
- Combines independent face, body/pose, catalog-state, image-set, and readiness
  filters in the main browser.
- Distinguishes unrun face analysis from a completed No Face result and offers
  a visibility-only likely-non-person preset for conservative cleanup.
- Provides paged, cached thumbnails for responsive review of large collections.
- Avoids catalog-wide all-pairs duplicate work during ordinary Browser loading;
  bounded duplicate grouping is indexed and single-image evidence is on demand.
- Lets the user choose 25, 50, 75, or 100 images per browser page.
- Jumps directly to the first, last, previous/next, or ±10 pages.
- Keeps automatic tags, manual tags, exclusions, and review decisions separate.
- Supports Boolean search, named searches, image sets, and transactional undo.
- Makes active filtering conspicuous without filling the toolbar with a long
  textual summary.
- Keeps display filtering separate from preview-first selection curation.
- Selects or deselects multi-keyword matches across every filtered result page.
- Updates a progressively pruned image set from the exact browser selection.
- Scores readiness for Flux, SDXL, SD 1.5, and general LoRA targets.
- Extracts video frames through a separately installed FFmpeg executable.
- Records source-video and fixed-interval timestamps for extracted frames and
  displays them in browser details.
- Opens images from a bottom-right thumbnail control into a built-in enlarged
  review window with floating zoom, pan, Previous/Next, Fit, 100%, and
  source-scene context.
- Keeps dense Blur, duplicate, face, and body/pose evidence in a read-only Image
  Quality popup instead of crowding ordinary Image Details.
- Estimates fixed-interval output from video duration before extraction and
  confirms Overwrite, Skip Existing, or Cancel for matching numbered frames.
- Quarantines selected images reversibly and restores them without overwriting.
- Sends selected files to the operating-system Recycle Bin with no permanent
  deletion fallback.
- Can remove selected images and all dependent metadata from the catalog;
  explicit catalog-only removal and multi-image delete cleanup create a fresh
  backup, while one reviewed Recycle Bin deletion does not.
- Shows modal, cancellable progress for Quarantine, Restore, Recycle Bin, and
  complete catalog-record removal while keeping Tk responsive.
- Exports images, sidecars, manifests, and a handoff README without moving or
  modifying source images.

## Safety and privacy

The application is designed for local operation:

- It does not upload images, captions, embeddings, identity names, or catalogs.
- Application/provider telemetry permission is disabled by default. The current
  local MediaPipe path has no application-configured telemetry collector.
- Source images are read-only during analysis and export.
- Catalog replacement is staged and validated before publication.
- Catalog deletion is limited to a validated SQLite file and its exact sidecars.
- FFmpeg is invoked with an argument list and `shell=False`.
- Model weights, FFmpeg, catalogs, caches, logs, and private datasets are not
  bundled in the release archive.
- Third-party models, packages, apps, websites, terms, privacy practices, and
  outputs remain under their respective authors' or operators' control.

See [SECURITY.md](SECURITY.md) for the threat boundary and
[THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md) for the provider/model/app
responsibility and privacy notice.

## Architecture

The application uses a deliberately layered design:

| Layer | Responsibility |
|---|---|
| Tk interface | User intent, confirmation, progress, and widget lifecycle |
| Workflow services | Import, analysis, review, quality, export, and video handoff |
| Provider adapters | Florence-2, InsightFace, and normalized body-analysis execution |
| SQLite catalog | Durable content identity, metadata, history, and migrations |
| Disposable caches | Rebuildable thumbnails and session-only UI state |

Detailed module ownership, invariants, and concurrency rules are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Windows 11 is the primary tested platform.
- Python 3.11 or newer with Tk support.
- A PyTorch installation appropriate for the system's CPU/CUDA environment.
- Packages in `requirements.txt`.
- Optional face-analysis dependencies installed with
  `Install Face Analysis Dependencies.bat`.
- Optional body-analysis and native Recycle Bin dependencies installed with
  `Install Body and File Action Dependencies.bat`.
- Optional FFmpeg installed separately or selected from the video dialog.

Florence-2, InsightFace, and MediaPipe model weights are not bundled. Review
[MODEL_LICENSES.txt](MODEL_LICENSES.txt) before downloading or using models.

## Quick start

Close the application and extract each release directly into the existing
`DatasetTools` folder, allowing the ZIP to overwrite older release files. Keep
the existing virtual environment, catalogs, images, models, settings, and
caches. If a retired top-level Python file needs removal, the smoke test names
that exact file so it can be moved to a backup folder without disturbing user
data. No v0.27.16 files are obsolete when installing v0.27.17.

1. Create a virtual environment named `venv` beside `app.py`.
2. Install the appropriate PyTorch build for the computer.
3. Install the remaining requirements:

   ```powershell
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Launch:

   ```text
   Run LoRA Image Curator.bat
   ```

5. Create or open a catalog and analyze images.
6. Optionally run **Body / Pose Analysis** from **Analyze & Update Catalog**, or
   enable body/face evidence filtering during folder import.
7. Use **Filters** in Catalog Browser to combine image-set, face, body/pose,
   catalog-state, and readiness constraints; update the image set from the
   resulting selection.
8. Quarantine or restore files during pruning, or use Delete for the Recycle Bin.
9. Validate the final set and export from **Finalize & Export**.

The comprehensive workflow reference is [README.txt](README.txt).

## Testing

The source tree includes dependency-light regressions for schema migrations,
catalog editing, search, image sets, curation, export, performance boundaries,
theme/font compatibility, video extraction, model selection, and release
packaging. Tk smoke tests require a live display; the final supported check is
performed on Windows.

The authoritative release/handoff command uses only synthetic temporary data
and runs all maintained automated, package, and GUI gates:

```powershell
python -X dev test_golden_build.py
```

The default run requires a live Windows desktop. `--no-gui` is useful for
headless development checks, but does not qualify a build as golden.
See [docs/GOLDEN_TEST.md](docs/GOLDEN_TEST.md) for the exact coverage and
honest limits of that result.

Developer setup, the historical suite, and release verification are described
in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Repository and release files

- `app.py` — desktop composition and workflow orchestration
- `catalog.py` — schema, migrations, and catalog persistence
- `catalog_browser.py` — visual browser, selection, and thumbnail lifecycle
- `browser_workflow.py` — unified filter and result-wide selection semantics
- `face_analyzer.py` / `florence_analyzer.py` — provider implementations
- `body_analysis.py` / `body_analysis_runner.py` — normalized local pose provider
- `body_setup_dialog.py` — responsive provider/model compatibility feedback
- `file_actions.py` — reversible quarantine and recoverable trash operations
- `dataset_export.py` — non-destructive training handoff
- `docs/` — architecture and development documentation
- `tools/` — static audit, regression, and deterministic release helpers
- `GIT_READY_CHECKLIST.md` — public-repository screening and publication steps
- `BUGS.md`, `ROADMAP.md`, `WISHLIST.md`, `CHANGELOG.md` — project history

## Development approach

Product direction, workflow design, acceptance criteria, hands-on Windows QA,
and release decisions are led by **David Scott Guffey**. Implementation has
been developed iteratively with AI assistance, with explicit regression tests,
catalog-integrity checks, security-boundary reviews, and extracted-archive
verification used to validate each release.

## Author

Created by **David Scott Guffey**.

- [LinkedIn](https://www.linkedin.com/in/davidsguffey/)
- For non-sensitive bugs and feature requests, use GitHub Issues once the
  repository is published.

## License

The application source is licensed under the [MIT License](LICENSE).
Third-party packages, models, and separately installed tools retain their own
licenses and are inventoried in [MODEL_LICENSES.txt](MODEL_LICENSES.txt).
LoRA Image Curator does not control or assume responsibility for those
third-party products; see [THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md).
