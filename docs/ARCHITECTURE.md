# Architecture

## Design objective

LoRA Image Curator turns unstructured image folders into a durable,
reviewable, training-ready dataset without making the application the owner of
the original files. The catalog records facts and decisions; provider output
remains attributable; export derives a handoff without rewriting source data.

## System boundaries

| Boundary | Owns | Must not own |
|---|---|---|
| `app.py` and dialogs | Tk lifecycle, user intent, confirmation, progress | Provider algorithms or direct schema evolution |
| Catalog services | Transactions, migrations, durable history | UI state or model execution |
| Provider modules | Local inference and structured results | User review decisions |
| Browser/readiness | Scoped filtering, selection, advisory scoring | Destructive file policy |
| Export/video services | Validated output and external-tool invocation | Silent overwrite or source deletion |
| Settings/cache | Local preferences and rebuildable previews | Catalog truth or training data |

## Durable data model

The SQLite catalog separates image content from file location:

- `images` identifies unique bytes by SHA-256.
- `files` records every known path for that content.
- analysis tables retain versioned provider output.
- face detections, embeddings, profiles, and matches remain normalized.
- manual tags and AI exclusions remain user-owned layers.
- review decisions, saved searches, image sets, export history, and bounded
  edit history are explicit catalog objects.

`PRAGMA user_version` is the schema contract. Migrations are sequential,
transactional, and forward-only. A newer unsupported schema is rejected rather
than opened optimistically.

The v0.25.0 public rename introduces a new catalog application marker while
accepting the historical `Dataset Tools` marker. Schema version 11 adds only
optional body-model/result tables.

## Browser pruning model

`browser_workflow.py` is the non-GUI contract shared by the Thumbnail Browser
and readiness model:

1. An optional saved image set defines the candidate dataset.
2. Readiness findings are calculated over that complete scope.
3. Any/All issue matching and ordinary catalog-state filtering narrow it.
4. Text search and sorting operate on the resulting view.
5. Selection commands apply across every result page unless explicitly labeled
   Current Page.

Readiness issue membership comes directly from `dataset_readiness.py`; the
browser does not duplicate Blur, resolution, training-text, identity, or
similarity rules. Filter compositions and selection remain session-only.
Updating an image set is a separate explicit transaction that replaces only
membership.

## Provider pipeline

`analysis_pipeline.py` coordinates provider phases without embedding provider
logic in Tk:

1. Discover supported source images through one defensive policy.
2. Register content and paths in the catalog.
3. Reuse compatible stored results.
4. Run Florence-2 only for outstanding work.
5. Optionally build an InsightFace reference profile and analyze faces.
6. Publish human-readable reports and a structured completion summary.

`provider_coverage.py` is a read-only catalog projection for the Analysis tab.
It counts durable Florence, face, and body attempts/successes for present
catalog images, so provider cards remain accurate after restarts, cancellations,
and provider-by-provider runs.

Provider results are suggestions. Confirmation, rejection, manual tags, and
training-text composition remain separate user decisions.

## Settings and provider diagnostics

`settings_manager.py` owns one backward-compatible local preference record.
`settings_dialog.py` presents it by user function rather than implementation
order: Catalog & Paths, Image Captioning, Face Scanning, Body / Pose Scanning,
Video Extraction, filtering, and privacy/diagnostics. Provider and tool names
remain visible inside those functional pages.

Folder traversal is explicit per workflow. Catalog import, Florence input, face
input, and face-reference discovery each receive their own persisted Boolean
and default to including subfolders. A provider change must not silently alter a
different workflow's source scope.

The UI also distinguishes application defects from provider/tool diagnostics.
Navigation, selection, scrolling, persistence, and catalog-integrity faults are
application-owned. Model accuracy, generation-length warnings, runtime execution
providers, model loading, and FFmpeg behavior remain attributable to their named
third-party boundary.

## Concurrency model

- Long-running provider, thumbnail, quality, export, and FFmpeg operations run
  outside Tk's event thread.
- Video-duration probing also runs outside Tk. Existing-frame Skip mode writes
  to a same-drive staging directory and merges only unoccupied deterministic
  names instead of guessing a numeric restart point.
- Background workers return immutable or queue-safe result records.
- Only the GUI thread creates widgets and `PhotoImage` objects.
- Scheduled Tk callbacks are owned and cancelled during shutdown.
- Cancellation is cooperative at item/phase boundaries so partial reports and
  committed catalog work remain inspectable.

## Thumbnail strategy

The browser uses two disposable cache layers:

1. disk-backed WebP previews beneath the per-user application-data directory;
2. a bounded least-recently-used cache of decoded Tk images.

Page widgets are rebuilt, but recently viewed decoded images remain warm.
Neither cache participates in catalog correctness and both can be deleted.

## Safety invariants

- Analysis never modifies source image bytes.
- Export copies; it does not move or delete source files.
- Existing output files require collision handling and are never silently
  replaced.
- Catalog creation and replacement use staging plus validation.
- Catalog deletion is limited to a validated database and exact WAL/SHM files.
- User strings are never evaluated as Python or interpolated into a shell.
- InsightFace model names are one path component beneath `<root>/models`.
- Automatic model download requires explicit license acknowledgement.
- Quarantine is the only reversible source-file move and records every path.
- Delete requests native Trash/Recycle Bin behavior and has no permanent
  fallback.

## Body-analysis provider boundary

`body_analysis.py` normalizes MediaPipe output before it reaches import or
browser code. The adapter produces pose count, body presence, visible-face
evidence, completeness, classification, and JSON landmarks. This keeps a
future vetted ONNX provider from forcing filter or schema consumers to
understand provider-specific objects.

`body_analysis_runner.py` owns model provenance, schema-11 reuse, and per-image
commits. `catalog_import.py` invokes the same adapter before registration only
when the user explicitly enables an import exclusion.

The current provider performs local inference and contains no telemetry or
network code. Dependency/model installation is separate and explicit. Future
providers must declare compatibility, license, hardware, network, and telemetry
metadata before sharing the boundary.

## Physical file actions

`file_actions.py` is the only module authorized to move source images or
request native Trash/Recycle Bin behavior. A catalog image can represent
several physical paths, so confirmation discloses both counts. Quarantine rolls
a move back if catalog mutation fails; restore refuses occupied paths.

## Documentation convention

Code documentation prioritizes maintenance:

- module docstrings explain ownership and architectural purpose;
- public functions document contracts and side effects;
- comments capture constraints, rejected shortcuts, and non-obvious lifecycle
  behavior;
- names and tests carry ordinary implementation detail;
- release documents record user-visible behavior and verification evidence.

The module count is intentional where files have separate ownership, safety, or
test boundaries. Consolidation is appropriate only when two modules truly share
one responsibility and the focused and golden tests prove the change; reducing
the visible file count alone is not an architectural objective.
