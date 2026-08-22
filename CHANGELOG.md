# LoRA Image Curator Changelog

## v0.28.4 — Pre-Feedback Workflow UI Pass

### v0.28.4.5 follow-up

- marked initial large-catalog validation complete after practical testing at
  roughly 14,000–17,000 images; deeper sustained-load and interruption testing
  remains optional follow-up work rather than a blocker for community feedback

### Changed

- renamed the combined action to **Update Catalog & Run Enabled Analysis** and made its
  order explicit: catalog registration, optional default-on Quality Analysis,
  then Florence and selected providers
- placed primary Quality Analysis controls under Analyze & Update Catalog while
  retaining cached status and missing-analysis warnings in Finalize & Export
- made Browser Filter Settings editable in place and synchronized the shared
  target, Blur threshold, duplicate similarity, overlay coverage threshold, and
  spatial-overlap rule across Browser, Settings, and Finalize & Export
- made the Finalize & Export LoRA target directly selectable
- duplicated catalog management and Export Training Data commands in File

### Added

- added **Prominent Overlay** to Browser readiness filters, Finalize readiness,
  and pre-export warnings. It combines Florence OCR rectangles with conservative
  Quality Analysis detection of obvious neutral bars/banners, then measures
  covered area instead of recognized character count
- added a shared 1–30% coverage control, defaulting to 5%, plus global Whole
  Image, Face, Body, Face or Body, and Face and Body region rules; the default
  checks a detected face or body
- made the Possible Duplicates readiness filter group duplicate clusters for
  direct review and keep cluster members adjacent in combined Any-filter views
- restored OCR text to ordinary search and added an explicit `ocr:` field while
  keeping OCR out of automatic tags and exported training text
- added v0.28.4 dependency-light orchestration/readiness/search contracts and a
  cumulative Windows GUI checkpoint

### Compatibility

- catalog schema 14 additively stores Quality Analysis bar/banner rectangles;
  schema 13's OCR rectangles remain intact, and opening an existing catalog
  changes no source images or reviewed metadata
- existing Florence captions remain reusable, but prior triage results are
  refreshed once when triage runs so spatial OCR boxes can be retained
- Quality Analysis algorithm version 2 refreshes its lightweight cache once to
  populate bar/banner candidates
- this is a Git/source update; the published Portable Source artifact remains
  v0.28.2 until a deliberate portable release update

## v0.28.3 — Independent Face Detection and Identity Matching

### Fixed

- an absent, invalid, or unusable InsightFace reference folder—and a blank
  Trigger Keyword—no longer prevents catalog images from being scanned
- Face Analysis now completes in an explicit detection-only fallback, storing
  normal face counts, bounding boxes, landmarks, and embeddings while creating
  no identity matches or trigger-word suggestions
- a later run with a valid reference profile can reuse the stored face
  detections and perform identity matching without rerunning inference
- standalone and integrated provider completion screens now warn that identity
  matching was skipped instead of reporting the entire provider as failed

### Compatibility and verification

- catalog schema remains 12; catalogs, face results, reviewed tags, settings,
  models, virtual environments, source images, and outputs require no migration
- added deterministic-provider regression coverage for failed-reference
  fallback, database integrity, CSV output, zero guessed suggestions, and later
  valid-reference reuse
- added a cumulative Windows GUI checkpoint for the detection-only completion
  warning; the v0.28.2 Portable Source artifact remains unchanged in this Git
  source update

## v0.28.2 — Slim Portable Source Distribution

### Added

- added the deterministic
  `LoRA_Image_Curator_Portable_Source_vX.Y.Z.zip` builder and a concise
  end-user guide that clearly states Python, dependencies, models, FFmpeg, and
  a private runtime are not bundled
- added a dedicated machine-readable Portable Source policy separate from both
  the full GitHub/source release and the future self-contained Windows x64
  payload policy
- made the Portable Source package include every signed top-level runtime/setup
  module plus only the launchers, requirements, licenses, notices, registry,
  SBOM, version, and package manifest needed by an end user

### Packaging and verification

- excluded tests, release tools, GitHub metadata, developer and planning
  documents, redundant one-off batch helpers, and the known-broken hidden VBS
  launcher from the end-user package
- continued to reject existing environments, catalogs, settings, models,
  caches, logs, datasets, exports, source images, archives, and credential-like
  artifacts rather than discovering files recursively
- added two byte-identical Portable Source builds, CRC and exact-inventory
  verification, package-specific SHA-256 manifest validation, and clean
  extraction to the golden release gate
- catalog schema remains 12; application behavior, providers, user data,
  models, caches, and existing virtual environments require no migration

## v0.28.1 — Explicit Provider Setup and Downloads

### Changed

- Florence Run / Restart now performs an offline check for the exact pinned
  cache revision and displays the model, publisher, approximate 1.54 GB size,
  source, license, and cache location before any missing model is downloaded
- the Florence loader uses local-files-only mode unless that specific run was
  approved to download, preventing lower-level Transformers behavior from
  bypassing the application confirmation
- the InsightFace `buffalo_l` prompt now gives its approximate 326 MB size,
  publisher, source, destination, and non-commercial-research restriction;
  custom model names must already point to a user-supplied/licensed pack
- Body / Pose Analysis now checks packages and the selected model before its run
  window opens; the missing recommended Google model uses visible progress and
  the existing pinned-size/SHA-256/atomic-publish downloader

### Setup and compatibility

- added `Tools > Open Setup & Repair…`, which closes the GUI before opening the
  existing Setup & Launch assistant; later repair and first-time installation
  therefore share one dependency, PyTorch, optional-provider, and FFmpeg path
- package checks never download anything, every model transfer requires
  explicit approval, FFmpeg remains user-installed, and package/runtime changes
  remain outside the running application
- catalog schema remains 12; catalogs, provider results, settings, images,
  models already present, outputs, caches, and virtual environments require no
  migration or deletion
- added v0.28.1 offline-cache, explicit-consent, shared-setup, release, and
  cumulative GUI contracts

## v0.28.0 — Portable Provider-Provenance Foundation

### Fixed

- preserved the acknowledged third-party notice revision when the main window
  saves settings during shutdown; the notice now remains dismissed on later
  launches until its release-owned revision changes
- removed the duplicate smart-launch environment inspection and added flushed
  package, graphics-runtime, and application-start messages so startup shows
  immediate progress instead of appearing stalled
- corrected the Windows first-launch sequence so the third-party notice is
  mapped and raised before it becomes modal; the notice is no longer transient
  to the deliberately hidden application root, which could leave startup
  waiting on an invisible dialog

### Added

- added `provider_registry.json` as the machine-readable source of third-party
  publisher, artifact, function, tested identity, revision, official source,
  download hosts, license, usage restriction, approximate size, integrity
  scope, redistribution status, and bundle status
- added a deterministic SPDX 2.3 source/provider inventory generated from the
  registry; the future portable Windows build must augment it with the exact
  private runtime, wheel files, transitive packages, and artifact hashes from
  that staged payload
- added a concise first-launch third-party/warranty acknowledgment; its single
  OK button stores only the current notice revision in local settings and opens
  the application, while closing the window exits without recording acceptance;
  no timestamp, identity, telemetry event, or network request is created
- clarified that LoRA Image Curator collects no telemetry data, some third-party
  tools may have their own telemetry, and the defaults are telemetry-off
- added a machine-readable portable-payload policy that separates the full
  source archive from the future end-user archive and explicitly excludes
  tests, release tools, GitHub metadata, developer documents, source-only setup
  files, existing environments, models, catalogs, caches, logs, datasets, and
  exports from that portable payload

### Security and setup

- replaced MediaPipe's moving `latest` model URL with Google's version-1 Pose
  Landmarker Full asset; setup enforces HTTPS and the registered Google host,
  downloads to a partial file, verifies the exact 9,398,198-byte length and
  SHA-256, and atomically publishes only a verified file while preserving an
  existing working model on failure
- changed the ordinary launcher to run a real required-runtime check before Tk
  starts; missing or incompatible dependencies route to guided setup, and an
  NVIDIA computer can no longer fall through silently to CPU-only PyTorch
- setup can display the release-owned provider record without installing
  anything; existing v0.27.23 CUDA repair, Florence checkpoint, large-catalog
  result reuse, and provider-data locations remain unchanged

### Packaging and compatibility

- source archives are now explicitly named
  `LoRA_Image_Curator_Source_vX.Y.Z.zip`; the later portable artifact reserves
  `LoRA_Image_Curator_Portable_Windows_x64_vX.Y.Z.zip`
- catalog schema remains 12; catalogs, models, provider caches, outputs,
  images, settings other than the new notice-revision field, and existing
  virtual environments require no migration or deletion
- added dependency-free v0.28.0 registry, verified-download, notice,
  portable-policy, SBOM, smart-launch, release, and cumulative GUI contracts

## v0.27.23 — NVIDIA Runtime Repair

### Fixed

- corrected base-setup ordering so PyTorch is selected before `timm` and its
  Torch/Torchvision dependency chain; a clean NVIDIA installation can no longer
  acquire CPU-only PyTorch silently and then have that accidental choice
  preserved as an intentional runtime
- made base repair detect the mismatch between visible NVIDIA hardware and a
  PyTorch build that cannot use CUDA, then route the user to an explicit
  PyTorch choice instead of reporting the environment as fully suitable for
  GPU inference

### Added

- added a Windows-qualified automatic repair for the official PyTorch 2.13.0 /
  Torchvision 0.28.0 CUDA 13.0 pair, with a minimum NVIDIA driver check before
  installation, a timestamped package snapshot, and a real synchronized CUDA
  tensor operation after installation
- the repair realigns an already-installed InsightFace/ONNX Runtime stack to
  the CUDA 13 package line; it does not install optional face packages when
  they were not already present
- setup status now distinguishes CUDA visibility from a successfully executed
  tensor operation and reports the CUDA architectures compiled into PyTorch
  during the focused repair

### Verification and compatibility

- Florence remains pinned to native Transformers 4.56.2 and the exact corrected
  checkpoint; catalog schema remains 12 and stored Florence results retain the
  v0.27.22 large-catalog resume contract
- the repair changes only the project-local `venv`; catalogs, models, settings,
  outputs, source images, caches, and separate application environments such as
  ComfyUI are outside its ownership
- added dependency-free v0.27.23 setup-order, driver-gate, CUDA-smoke, release,
  and cumulative Windows GUI contracts

## v0.27.22 — Florence Large-Catalog Recovery

### Fixed

- replaced the incompatible Microsoft/native pairing with Hugging Face's
  official Transformers-converted `florence-community/Florence-2-large-ft`
  checkpoint, pinned to
  `26b734a54fdfbf9c398351eedfabb7f27fc470b7`
- changed the loader to Transformers' native image-to-text auto class while
  retaining `trust_remote_code=False`, safetensors-only weights, exact
  Transformers 4.56.2, and native-module rejection
- added a fail-fast compatibility preflight that prepares caption, object
  detection, and regional OCR prompts and performs one bounded generation
  before the first unfinished catalog image

### Recovery and compatibility

- exact successful Florence results recorded with the former Microsoft
  checkpoint under Transformers 4.49.0 or 4.56.2 are reusable; new results are
  stored under the corrected checkpoint identity
- interrupted large catalogs can therefore retain completed caption/triage
  work and resume only the remaining images when stored-result reuse is enabled
- catalog schema remains 12 and no settings, catalogs, models, outputs, image
  sources, caches, or virtual environments are migrated or removed
- added dependency-free v0.27.22 recovery contracts and a cumulative Windows
  GUI endpoint; final release qualification still requires a live Windows run
  against a tiny catalog followed by a bounded resume of the real catalog

## v0.27.21 — Florence Provider Security Stabilization

### Security

- migrated Florence-2 from repository-supplied Python execution to the native
  implementation in the pinned `transformers==4.56.2` release
- disabled `trust_remote_code` explicitly for both model and processor loading
  and added a runtime check that rejects implementations outside
  `transformers.models.florence2`
- pinned `microsoft/Florence-2-large-ft` to Microsoft's verified
  `4a12a2b54b7016a48a22037fbd62da90cd566f2a` snapshot and required its
  safetensors weights rather than permitting pickle-weight fallback

### Improved

- made setup readiness reject an established environment with the old
  Transformers line and show the exact required repair version
- passed the processor's attention mask through native Florence generation,
  matching the maintained Transformers interface
- documented provider identity, model revision, license, third-party status,
  upgrade behavior, and the no-remote-code execution boundary

### Verification and compatibility

- added dependency-free v0.27.21 security contracts plus a cumulative Windows
  GUI endpoint; the exact Florence model still requires a real Windows/GPU
  inference smoke test
- existing catalogs remain schema 12 and require no migration; stored Florence
  results remain intact, but 4.49.0 results are not considered compatible with
  4.56.2 and will be regenerated only when the user starts Florence again
- model weights remain outside the source ZIP and release manifest

## v0.27.20 — Pre-Portable Source Stabilization

### Improved

- made `Send2Trash` part of the normal base dependency installation so native
  Recycle Bin behavior is automatic rather than presented as a heavyweight
  optional provider; the application still has no permanent-delete fallback
- narrowed the optional body installer to MediaPipe and its user-approved pose
  model download, with a clearly named `Install Body Analysis Dependencies.bat`
- moved all 84 maintained smoke, golden, regression, and GUI files from the
  repository root into `tests/` without discarding their cumulative coverage
- updated GitHub Actions, regression/golden runners, release packaging,
  development commands, README maps, and historical source-path assumptions for
  the dedicated test directory
- added a non-mutating clean-install checker and a documented three-phase real
  new-computer, post-setup, and remembered-settings upgrade procedure

### Verification and compatibility

- added focused v0.27.20 dependency/layout contracts and clean-install boundary
  tests, plus a cumulative v0.27.20 Windows GUI endpoint
- catalog schema remains 12; catalogs, settings, models, outputs, datasets,
  caches, and local virtual environments require no migration
- ZIP-overlay upgrades must retire the old root-level `test_*.py` copies and
  `Install Body and File Action Dependencies.bat`; Git users should record
  those removals/moves with `git rm`

## v0.27.19 — Portable and Sane Source Setup

### Added

- added one double-clickable **Setup and Launch LoRA Image Curator** menu for
  first-time setup, readiness checks, required dependencies, optional features,
  FFmpeg detection, and application launch
- added a standard-library setup assistant that creates and owns the adjacent
  `venv`, reports required and optional tiers separately, and runs every pip
  action through that exact interpreter
- added a safe PyTorch flow that can install the official CPU build or validate
  and redirect a command copied from PyTorch's official selector into the local
  environment without executing shell operators or unrelated packages
- added a standalone required-dependency installer for users who prefer the
  existing one-task-per-batch-file layout

### Improved

- corrected the setup wrappers so they accept any installed Python 3.11 or
  newer instead of probing exact Python 3.11 first; this avoids Windows Python
  Manager failures on machines that have a newer supported runtime such as
  Python 3.14
- made the setup assistant print its full **Setup & Launch** title once and use
  a compact main menu afterward, reducing repeated console headings during
  checks, failures, and return-to-menu flows
- normal launch now opens guided setup when the local environment is missing;
  the diagnostic, face, and body installers give the same actionable setup path
- removed the body installer's unsafe fallback to whichever system `python`
  happened to be on PATH
- documented why Python and a venv remain necessary for the source release,
  which components are optional, and how the planned executable/installer will
  remove that burden for non-source users
- documented the ONNX Runtime 1.27 CUDA boundary: CUDA 12 stays below 1.27,
  CUDA 13 uses 1.27 or newer, CPU and GPU packages must not be mixed, and newest
  is not automatically compatible

### Verification and compatibility

- added a focused v0.27.19 regression for launcher portability, safe PyTorch
  command translation, dependency tiers, GitHub documentation, and release
  inventory
- catalog schema remains 12; application and user-data behavior are unchanged
- no v0.27.18 release files are obsolete

## v0.27.18 — Professional Repository Review

### Improved

- rewrote the public README around a first-time visitor: concise project status,
  core workflow, engineering highlights, clean-checkout setup, optional
  components, verification scope, limitations, authorship, and license
- separated clean installation from overwrite-in-place upgrade guidance
- recorded the user-confirmed v0.27.17 live-Windows golden pass instead of
  continuing to describe it as pending
- added valid GitHub issue-template metadata and a privacy-aware pull-request
  checklist
- expanded `.gitignore` so raw image/video datasets, local environments,
  generated build material, and local secret files are private by default;
  permission-safe public screenshots can be added deliberately under
  `docs/assets`

### Repository automation

- added a dependency-free GitHub Actions workflow that compiles the signed
  Python inventory, runs the bounded source/privacy audit, and verifies current
  repository contracts on Windows and Linux with Python 3.11 and 3.14
- kept provider/model downloads, GPU checks, and live Tk execution out of hosted
  automation; those remain explicit workstation verification

### Verification and compatibility

- advanced the regression, deterministic package inventory, and cumulative
  Windows GUI chain to v0.27.18
- catalog schema remains 12; application runtime behavior and user data are
  unchanged
- no v0.27.17 files are expected to be obsolete

## v0.27.17 — Isolated Windows GUI Gate

### Fixed

- isolated the v0.27.10-and-earlier cumulative Tk replay in a strict child
  process so destroyed historical Tcl interpreters cannot be finalized during
  newer lifecycle checkpoints at garbage-collection-dependent times
- retained strict failure behavior: non-zero child exits and every stderr
  diagnostic still reject the golden build
- added explicit golden output for the tested project-source folder and Python
  runtime, plus a hard check that application identity imports come from the
  source folder under test

### Verification and compatibility

- advanced the maintained regression, package inventory, and cumulative
  Windows GUI chain to v0.27.17
- catalog schema remains 12; no application behavior or user-data migration
  changed in this release-gate correction
- no v0.27.16 files are expected to be obsolete

## v0.27.16 — Git Repository Readiness

### Added

- added `GIT_READY_CHECKLIST.md` to make the first public Git snapshot
  repeatable, including privacy screening, release verification, and
  post-publish checks
- added GitHub bug-report and feature-request templates that ask for workflow
  evidence while warning users not to post private datasets, catalogs, logs,
  model files, credentials, or sensitive paths
- added `.gitattributes` to normalize public text files and keep local binary
  artifacts from receiving text diffs if they ever appear outside the release
  boundary

### Verification and compatibility

- advanced the maintained regression, package inventory, and cumulative
  Windows GUI chain to v0.27.16
- catalog schema remains 12; no application behavior or user-data migration
  changed in this repository-readiness pass
- no v0.27.15 files are expected to be obsolete

## v0.27.15 — Deterministic Tk Shutdown

### Fixed

- Browser thumbnail workers now capture plain task data and a thread-safe
  result queue instead of retaining the complete Tk Browser frame
- decoded thumbnail and detail `PhotoImage` references are released explicitly
  on Tk's GUI thread before the interpreter is destroyed
- Browser and Readiness callbacks detach during shutdown, breaking ordinary
  Python ownership cycles back to the application
- input-folder counting follows the same queue-only worker ownership rule

### Verification

- added static regressions for worker closures, image release, callback
  detachment, version consistency, and release-chain ownership
- added a live Tk checkpoint that retains a synthetic decoded image, shuts down
  the Browser, forces garbage collection, and rejects unraisable finalizers
- advanced the maintained regression and Windows GUI chains to v0.27.15
- catalog schema remains 12; no release files are obsolete and no user-data
  migration is required

## v0.27.14 — Git-Ready Workflow Clarity

### Added

- reorganized the Settings menu and central Settings window around user
  functions: Catalog & Paths, Image Captioning, Face Scanning, Body / Pose
  Scanning, Video Extraction, Filter Settings, and Privacy & Diagnostics
- named Florence-2, InsightFace/ONNX Runtime, MediaPipe, and FFmpeg inside their
  functional pages instead of using generic provider numbering in Settings
- added independent, persisted subfolder choices for catalog import, Florence
  input, face input, and face-reference folders; all retain the inclusive default
- added a shared **Current work** heading and temporary green provider markers;
  the two shared-workflow markers explicitly point to the single progress bar
- added user-facing guidance that distinguishes application defects from
  third-party provider/tool limitations and diagnostics

### Fixed

- read-only Training Tags and Image Details surfaces now route mouse-wheel input
  to their outer inspector instead of making scrolling appear to stick
- all four Windows launcher/diagnostic banners now report the current version;
  a regression prevents them from silently drifting again

### Provider note

- verified that the official Florence-2 Transformers example requests
  `max_new_tokens=1024` for object detection; the app already runs one image and
  one task at a time, so the observed 1,024-token message is recorded as a
  provider/runtime warning rather than a whole-job batching failure
- retained the documented token request while failed-image counts and
  Transformers compatibility are tested; no speculative output truncation was
  introduced

### Verification and compatibility

- added dependency-light coverage for subfolder discovery, settings round-trip,
  wheel routing, temporary progress markers, functional menu organization, and
  launcher synchronization
- advanced the maintained Windows GUI chain to v0.27.14
- catalog schema remains 12; source images, existing catalogs, provider results,
  and exports require no migration
- no v0.27.13 files are expected to be obsolete

## v0.27.13 — Startup Worker Ownership

### Fixed

- provider-device inspection now captures only plain setting values and its
  thread-safe result queue before starting
- slow PyTorch or ONNX Runtime inspection can no longer retain the complete Tk
  application beyond GUI teardown and finalize Tk variables on its worker

### Verification

- strengthened the original provider-worker regression to reject every
  `self` or Tk-variable access inside the worker
- retained the unraisable-exception capture and strict GUI-stderr boundary that
  correctly rejected the v0.27.12 Windows run
- advanced the maintained chain to 36 non-GUI regressions plus the cumulative
  v0.27.13 Windows GUI entry point

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.12 files are expected to be obsolete

## v0.27.12 — Strict Tcl Callback Lifecycle Verification

### Fixed

- synchronous enlarged-view actions now cancel any delayed Configure redraw
  before rendering, so they cannot discard the only stored Tcl timer ID
- viewer shutdown is explicitly idempotent and refuses to queue new redraws
  once destruction begins
- the golden runner now treats any GUI-child stderr diagnostic as a failure,
  including Tcl background `after` errors that do not raise a Python exception

### Verification

- added a live Windows regression reproducing the exact sequence that escaped
  v0.27.11: queue delayed redraw, render synchronously, then close
- added a headless regression proving an exit-zero GUI child that writes an
  asynchronous callback diagnostic cannot receive the golden success verdict
- advanced the maintained chain to 35 non-GUI regressions plus the cumulative
  v0.27.12 Windows GUI entry point

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.11 files are expected to be obsolete

## v0.27.11 — Clean GUI and Synthetic Overlay Verification

### Fixed

- enlarged image review now owns and cancels both idle and delayed redraw
  callbacks before destroying its Tcl widget command
- historical GUI fixtures collect destroyed dialog/variable cycles on the Tk
  main thread, preventing Python 3.14 `Variable.__del__` warnings
- the golden overlay check no longer copies the installed `DatasetTools`
  directory, including its virtual environment, catalogs, and local archives

### Verification

- the current cumulative GUI entry point captures and fails on any unraisable
  cleanup exception instead of allowing a warning-bearing golden pass
- added a live viewer check proving its queued redraw is removed from Tcl
  before the window closes
- added a synthetic overlay fixture that proves release files are replaced
  while representative `venv`, `output`, and archived files remain unchanged
- clean-extraction and overlay stages now print progress before running

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.10 files are expected to be obsolete

## v0.27.10 — Manifest-Bounded Installed Release Validation

### Fixed

- source/security auditing now reads the signed project inventory instead of
  recursively treating every file beneath an established `DatasetTools`
  installation as application source
- arbitrary unmanifested local archives, including `Old Files to be trashed`,
  no longer trigger SQLite or documentation rules intended for shipped code
- release packaging now uses the same manifest boundary, preventing local
  source-like folders from being swept into a ZIP

### Verification

- added a regression containing the exact archived bare-SQLite pattern that
  stopped the Windows v0.27.9 run
- proved the archived copy is ignored while the identical pattern still fails
  when it appears in a manifested project file
- advanced the golden chain to 33 maintained non-GUI regressions plus the
  cumulative v0.27.10 Windows GUI entry point

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.9 files are expected to be obsolete

## v0.27.9 — Python 3.14 SQLite Resource Safety

### Fixed

- every maintained regression now explicitly closes direct SQLite connections
  instead of relying on the transaction-only connection context manager
- Windows can remove each synthetic test database immediately after its test,
  preventing `WinError 32` during temporary-directory cleanup
- `Catalog` now closes its connection when initialization or schema validation
  raises before the object can enter its normal context-manager lifetime

### Verification

- added an audit rule that rejects bare `with sqlite3.connect(...)` usage
- added a Windows-relevant regression proving a rejected future-schema catalog
  can be deleted immediately after the failed open
- advanced the golden chain to 32 maintained non-GUI regressions plus the
  cumulative v0.27.9 Windows GUI entry point

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.8 files are expected to be obsolete

## v0.27.8 — Installed-Output-Safe Golden Audit

### Fixed

- the golden source/security audit now treats `output` as user-managed runtime
  data instead of public release source
- active catalogs and timestamped database backups inside `output` no longer
  stop verification in an established overwrite-in-place installation
- the release builder also excludes the complete `output` folder, including
  text reports that would otherwise match its public-document suffix policy

### Verification

- added a regression proving that `output\dataset_tools.db` is ignored while a
  database accidentally placed in actual source is still rejected
- added a release-inventory regression proving that runtime text reports do not
  enter the ZIP
- advanced the cumulative Windows entry point and golden documentation to
  v0.27.8 without changing application behavior or catalog schema

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.7 files are expected to be obsolete

## v0.27.7 — Bounded Golden-Build Compilation

### Fixed

- the golden-build source compilation gate now reads the signed release
  manifest and compiles only project-owned Python files
- the test no longer recursively enters the neighboring `venv`, model,
  catalog, cache, backup, or other user-managed folders
- a malformed third-party package test can no longer stop the golden test
  before its maintained regressions and Windows GUI checkpoints begin

### Verification

- added a regression fixture containing intentionally invalid Python beneath a
  synthetic `venv`; the bounded compiler verifies the manifested project file
  and ignores the unowned third-party file
- advanced the cumulative Windows entry point and golden-build documentation
  to v0.27.7 without changing application behavior or catalog schema

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.6 files are expected to be obsolete

## v0.27.6 — Golden-Build Verification

### Added

- one authoritative `tests/test_golden_build.py` command that creates its own
  synthetic catalog, runs the complete maintained regression history, audits
  source and documentation, verifies deterministic flat packaging, checks a
  clean extraction and overwrite overlay, and runs the current cumulative GUI
  chain on Windows
- dedicated v0.27.5 and v0.27.6 dependency-light and GUI coverage

### Changed

- deleting one image through the ordinary Recycle Bin workflow no longer
  creates a catalog backup before optional cleanup of its one complete record
- multi-image delete cleanup still creates a fresh backup; explicit
  catalog-only record removal retains its always-back-up rule
- current version references, test instructions, launchers, and package
  metadata now agree on v0.27.6

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.5 files are expected to be obsolete

## v0.27.5 — Single-Image Review Deletion

### Added

- a trash-can control in the enlarged image viewer for deleting the currently
  reviewed image without returning to the Browser grid

### Changed

- enlarged-view deletion delegates to the existing Browser Recycle Bin action,
  preserving the saved catalog-record setting and one-image confirmation rules
- the viewer closes after handing off the action so the normal Browser refresh
  can remove the image from current results

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no v0.27.4 files are expected to be obsolete

## v0.27.4 — Filter Settings and Image Review Clarity

### Added

- a dedicated **Settings > Filter Settings** page for the shared dataset target,
  whole-number Blur threshold, and described 96–100% duplicate-similarity value
- a small bottom-right maximize control on every Browser thumbnail
- a compact floating enlarged-view control strip with Previous/Next, zoom,
  Fit, 100%, external-viewer handoff, and return-to-Browser
- v0.27.4 dependency-light and Windows GUI smoke coverage

### Changed

- renamed the Browser filter dialog's **Quality** tab to **Filter Settings**
- kept on/off visibility choices in Browser Filters while moving shared
  threshold/profile controls into central Settings
- clarified that **Readiness > Possible Duplicates** is the on/off Browser
  filter and the 96–100% value only controls its strictness
- made Finalize & Export display the shared duplicate setting rather than
  owning a second threshold slider
- restored overwrite-in-place releases: ZIP members now extract directly into
  the existing `DatasetTools` folder rather than creating a versioned subfolder

### Fixed

- reserved a permanent Settings footer so **Save** and **Cancel** remain visible
  when Notebook content requests more height under Windows scaling
- replaced fresh-folder-only stale-file guidance with an exact obsolete-file
  list and instructions to move or delete only those named files

### Upgrade

- close the application, extract the ZIP directly into `DatasetTools`, and
  approve replacement of existing release files
- no files from v0.27.3 are obsolete in this update
- keep the existing virtual environment, catalogs, images, models, settings,
  logs, and caches

## v0.27.3 — Large-Catalog Curation and Responsive Bulk Actions

### Added

- responsive modal progress and cooperative cancellation for Quarantine,
  Restore, Recycle Bin, and complete catalog-record removal
- a built-in **Enlarge / Review** window with result-wide Previous/Next,
  mouse-wheel zoom, drag pan, Fit, keyboard navigation, video-scene context, and
  an external-viewer handoff
- a read-only **Image Quality** popup for Blur, duplicate, face, body/pose,
  threshold, analysis-time, and false-positive diagnosis
- a conservative **Show Likely Non-Person** filter preset combining completed
  No Face and No Body/Pose evidence without changing data or selection
- explicit **Face analysis not run** filtering so unprocessed images are not
  mislabeled as analyzed images with no face
- scoped mouse-wheel routing for canvas/elevator-backed application areas
- Browser refresh timing logs and v0.27.3 dependency-light/Windows GUI coverage
- release-folder preflight diagnostics for stale top-level Python modules

### Changed

- Browser startup no longer performs an all-pairs nearest-duplicate comparison;
  96–100% grouping uses indexed Hamming neighborhoods, single-image details are
  calculated on demand, and culling enriches only its explicit selection
- large record and file-location queries use bounded SQLite parameter batches
- body/pose strictness and completeness controls use whole-number percentages
  with a value-specific plain-language description
- duplicate similarity exposes only five described whole-number choices from
  96% through 100%; Blur uses a whole-number sharpness score
- Image Details keeps practical file, source-video, timestamp, caption, and
  catalog information visible while dense detector/quality evidence moves to
  the dedicated popup
- release ZIPs contain a versioned parent folder to discourage merged extraction

### Fixed

- retained **Also remove the complete catalog record when deleting an image
  file** after Settings Save and through later shared-settings rewrites
- kept Tk responsive during destructive batches so progress can repaint and a
  cancellation request can be accepted between files or database batches
- prevented the No Face filter from treating unrun face analysis as a negative
  detection result
- removed the normal Browser-load path that scaled quadratically with the count
  of quality-analyzed images

### Safety

- bulk actions remain modal so unrelated curation cannot race with destructive
  work; cancellation is cooperative and completed external file actions remain
  truthfully reported
- catalog record removal remains transactional and always follows a fresh
  SQLite backup; cancellation before commit rolls back the complete removal
- the likely-non-person preset is visibility-only and never auto-deletes
- Recycle Bin behavior still has no permanent-delete fallback

## v0.27.2 — Large-Catalog Safety and Provider Run Controls

### Added

- a vertical scrollbar for the complete Analysis tab so provider progress,
  messages, and controls remain reachable at smaller window sizes
- recursive **Images found** feedback beneath the selected input folder
- cooperative Pause/Resume for Florence and face runs, plus matching safe
  Pause/Resume inside the MediaPipe body-analysis run window
- explicit **Run / Restart** provider wording; compatible stored results remain
  skipped by default, including after cancellation or application restart
- per-provider device status showing the detected Florence, InsightFace, and
  MediaPipe execution path before a run
- durable video-origin manifests for extracted frames and schema-12 catalog
  fields for source video, sampling mode, frame number, interval, and timestamp
- source-video and timestamp details in the browser; a conservative fallback
  derives timestamps for a remembered legacy fixed-interval extraction
- **Browser > Selected Images** commands for quarantine, restore, Recycle Bin,
  and complete record removal, with keyboard shortcuts for the destructive
  workflows
- a **No image file found** filter for missing and intentionally recycled files
- v0.27.2 dependency-light regression and Windows GUI smoke coverage

### Changed

- Florence ETA now measures newly completed remaining inference work instead of
  treating already-reused catalog results as fresh throughput
- catalog discovery continues to report text progress as `x / y`, and provider
  no-image diagnostics identify the exact input folder and supported formats
- Recycle Bin deletion leaves catalog records by default; a conservative
  Settings option can remove each successfully deleted image's complete catalog
  record and all dependent provider, caption, tag, set, and review rows
- destructive confirmation is relative to the active browser page size:
  operations of at most one configured 25/50/75/100-image page proceed without
  an extra dialog; larger operations require confirmation
- selected-image physical actions now live in the Browser menu, matching their
  browser-only selection scope
- schema advances additively from 11 to 12 for video-origin metadata

### Fixed

- corrected the Analysis canvas palette reference so the Windows GUI can
  construct the new scrollable tab successfully
- added a dependency-light theme-contract regression so invalid
  `self.theme.<field>` references fail before a graphical smoke test
- provider-device inspection now copies face-model settings on the Tk main
  thread before starting its worker, preventing teardown-time Tcl errors and
  follow-on logging failures during short-lived GUI sessions
- updated the inherited v0.26.0 GUI assertion to reflect v0.27.2's
  page-relative confirmation rule; the removed always-confirm checkbox remains
  only as a settings-file compatibility field
- updated the inherited v0.27.0 GUI assertion to expect the current
  **Run / Restart Body** label and added a dependency-light guard against that
  historical test drifting back to superseded wording
- the top-level Windows smoke test now explains that its historical checkpoint
  chain intentionally opens and closes several temporary application windows

### Safety and acceleration

- every catalog-record removal creates a fresh SQLite backup immediately before
  the removal transaction
- catalog-only removal never touches image files; Recycle Bin use still has no
  permanent-delete fallback
- clearing removed-image edit history avoids keeping invalid undo snapshots;
  the fresh backup preserves the complete pre-removal catalog
- Florence continues to select PyTorch CUDA and FP16 when CUDA is available and
  logs the actual GPU; InsightFace continues safe CPU fallback when ONNX Runtime
  does not expose `CUDAExecutionProvider`
- the supported MediaPipe Python Pose Landmarker path remains CPU-backed rather
  than enabling an unverified GPU delegate

## v0.27.1 — Provider Coverage and Large-Catalog Workflow Polish

### Added

- independent **Run Florence**, **Run Face**, and **Run Body Analysis** controls
  while preserving **Start Catalog & Providers** for the complete configured run
- matching provider commands in the Tools menu
- persisted provider coverage for the active catalog: checked/total,
  successful, error-only, and remaining image counts; Florence also reports
  successful full-triage coverage
- compact **First**, **−10**, **Prev**, **Next**, **+10**, and **Last** browser
  page navigation for catalogs with hundreds of pages
- fixed-interval video duration probing and an up-front complete-video image
  estimate bounded by the configured maximum
- explicit **Overwrite / Skip Existing / Cancel** frame-name collision handling
- explicit **Replace / Merge / Cancel** handling when a video handoff targets
  an existing catalog
- v0.27.1 dependency-light regression and Windows GUI smoke coverage

### Changed

- moved remember-folder and stored-result-reuse choices from the Analysis tab's
  Run options panel into the appropriate Settings sections
- the face checkbox now clearly means **Include in Run All**; the independent
  Face Run button does not depend on that checkbox
- safe Skip Existing extraction uses a same-drive staging folder and merges
  only missing deterministic names instead of guessing a new start number

### Fixed

- physically grouped the Filters button with the Sort controls, matching the
  intended v0.27.0 layout
- **Clear Filters** now applies and closes immediately
- applying or clearing Filters is one complete session-only Undo/Redo action
- non-empty input/output folder choices save on focus loss instead of waiting
  for an unrelated later settings boundary
- closed FFmpeg process pipes explicitly after extraction, including in
  development-mode regression tests

### Safety

- filter history is session-only and never writes catalog metadata
- Skip Existing never overwrites an occupied frame name
- Overwrite publishes from same-drive staging only after FFmpeg succeeds, then
  replaces only files matching the confirmed normalized prefix and format
- existing-catalog replacement still uses staged validation and atomic
  publication; source images and exports remain outside replacement scope
- schema remains version 11 and no dependency reinstall is required

## v0.27.0 — Browser Workflow and Settings Reorganization

### Changed

- removed the attached curation pane and moved its selection-changing criteria
  to a dedicated **Remove Unnecessary Images** dialog
- reorganized Filters into Scope, Face, Body / Pose, Quality, and Readiness
  sections
- split general, face, and body/pose state into composable filters so, for
  example, **Has face** and **Full body** can be applied together
- moved the Filters button beside Sort, removed the redundant `Filter:` label,
  active-filter summary, toolbar Clear button, and ellipsis
- added a conspicuous bold accent **Filters On** whole-button state
- added an organized browser-only Filters menu that focuses the same central
  filter dialog; **Clear Filters** now lives inside that dialog
- moved curation access to Selection and retained `N` as its shortcut
- added Body / Pose Analysis controls to Analyze & Update Catalog
- separated Settings menu access into Paths & File Actions, Analysis Settings,
  and Privacy & Legal
- moved Blur threshold editing to Analysis Settings; browser/finalization
  surfaces consume the same configured value

### Fixed

- Body Analysis Setup now opens a visible checking dialog immediately and runs
  MediaPipe model initialization on a worker thread instead of blocking Tk
- the same non-blocking setup boundary is used before a body-analysis run
- body filters are no longer buried in one mixed catalog-state dropdown

### Planning and safety

- added a dedicated large-catalog stress and pre-import-screening milestone for
  full-movie frame collections
- added a later general GUI/workflow review, large-image zoom viewer, and
  project-wide whole-control active-state review
- filter and curation semantics remain separate: filters only change visibility;
  curation only changes transient selection after an explicit preview
- no catalog schema, source-image mutation, quarantine, Recycle Bin, export, or
  provider-result format changed

## v0.26.0 — Body-Aware Curation and Reversible File Actions

### Added

- optional local Google MediaPipe Pose Landmarker provider with a normalized
  provider boundary, compatibility check, model fingerprint, and schema-11
  cached result storage
- opt-in import rules for skipping images with no detected body/pose and/or no
  visible-face pose evidence
- result-wide browser filters for body-analysis status, body/pose presence,
  full body, partial body, visible-face pose evidence, and multiple poses
- **Full-body evidence** browser sort and body evidence in image details
- Settings tabs for quarantine path, body model, user-adjustable detection,
  visibility, and 60–100% full-body completeness thresholds
- **Quarantine Selected** with collision-safe moves, durable path history, and
  **Restore Selected** without overwriting occupied original paths
- Delete-key and menu actions that use the operating system Recycle Bin through
  Send2Trash and never fall back to permanent deletion
- optional dependency/model installer with explicit package/model sources and
  confirmation before each network operation
- Privacy & Third-Party Products help, an About disclaimer, and
  `THIRD_PARTY_NOTICE.md`
- v0.26.0 dependency-light regression and Windows GUI smoke coverage

### Privacy and provider policy

- telemetry/provider-diagnostics permission is disabled by default
- every attempt to enable that permission displays the current collector, data,
  and purpose disclosure
- LoRA Image Curator implements no telemetry; the current MediaPipe analysis
  path is local and does not use the permission
- arbitrary executable provider packages remain blocked; only vetted provider
  and model paths are accepted in this release

### Safety

- quarantine discloses both selected catalog-image count and physical-file
  count because one image can have several known locations
- quarantine/restore rolls a filesystem move back if the catalog update fails
- native Recycle Bin failure stops with an error and does not permanently
  delete the source
- schema 11 is additive; prior catalog, face, quality, image-set, review, and
  export records are preserved
- the planned 100-file source-structure cleanup remains deferred to its own
  refactor/retest pass

## v0.25.3 — Clean GUI Smoke-Test Shutdown

### Fixed

- canceled Catalog Browser search, layout, load-more, and thumbnail-result
  `after()` callbacks during application shutdown
- added shutdown guards so late Tk callbacks return quietly instead of trying
  to run against destroyed widgets after the smoke test has already passed

### Safety

- no catalog schema, image-set, readiness-filter, analysis, export, model, or
  launcher behavior changed
- schema remains version 10

## v0.25.2 — Unified Browser Pruning Workflow

### Added

- one **Browser Filters** dialog that combines an image-set scope, the existing
  catalog-state filter, and all eleven Finalize & Export readiness findings
- **Select All Checks** plus Any/All matching so every readiness condition can
  be reviewed in one browser view or intersected deliberately
- result-wide **Select by Keyword** and **Deselect by Keyword** commands with
  comma-separated multi-keyword Any/All matching
- **Select by Image Set** in the browser Selection menu
- exact readiness-issue image membership in the shared report model so browser
  filters and final validation use the same evidence
- v0.25.2 dependency-light regression and Windows GUI smoke coverage

### Changed

- Catalog Browser is now the primary pruning workspace; Finalize & Export
  remains the readiness summary, local quality-analysis, and export handoff
- image-set filtering defines the dataset scope before duplicate, repeated
  training-text, resolution, Blur, and other readiness checks are calculated
- image-set management now uses one **Update Image Set** action that replaces
  membership with the exact current browser selection
- **Select Image Set in Browser** replaces the browser selection instead of
  silently adding to it
- Ctrl+A, Escape, and Ctrl+I now favor the complete current result set; clearly
  labeled current-page alternatives remain available
- readiness profile, Blur threshold, and duplicate similarity stay synchronized
  between Browser Filters and Finalize & Export
- Browser Filter results are cached across search keystrokes so duplicate and
  repeated-text analysis is not recomputed for every character typed

### Fixed

- Subject Thresholds no longer crowds two separate help icons into the numeric
  rows; one shared help icon explains both percentages
- clarified that `0.25` in **Small face below** means `0.25%` of the complete
  image and widened both numeric controls
- readiness issue links clear stale browser filters before revealing their
  authoritative result set

### Safety

- filters and browser selection remain session-only and non-destructive
- **Update Image Set** changes only saved membership; it never deletes catalog
  images or source files and confirms an intentional empty replacement
- identity and possible-duplicate findings remain warnings rather than
  automatic rejection rules
- schema remains version 10

## v0.25.1 — Windows Alt-Navigation Hotfix

### Changed

- browser page navigation now tracks physical Left/Right press and release state,
  accepting fast deliberate presses while consuming operating-system key repeat
- Alt navigation runs through a first-priority Tk bind tag attached to each
  keyboard-focus target, before widget classes and Windows menu traversal

### Fixed

- fixed the remaining Windows rubber-band behavior when Alt stayed held while
  one or more Left/Right keys were pressed
- claimed the Alt modifier on key-down while Catalog Browser is active rather
  than waiting for an Alt+arrow event or the final Alt release
- strengthened the v0.25 regression so it covers Alt key-down, three distinct
  Right strokes, repeat suppression, arrow releases, Alt release, and bind-tag
  priority

### Safety

- schema remains version 10
- no catalog, image, analysis, export, model, or cache behavior changed
- F10 and mouse access to application menus remain available

## v0.25.0 — Pre-1.0 Readiness

### Added

- renamed the public application to **LoRA Image Curator**
- added validated browsing for installed InsightFace model-pack folders
- added centralized public identity/version/author metadata
- added `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`,
  `pyproject.toml`, `.gitignore`, architecture/development documentation, and a
  core requirements inventory
- added static repository/security/documentation audit tooling
- added a deterministic release builder with SHA-256 member manifest and CRC,
  path, required-member, and forbidden-artifact verification
- added a safe historical regression runner that copies fixture catalogs before
  older migration tests
- added dedicated v0.25.0 headless and Windows GUI smoke coverage

### Changed

- new local settings, logs, and thumbnail caches use
  `%APPDATA%\LoRAImageCurator`
- new catalogs identify themselves as LoRA Image Curator while validation still
  accepts historical `Dataset Tools` catalogs
- About now identifies David Scott Guffey as the creator and the application
  source as MIT licensed
- the public README uses
  `https://www.linkedin.com/in/davidsguffey/` instead of publishing an email
- production module and public API documentation now follows an explicit
  architecture/intent/constraint standard

### Fixed

- added an Alt-release guard for browser Alt+Left/Right paging; subsequent live
  Windows testing showed the native menu path began on Alt key-down, which is
  corrected by v0.25.1
- prevented typed InsightFace model names from containing absolute paths,
  separators, or traversal components
- made model Browse derive the exact InsightFace root/name pair and reject
  folders outside the expected `<root>/models/<pack>` layout or without ONNX
  files

### Verification and safety

- schema remains version 10
- old catalog identities remain readable
- provider models, FFmpeg, catalogs, settings, caches, logs, and private data
  remain outside the release archive
- no new image-deletion, source-overwrite, network, or shell-command boundary
  was introduced

## v0.24.3 — Windows Smoke-Test Hotfix

### Changed

- circled question-mark help icons are now hover-only: they are not buttons and
  do not add keyboard-tab stops
- the Windows GUI smoke test now verifies only the supported hover behavior
- the v0.23.0 and v0.24.x GUI smoke tests close the application through its
  normal owned shutdown path

### Fixed

- removed a Windows-only smoke-test assertion that assumed a programmatic
  `focus_set()` call would synchronously grant focus to a Canvas
- prevented smoke-test teardown from bypassing cancellation of the app-level
  menu and message-queue `after()` callbacks

### Safety

- the repair is limited to transient GUI help and test teardown
- schema remains version 10; no image, catalog, provider, export, cache, or
  dependency behavior changed

## v0.24.2 — Hover Help and Teardown Hotfix

### Changed

- circled question-mark help icons now use hover and keyboard focus only
- clicking a help icon dismisses any visible hover tip instead of pinning help
- the Windows GUI smoke test now verifies hover/focus help and confirms click
  does not create a pinned popup

### Fixed

- cancelled app, browser, and menu `after()` callbacks during shutdown so the
  smoke test no longer leaves Tk console warnings after the GUI passes
- made tooltip cleanup destroy-safe when the owning widget disappears

### Safety

- the repair is limited to transient GUI help and shutdown state
- schema remains version 10; no image, catalog, provider, export, or dependency
  behavior changed

## v0.24.1 — Contextual Help Hotfix

### Changed

- clicking a circled question-mark icon now opens its tip immediately and pins
  it until the icon is clicked again or Escape is pressed
- hover and keyboard focus retain their delayed, non-pinned behavior
- strengthened the Windows GUI smoke test to generate real hover, leave, and
  click events and verify that the expected help text becomes visible

### Fixed

- made help-icon visual bindings additive so they no longer replace the
  tooltip's hover and keyboard-focus handlers

### Safety

- the repair is limited to transient GUI help state
- schema remains version 10; no image, catalog, provider, export, or dependency
  behavior changed

## v0.24.0 — Responsiveness and Contextual Help

### Added

- added a reusable circled-question-mark help icon beside technical labels,
  including Face Detection's Reference folder Browse row
- made help icons keyboard-focusable, theme-aware, and limited to concise
  contextual guidance
- added longer FFmpeg guidance under `Help > Video Extraction`
- added a bounded least-recently-used cache for decoded Tk thumbnail images so
  current and recently visited pages can reuse already loaded previews
- added `tests/test_v0240_regression.py` and `tests/test_v0240_gui.py`

### Changed

- moved primary analysis, face-provider, catalog, search, sort, filter, review,
  and curation explanations away from the associated input/button hit targets
  and onto nearby help icons
- kept dynamic issue explanations and the icon-only curation handle as ordinary
  tooltips, where hover help remains the appropriate affordance
- deferred FFmpeg discovery until after the video dialog is constructed and
  performs the executable probe in a background worker

### Fixed

- consumed repeated and boundary `Alt+Left`/`Alt+Right` events and debounced
  held-key repeats, preventing native menu handling from rubber-banding browser
  pages
- retained decoded thumbnails across bounded page rebuilds so returning to a
  recently viewed page does not visibly decode the same preview files again

### Safety

- decoded previews are bounded to 320 least-recently-used entries and remain
  disposable UI state
- FFmpeg discovery remains read-only and does not download or update software
- schema remains version 10; no source-image, catalog, provider, or export
  behavior changed and no dependency was added

## v0.23.0 — Visual Polish and Theme Picker

### Added

- added `Settings > Appearance Theme` with four curated themes: Clean Gray,
  Soft Light, Dark Workstation, and High Contrast
- centralized application palettes in `ui_theme.py` so ttk widgets, classic Tk
  canvases/text widgets, thumbnail cards, tag chips, duplicate groups, and the
  attached curation marker can share one visual language
- added live theme application and persistent theme settings without changing
  the catalog schema or adding dependencies
- added `tests/test_v0230_gui.py` for the visual/workflow polish smoke pass

### Changed

- made the default gray palette cleaner and more legible instead of relying on
  raw toolkit gray
- changed live browser search to debounce for 0.5 seconds after typing pauses
- changed `Esc` to deselect the current thumbnail page only
- changed `Ctrl+D` to deselect all selected images across pages/results
- kept `Ctrl+Shift+D` as an alternate current-page deselect shortcut

### Fixed

- made `Alt+Right` advance browser pages from result/page state instead of
  depending on the visible Next Page button state
- replaced Tcl-parsed `Segoe UI` and `Consolas` descriptions with cached
  `tkinter.font.Font` objects, preventing the Python 3.14/Tk startup crash that
  reported `expected integer but got "UI"`
- resolved thumbnail-card fonts through the card's real Tk frame rather than
  its plain Python controller, preventing catalog loading from stopping with
  `AttributeError: 'ThumbnailCard' object has no attribute '_root'`
- made the shared font helper tolerate a UI controller with a real Tk
  `outer` widget, so a partially overwritten v0.23.0 folder cannot recreate
  the thumbnail-card crash
- made live theme changes ignore a thumbnail-container empty-state label after
  catalog loading has destroyed it, preventing the v0.23.0 GUI smoke test from
  stopping with `invalid command name ...label`
- added a dependency-light regression that rejects literal widget font
  descriptions, verifies structured family/size/weight construction, and
  prevents thumbnail controllers from being mistaken for Tk widgets; added a
  lifecycle regression for theme changes after the empty-state label is gone

### Safety

- theme choices are ordinary GUI preferences stored in settings JSON
- schema remains version 10 and no source images, catalog records, or export
  behavior are changed by the appearance picker

## v0.22.0 — Workstation UI

### Changed

- added a native menu bar organized around `File`, `Edit`, `Catalog`, `Tools`,
  `Settings`, and `Help`; browser-only `Selection`, `View`, and `Browser` menus
  appear only while the Catalog Browser tab is active
- moved Refresh, Image Sets, saved-search actions, search history, selection
  commands, and Export Selected out of the browser toolbar
- reduced the browser toolbar to search, Advanced Search, sort, and filter
- replaced the Curation Filters toolbar button and internal Hide Panel button
  with a thin attached edge marker, `View > Curation Filters`, and the `N`
  shortcut
- moved Video Sources above Catalog folders on Analyze & Update Catalog
- changed ordinary bounded pages from 96 to a configurable maximum of 100
  images; `Settings > Images per Browser Page` offers 25, 50, 75, and 100
- placed Next Page immediately to the right of Previous Page and kept its label
  stable while disabling it on the final page
- changed Ctrl+A in the thumbnail browser to select only the current page;
  selecting every filtered result is now the explicit Ctrl+Shift+A command

### Added

- one chronological browser Undo/Redo history for selection changes and durable
  tag/review edits, used by Ctrl+Z, Ctrl+Y, and Ctrl+Shift+Z
- selection commands for selecting, deselecting, and inverting the current page
  or the complete result set
- focus-aware shortcut routing so text fields retain standard Cut, Copy, Paste,
  and Select All behavior
- common browser shortcuts including Ctrl+F, Ctrl+E, F5, N, and page navigation
- a Help menu with Getting Started, tab-specific guidance, complete shortcut
  reference, face-analysis guidance, licensing, and About
- consequence-oriented tooltips for the main workflow, browser, paging, and
  curation controls
- `tests/test_milestone_10_phase1c.py` and `tests/test_v0220_gui.py`

### Fixed

- explicitly disabled Tk's synthetic tear-off entry on the application menu bar
  and made the v0.22.0 GUI smoke test ignore any unlabeled toolkit entries;
  this prevents `_tkinter.TclError: unknown option "-label"` on Python/Tk 3.14
- Ctrl+Z/Ctrl+Y no longer ignore selection history and unexpectedly rebuild the
  grid at page one merely because a durable catalog edit was chosen instead
- `Select Visible` no longer silently selects results on other browser pages
- browser-only commands no longer clutter unrelated workflow tabs

### Safety

- all browser removal language remains deselection-only; no Cut, Move, Paste,
  or Delete operation was added for image files
- curation remains preview-first and never deletes source images or catalog
  records
- schema remains version 10 and no new dependency is required

## v0.21.0 — Workflow Feedback and Curation

### Fixed

- replaced the indefinitely growing thumbnail canvas with bounded 96-image
  pages, preventing deep-scroll clipping on Windows while preserving selection
  across pages
- kept automatic end-of-scroll loading within the current page only; advancing
  to another page is explicit and cannot unexpectedly replace the current view
- changed the provider progress bar from per-phase resetting to one monotonic,
  workload-weighted workflow bar covering Cataloging, Florence analysis, and
  optional Face analysis
- promoted important phase transitions such as Florence model loading to a bold
  status area directly above the progress bar

### Added

- measured per-phase elapsed time and ETA after enough real images have
  completed to support a useful estimate
- an amber long-run notice when measured remaining time reaches ten minutes;
  the existing cooperative Cancel Run control remains available and completed
  results remain reusable
- a visible Florence note explaining that large collections can take much
  longer than cataloging
- an in-browser **Remove Unnecessary Images** curation panel with independent
  checkboxes for rejected, missing/unreadable, low-resolution, Blur,
  screenshot/UI, no-person/no-face, small-face, face-prominence,
  any-multiple-person, and near-duplicate evidence
- adjustable main-face-size and second-face-prominence thresholds
- second-largest detected-face area in the browser projection and image details
- distinct **Undo Selection** and **Redo Selection** controls for transient
  selection actions, including curation, without colliding with Ctrl+Z/Ctrl+Y
  for durable catalog edits

### Curation behavior

- several people are no longer automatically treated as unnecessary by the
  default curation profile
- **Multiple similarly prominent faces** compares stored face bounding boxes;
  background faces are retained when they are much smaller than the main face
- **Any multiple people or faces** remains available as an explicit stricter
  option
- all curation remains preview-first and selection-only; no image, catalog
  record, review decision, tag, or image set is deleted or changed

### Testing

- added `tests/test_milestone_10_phase1b.py` and `tests/test_v0210_gui.py`
- expanded Milestone 8F regressions for granular checks, small-face metrics,
  background-person retention, and similarly prominent faces
- retained the complete v0.20.0 preview-cache, schema-repair, tag-search, and
  cancellation regressions

## v0.20.0 — Real-World Stabilization and Code Audit

### Root cause confirmed from the 768-frame QA catalog

- the first import correctly cataloged 768 files as 767 unique images
- v0.19.0 then wrote 767 WebP previews beneath the selected `output` source
  folder
- the optional post-video provider handoff recursively accepted those previews,
  growing the catalog to 1,535 file records and 1,532 image records
- reopening that expanded browser created another generation of previews from
  the mistakenly cataloged previews, allowing the output directory to reach
  2,302 total files

### Fixed

- moved all new browser previews to the user's LoRA Image Curator application-data
  directory, outside catalogs and source folders
- centralized supported-image discovery so metadata import, Florence, and face
  analysis all exclude the exact legacy LoRA Image Curator preview signature
- added schema 10 migration that removes only mistakenly cataloged legacy
  preview records and now-orphaned catalog metadata; it never deletes source or
  preview files from disk
- schema 10 also finalizes run records left `running` by a forced application
  close
- changed ordinary Catalog Browser search to tag/Trigger Keyword content only;
  shared filenames no longer make every extracted frame match
- removed Filename or path from the visible Advanced Search fields while
  retaining the explicit `filename:` parser for existing saved-search
  compatibility
- renamed the misleading per-image progress phase from **Reporting** to
  **Florence analysis**
- added a second, workload-counted confirmation before the optional
  post-extraction provider run starts
- added **Cancel Run** with cooperative stopping between images and a safe
  close-after-cancellation path

### Performance and maintainability

- the browser now creates 96 cards/previews initially and extends the grid in
  bounded batches on demand or near the end of the current scroll range
- completed previews are applied to Tk in small batches instead of allowing a
  large result burst to monopolize the GUI thread
- disposable WebP previews use a faster encoding effort
- report files flush every 25 images rather than forcing a disk flush after
  every row; committed catalog results remain the durable recovery source
- perceptual-hash pairwise comparisons parse each stored hash once instead of
  reparsing both hashes for every pair
- removed unused imports and an unused in-memory face-report accumulation list
- centralized duplicated image-extension and cache-exclusion policy in
  `image_discovery.py`
- documented the code-quality, performance, and reasonable-security review in
  `PHASE1_AUDIT.md`

### Safety

- the supplied QA database was tested through migration in a copy: 1,532 images
  became the correct 767; 1,535 locations became the correct 768; all 117
  completed Florence results and the 767-member image set remained intact
- the original database bytes were not modified during diagnosis
- the legacy `thumbnail_cache` directory is deliberately left on disk; users
  may delete it manually after closing v0.19.0
- FFmpeg and system-folder opening remain argument-list subprocess calls with
  no shell interpolation
- catalog replacement, export, and migration continue to operate only within
  their documented ownership boundaries

### Testing

- added `tests/test_milestone_10_phase1.py` and `tests/test_v0200_gui.py`
- added regressions for first- and later-generation preview exclusion, schema
  repair, tag-only search, external preview storage, and pre-start provider
  cancellation
- verified compilation, the self-contained Milestones 7D–10 Phase 1 chain,
  source-database preservation, SQLite integrity, and foreign keys

## v0.19.0 — Workflow Clarity and Catalog Replacement

### Added

- visible **LoRA target** text inside the **Training Handoff** card
- explicit overwrite confirmation when **New Empty Catalog** or
  **Create from Images** targets an existing LoRA Image Curator catalog
- staged, validated, atomic empty-catalog replacement
- `tests/test_milestone_9b.py`
- `tests/test_v0190_gui.py`

### Changed

- removed the redundant **Exact Copies** readiness row; **Similarity match** and
  **Possible Duplicates** are now the single duplicate-review path in
  **Finalize & Export**
- renamed **Export This Scope…** to **Export Training Data…**
- removed the redundant `Scope:` prefix from the Training Handoff value
- confirmed Create-from-Images replacement retains the user's create workflow
  wording while publishing through the existing private staging database
- the visible UI describes current behavior even where the internal
  architecture deliberately supports future expansion
- schema remains version 9; v0.18.0 catalogs require no migration

### Fixed

- explicitly closed legacy keyword/review/identity reads and both catalog-backup
  connections, removing the v0.18.0 SQLite `ResourceWarning` under
  `python -X dev`

### Safety

- overwrite permission is explicit and is not inferred merely from an existing
  filename
- only a validated LoRA Image Curator catalog can be replaced through this workflow
- source images and prior exports remain outside catalog replacement
- failed or cancelled image imports leave the original catalog intact

### Testing

- confirmed and declined catalog-replacement boundaries
- source-image preservation across populated and empty replacement
- resource-warning cleanup under Python development mode
- Finalize & Export labels, active target, duplicate controls, and named-set
  scope wording
- full non-GUI regression chain through Milestone 9B

## v0.18.0 — Training Text Validation

### Added

- profile-aware **No Training Text** and **Repeated Training Text** readiness
  checks based on the exact sidecar text produced by the active LoRA preset
- exact Catalog Browser result links for the images involved in repeated-text or
  empty-text validation findings
- live empty/repeated sidecar validation in the export dialog when its profile
  changes
- `tests/test_milestone_9a.py`
- `tests/test_v0180_gui.py`

### Changed

- training-text quality is treated as validation, not as a separate warning
  workflow or duplicate caption-search UI
- readiness and export share the canonical training-text builder, including
  profile-specific layer selection and case-insensitive duplicate-tag removal
- repeated text is a review finding with a small capped deduction; it is not an
  automatic export blocker because simple identity anchors can intentionally
  share basic text
- rejected and quarantined records remain excluded from the repeated-text
  validation count
- the repeated-text row opens a generated exact image-ID result rather than a
  misleading contains/does-not-contain term search
- fixed the v0.17.0 GUI smoke test's themed-button state assertion by using
  `ttk.Button.instate(("!disabled",))`
- schema remains version 9; v0.17.0 catalogs require no migration

### Testing

- canonical and profile-specific repeated text, empty-sidecar separation,
  exact-result filtering, and rejected-record exclusion
- live Finalize & Export and export-dialog validation
- Milestones 8A, 8G, and 8H remain green

## v0.17.0 — Video Source Import

### Added

- compact **Video Sources** launcher in **Analyze & Update Catalog**
- focused **Extract Frames from Video** dialog that keeps infrequent controls
  out of the already dense first tab
- automatic FFmpeg resolution from an approved saved path and then PATH
- manual `ffmpeg.exe` selection, harmless `-version` validation, visible
  resolved path/version, and persistent local configuration
- fixed-interval and scene-change sampling
- JPEG and lossless PNG frame output, safe filename prefixes, and a required
  maximum frame count
- Save Frames Only, Add to Current Catalog, and Create New Catalog handoffs
- optional image-set creation and optional start of the currently configured
  caption/face providers after staged import
- scrollable completion report with exact command, source/destination,
  sampling, output/failure counts, and catalog-import summary
- `video_extraction.py`, `video_extraction_dialog.py`,
  `tests/test_milestone_8h.py`, and `tests/test_v0170_gui.py`

### Changed

- source preparation now precedes catalog analysis in the documented
  left-to-right workflow; **Finalize & Export** remains the final handoff
- matching destination names cause a clear refusal instead of overwrite,
  replacement, or silent mixing with a previous extraction
- multi-person action frames are retained as review-needed candidates rather
  than declared safe for a single-subject likeness LoRA
- provider runs now refuse a custom-named active catalog that would otherwise
  diverge from the pipeline's standard `dataset_tools.db`
- schema remains version 9; v0.16.0 catalogs require no migration

### Privacy, licensing, and safety

- video processing is local and offline; the source video is never modified,
  moved, deleted, or uploaded
- process arguments use `shell=False`, so user-selected paths are not evaluated
  as shell commands
- FFmpeg is not bundled, downloaded, updated, or relicensed by LoRA Image Curator
- any future bundled FFmpeg build requires an explicit LGPL/GPL/nonfree review
  and the corresponding license/source-compliance package
- staged catalog import publishes only after the complete import succeeds
- cancellation preserves partial generated frames and reports their count

### Testing

- FFmpeg identity probing, stale-saved-path fallback, argument-safe command
  construction, interval/scene filters, output accounting, and collision refusal
- real FFmpeg interval and scene-change extraction in the development environment
- staged catalog handoff and persistent video-setting round trip
- GUI smoke coverage for placement, manual-path status, sampling controls,
  post-extraction choices, report contents, and clean logging shutdown
- Milestones 8A through 8G and the v0.9 export regression remain green

## v0.16.0 — Finalize & Export

### Added

- renamed **Dataset Readiness** tab to **Finalize & Export**
- **Training Handoff** card beside Readiness Checks for the active All catalog
  images or named image-set scope
- exact eligible and review-excluded counts before export
- **Export This Scope…** action that excludes Reject and Quarantined records
  while retaining them in readiness statistics
- readiness-aware pre-export summary and explicit Continue confirmation for
  unresolved missing-file, keyword, review, resolution, training-text, Blur,
  and possible-duplicate findings
- built-in **SD 1.5 LoRA** and **General / Other LoRA** training-text profiles
- optional collision-safe `README.txt` documenting scope, profile, output
  counts, readiness notes, and the boundary between dataset preparation and
  trainer-specific settings
- `tests/test_milestone_8g.py` and `tests/test_v0160_gui.py`

### Changed

- the active Finalize & Export target preselects its corresponding Flux, SDXL,
  SD 1.5, or General / Other handoff profile
- the existing browser **Export Selected…** workflow remains available for
  arbitrary thumbnail selections and uses the same exporter
- the export dialog now identifies its source scope and reports profile-specific
  empty sidecars before any files are written
- schema remains version 9; v0.15.0 catalogs require no migration

### Privacy and safety

- scope export copies only; it never moves, deletes, resizes, converts, or
  rewrites source images
- Reject and Quarantined records are skipped by the Finalize & Export scope
  action rather than silently included
- unresolved readiness findings warn but do not prevent a deliberate export
- README and manifest names are allocated without overwriting existing files
- no online service, telemetry, or new Python dependency is introduced

### Testing

- eligible-scope filtering and target-to-handoff profile coverage
- collision-safe README planning and complete handoff contents
- GUI smoke coverage for the renamed tab, handoff card, scope callback,
  pre-export context, profiles, and clean logging shutdown
- Milestones 8A through 8F and the v0.9 export regression remain green

## v0.15.0 — Remove Unnecessary Images

### Added

- **Remove Unnecessary…** action in the Thumbnail tab for an explicitly selected
  candidate pool
- scrollable preview report listing every proposed deselection and all concrete
  reasons before the selection changes
- profile-aware low-resolution, Blur, missing/unreadable source, likely
  screenshot/UI, multiple-person/face, and extremely-small-face checks
- conservative direct near-duplicate culling that keeps the strongest available
  version without treating a transitive similarity chain as one redundant image
- transparent counts for checks unavailable because optional quality, Florence,
  face, or similarity analysis has not been run
- `selection_culling.py`, `cull_report_dialog.py`, `tests/test_milestone_8f.py`, and
  `tests/test_v0150_gui.py`

### Changed

- the abandoned auto-selection design is replaced by an explicit culling
  workflow: users select the candidate pool first, preview removals, then decide
  whether to apply them
- duplicate ranking prioritizes manual Keep and confirmed identity decisions,
  then available presence, single-person evidence, identity confidence,
  sharpness, resolution, and face visibility
- the difference-hash implementation uses grayscale bytes rather than Pillow's
  deprecated `Image.getdata()` API
- normal application close and the new GUI smoke test flush and close logging
- schema remains version 9; v0.14.0 catalogs require no migration

### Privacy and safety

- the operation changes only the in-memory browser selection after explicit
  confirmation
- no source image, SQLite row, review decision, tag, caption, image set, saved
  search, or export is modified
- absent optional analysis never becomes a removal reason
- pose, outfit, expression, lighting, likeness, anatomy, and aesthetic quality
  are disclosed as unsupported rather than inferred with false confidence

### Testing

- combined issue reasons, missing-analysis conservatism, best-version ranking,
  and non-transitive redundancy behavior
- GUI smoke coverage for the real report, applied deselection, prior grouped
  similarity behavior, Pillow analysis, and Windows log-handle cleanup
- Milestones 8A through 8E and the v0.9 export regression remain green

## v0.14.0 — Grouped Similarity Review

### Added

- threshold-based perceptual-similarity clusters for the existing duplicate
  search and Dataset Readiness issue-link workflow
- one bordered comparison area per connected cluster, with a group heading and
  explicit instruction to compare only images inside that area
- `duplicate_candidate_clusters()` as a GUI-independent clustering primitive
- `tests/test_milestone_8e.py` and `tests/test_v0140_gui.py`

### Changed

- Possible Duplicates now counts complete in-scope clusters rather than relying
  on each image's single globally nearest neighbor
- positive conjunctive `duplicate:` similarity searches use the grouped layout;
  normal browsing, exact-copy searches, negated queries, and mixed OR searches
  keep the ordinary thumbnail grid
- loading a saved image set adds its members to the existing transient browser
  selection instead of replacing that selection
- user-facing catalog actions are now Create from Images and Add Images
- schema remains version 9; v0.13.0 catalogs require no migration

### Privacy and safety

- similarity grouping is a temporary presentation mode and persists no hidden
  state
- the workflow never chooses Keep/Reject, deletes a file, changes a source
  image, or saves an image set automatically
- source images, provider results, manual metadata, saved set membership, and
  export history remain unchanged until the user invokes an existing explicit
  action

### Testing

- connected/transitive grouping, threshold boundaries, readiness counts, query
  activation boundaries, and additive set selection
- GUI smoke coverage for renamed actions, one visually separated group, and
  restoration of the ordinary grid after leaving duplicate review
- Milestones 8A through 8D and the v0.9 export regression remain green

## v0.13.0 — Catalog Import and Management Cleanup

### Added

- SQLite Catalog controls on the LoRA Image Curator tab for New Empty Catalog,
  Create from Folder, Open Catalog, Import Folder, and confirmed deletion
- metadata-only folder import with a recursive-scan checkbox and supported-image
  filtering
- optional named image-set creation from each imported folder, enabled by default
- explicit Replace/Merge/Cancel decision before importing into an existing catalog
- full import report with image counts, failures, and exact duplicate SHA-256 values
- `catalog_import.py`, `catalog_import_dialog.py`, `tests/test_milestone_8d.py`, and
  `tests/test_v0130_gui.py`

### Changed

- catalog lifecycle controls no longer appear in the Catalog Browser; that tab
  remains focused on thumbnails, selection, review, image sets, and export
- folder import registers content identity, file locations, and image dimensions
  without forcing Florence or face analysis
- exact byte copies remain one SHA-256-identified catalog image while their file
  locations remain traceable
- Dataset Readiness continues to open on All catalog images
- duplicate review moves to Milestone 8E after the new catalog workflow
- schema remains version 9; v0.12.0 catalogs require no migration

### Privacy and safety

- source images are read only and are never moved, renamed, modified, or deleted
- create, merge, and replace run against a private staging database and publish
  only after the complete operation succeeds
- cancellation or failure leaves the original catalog unchanged
- Replace is explicitly confirmed and removes only catalog-owned contents;
  source images and exported datasets remain outside its boundary
- no import choice, progress, or partially completed operation is persisted
  unless the completed catalog and optional image set are deliberately published

### Testing

- create, merge, replace, recursive/non-recursive discovery, duplicate reporting,
  set creation/name collisions, invalid images, cancellation, source preservation,
  SQLite integrity, and foreign-key integrity
- Milestones 8A through 8C and the v0.9 export regression remain green
- GUI smoke test verifies catalog controls belong to the LoRA Image Curator tab and
  readiness defaults to All catalog images

## v0.12.0 — Image Sets and Set-Scoped Readiness

### Added

- schema version 9 `image_sets` and `image_set_members` tables with additive
  migration from existing catalogs
- named, catalog-local image sets created explicitly from the current browser
  selection
- Image Sets manager for adding/removing the current selection, renaming,
  deleting, and restoring a saved set as the browser selection
- `set:` Boolean-search field and Image set option in Advanced Search
- Dataset Readiness scope selector for All catalog images or one named set
- set-constrained readiness issue links back to the Catalog Browser
- `image_sets.py`, `image_set_dialog.py`, `tests/test_milestone_8c.py`, and
  `tests/test_v0120_gui.py`

### Changed

- Blur changes update only the Blur result plus the dependent overall score
- Similarity dragging changes only its descriptive cue; the selected value is
  applied on release and updates only Possible Duplicates
- Dataset Composition and Common Vocabulary are no longer rebuilt for
  interpretation-only threshold changes
- duplicate review moves to Milestone 8D so readiness can evaluate deliberate
  candidate sets first

### Privacy and safety

- sets persist only after explicit create/add/remove/rename/delete actions
- current browser selection and the active readiness set remain session-only
- deleting a set removes memberships only; catalog images, source files,
  metadata, analyses, and exports remain untouched
- existing catalog deletion also removes its owned sets, as expected

### Testing

- schema 8 -> 9 additive migration and image preservation
- set CRUD, case-insensitive name uniqueness, membership integrity, set search,
  scoped readiness inputs, and non-destructive deletion
- Milestone 8B, Milestone 8A, and v0.9 export regressions remain green
- GUI smoke test uses valid image fixtures and checks targeted widget updates

## v0.11.1 — Similarity Control Patch

### Changed

- narrowed perceptual Possible Duplicates matching to integer slider stops from
  96 through 100, matching the useful near-identical range found during Windows
  testing
- replaced the visible percentage readout with descriptive labels from looser
  matching through Identical, while keeping exact SHA-256 copies as the factual
  duplicate signal
- made the Dataset Readiness GUI smoke-test shutdown cancel its repeating timer
  before Tk teardown, so Windows can release the temporary SQLite catalog before
  `TemporaryDirectory` cleanup

### Testing

- Milestone 8B regression tests pass
- Python compilation checks pass for the patched modules
- GUI smoke test remains included for Windows or Linux systems with a display

## v0.11.0 — Local Image-Quality Analysis

### Added

- manually started local quality analysis with progress, cooperative
  cancellation, cached-result reuse, and an explicit reanalyze option
- Pillow-based variance-of-Laplacian sharpness scores presented as **Blur** with
  an explanatory tooltip and adjustable threshold
- 64-bit perceptual difference hashes and a 96–100 stepped Possible Duplicates
  similarity control
- exact-copy reporting based on the catalog's existing SHA-256 identity and
  multiple known file locations
- schema version 8 `image_quality_results` storage, separate from user review
  state and replaceable when the algorithm changes
- quality fields in the common browser projection, details pane, Boolean search,
  and Dataset Readiness checks
- Flux Character LoRA, SDXL Character LoRA, SD 1.5 Character LoRA, and General /
  Other LoRA readiness targets
- explicit **New Catalog…** and **Delete Catalog…** browser actions; deletion
  removes the catalog and its stored quality data but not source images
- `quality_analysis.py`, `catalog_lifecycle.py`, `tests/test_milestone_8b.py`, and
  `tests/test_v0110_gui.py`

### Changed

- the Catalog Browser no longer includes its own training-text preview because
  that workflow is owned by the separate Dataset Reviewer; export retains its
  authoritative live preview
- Dataset Readiness now combines catalog preparation with locally measured
  quality coverage and Blur deductions
- perceptual duplicate matches and exact-copy counts remain advisory and never
  make automatic Keep/Reject decisions
- quality thresholds and the explicitly selected readiness target are remembered
  as preferences; run progress and the reanalyze checkbox remain ephemeral
- `BUGS.md` was reviewed; the hidden/no-console launcher remains open

### Privacy and safety

- image decoding, sharpness scoring, and perceptual hashing run locally
- no images, hashes, catalog contents, or activity are uploaded
- source images are read only and never moved, renamed, modified, or deleted
- catalog deletion requires explicit confirmation and names the exact database
  being removed
- quality results are stored only in the selected SQLite catalog and disappear
  when that catalog is deleted

### Testing

- schema 7 -> 8 additive migration and SQLite integrity/foreign-key checks
- synthetic sharp, soft, resized, and recompressed image fixtures
- cache reuse, reanalysis, cooperative cancellation, adjustable similarity
  matching, exact-copy projection, profile-specific readiness, and source-byte
  preservation
- prior Milestone 8A search/readiness and v0.9 export regressions remain green
- Python compilation checks for every application and test module
- GUI smoke test included for Windows or Linux systems with a display/Xvfb

## v0.10.0 — Advanced Search and Dataset Readiness

### Added

- Boolean query evaluation with AND, OR, NOT, parentheses, implicit AND, and
  leading-minus exclusion
- field operators for tags, Trigger Keywords, review state, identity state, file
  availability, captions, filenames/paths, and resolution
- Advanced Search dialog with Include/Exclude rows and All/Any matching
- optional automatic search history with enable/disable, a 1–200 entry limit,
  and a separate Clear History action
- explicitly named, catalog-local saved searches with apply and delete actions
- schema version 7 `saved_searches` table
- Dataset Readiness tab for the default Flux Character LoRA preparation profile
- visible weighted readiness deductions, advisory checks, review/file/resolution
  statistics, and common vocabulary summaries
- clickable readiness checks that apply the corresponding ordinary browser query
- hover explanations for compact readiness labels
- `advanced_search.py`, `dataset_readiness.py`, `search_dialogs.py`, and
  `readiness_frame.py`
- `tests/test_milestone_8a.py` and `tests/test_v0100_gui.py`

### Changed

- user-facing **Set Keyword** terminology is now **Trigger Keyword**; compatible
  internal database names remain unchanged
- browser search history is distinct from current search text and from named
  saved views
- settings preservation now includes the history preference, maximum, and list
- `DESIGN_PHILOSOPHY.md` now records the rule that user activity is ephemeral
  unless persistence is necessary or explicitly chosen
- `ROADMAP.md` now separates Milestone 8B image-quality analysis from Milestone
  8C duplicate review
- `BUGS.md` was reviewed; the hidden/no-console launcher issue remains open and
  the visible batch launchers remain the documented workaround

### Readiness scoring

- rejected and quarantined images remain in composition statistics but are not
  counted as intended training images
- deductions are proportional to eligible-image counts and individually capped
- missing files and missing Trigger Keywords are blocking checks
- unreviewed, low-resolution, missing-training-text, and unresolved-identity
  checks remain visible and explainable
- Multiple Faces is advisory and does not reduce the score
- the score is documented as a preparation checklist, not a prediction of LoRA
  quality

### Privacy and safety

- partially typed/current search text is never restored as general window state
- automatic history can be disabled without deleting deliberately saved views
- named searches are stored only after an explicit Save Search action
- no telemetry, online service, image upload, new analysis model, or destructive
  file operation was added

### Testing

- dependency-free tests for Boolean precedence, field aliases, query building,
  readiness counts/scoring, schema 7 migration, saved-search replacement/deletion,
  and history settings
- prior v0.9.0 export regression remains green against schema version 7
- Python compilation checks for every application and test module
- GUI smoke test included for Windows or Linux systems with a display/Xvfb

## v0.9.0 — Dataset Export and Caption Builder

### Added

- **Export Selected…** workflow in the Catalog Browser
- non-destructive copying of selected source images into a new dataset folder
- same-name UTF-8 `.txt` sidecars derived from catalog layers
- built-in **Flux LoRA**, **SDXL LoRA**, and **Caption Dataset** profiles
- locally remembered **Custom** profile with explicit trigger/manual/AI/caption layers
- live training-text preview in both the details pane and export dialog
- reviewable pre-export summary with exact counts, collision-safe filenames, and
  sample output paths
- safe collision policies: rename with `_2`, `_3`, and so on, or skip existing
- optional UTF-8-with-BOM `manifest.csv` containing source, output, curation,
  identity, review, and training-text fields
- background export progress with cooperative cancellation after the current file
- item-level failure isolation and `export_errors.csv` reporting
- **Open Folder** action after completion
- local export preferences for destination, profile, outputs, collision policy,
  and Custom profile layers
- schema version 6 export audit tables: `export_runs` and `export_run_items`
- `dataset_export.py`, `export_dialog.py`, `tests/test_milestone_7d.py`, and
  `tests/test_v090_gui.py`

### Changed

- final sidecar text is now generated on demand from the preserved catalog layers
  rather than stored as a mutable caption
- the details pane includes a profile selector and exact/sample export preview
- application-level settings saves now preserve browser export preferences
- the v0.8.2 GUI smoke test now closes SQLite explicitly before Windows removes
  its temporary catalog
- Milestone 7D is complete; advanced search and saved dataset views are next

### Safety

- source images are copied only; they are never moved, renamed, deleted, or
  overwritten
- destination files are staged under temporary names before promotion
- existing destination files are never overwritten by any collision policy
- export history is not mixed into Ctrl+Z/Ctrl+Y catalog-edit history
- provider captions, object labels, face results, tags, exclusions, and review
  decisions are read but never rewritten by export
- partial and cancelled exports remain auditable through manifests and catalog
  history

### Testing

- schema 5 -> 6 migration and SQLite integrity/foreign-key checks
- Flux, SDXL, Caption, and Custom profile assembly
- duplicate-free trigger/manual/AI ordering
- existing and intra-selection filename collisions
- image copies, sidecars, manifests, missing files, item failures, and error reports
- cooperative cancellation and export-history counts
- GUI smoke coverage for Export Selected, live preview, planning, and worker-thread
  progress
- all prior Milestone 6B/7A/7B/7C regression tests remain green

## v0.8.2 — Tag and Caption Curation Foundation

- materialized Florence object labels as provider-owned AI tag suggestions
- added blue active-AI, gray excluded-AI, and orange manual tag chips
- added idempotent multi-tag entry and common-only batch tag display
- added reversible AI exclusions without rewriting raw provider output
- added tag-aware search operators
- derived training text in Set keyword, manual tag, active AI tag order
- migrated catalog schema 4 -> 5
- added Milestone 7C regression and GUI smoke tests

## v0.8.1 — Unified Selection Editing

- replaced the separate Batch Edit dialog with one details-pane editor for both
  single-image and multi-image selections
- added aggregate multi-selection summaries in the preview area
- shared values display normally; mixed values display as Multiple values
- disposition and identity actions apply immediately to the complete selection
- retained an explicit Save Keyword action for free-form text
- added confirmation before edits affecting 100 or more selected images
- added a durable 20-step undo/redo history with Ctrl+Z, Ctrl+Y, and
  Ctrl+Shift+Z
- added safe redo-branch invalidation after new edits
- added Explorer-style drag-box selection on blank grid space
- removed the visible Batch Edit and Undo Last Batch buttons
- migrated catalog schema 3 -> 4 by adding redo-branch metadata
- added regression coverage for migration, history retention, undo, redo,
  branching, conflicts, provider preservation, multi-selection UI, and drag
  selection

## v0.8.0 — Milestone 7B Batch Review and Undo

### Added

- compact **Batch Edit...** button for selections of two or more images
- shared-value summaries that display the common value or **Multiple values**
- explicit per-field opt-in controls so unchecked metadata is untouched
- batch Set/Clear manual Set keyword
- batch disposition changes: Unreviewed, Keep, Needs follow-up, and Reject
- batch Confirm/Reject/Reset for strongest eligible identity suggestions
- preview page with exact change counts and affected filenames
- durable **Undo Last Batch** history stored locally in SQLite
- conflict detection that refuses undo when newer edits touched the same metadata
- schema migration 2 -> 3 for batch operation snapshots
- `tests/test_milestone_7b.py` regression coverage for atomic writes, mixed identity
  eligibility, exact undo, no-op suppression, conflict safety, and integrity

### Changed

- the browser's multi-selection now controls real catalog operations
- batch disposition changes preserve any existing review notes
- identity rows without suggestions are explicitly counted as skipped
- orange identifies user-owned decisions; blue identifies unreviewed AI suggestions
- Milestone 7C tag and caption curation is now the next roadmap item

### Safety

- the entire batch commits in one SQLite transaction or not at all
- the first edit in a session still creates a SQLite-native catalog backup
- source images and provider analysis rows are never modified
- undo restores only user-owned metadata captured by the batch operation
- undo verifies the current state before restoring and will not overwrite newer work


## v0.7.0 — Milestone 7A Manual Review

### Added

- compact Manual Review controls in the image details pane
- image decisions: Unreviewed, Keep, Needs follow-up, and Reject
- confirm, reject, and reset actions for the strongest AI identity suggestion
- manual Set keyword entry, replacement, and clearing
- automatic transactionally consistent SQLite backup before the first catalog
  edit of each application session
- synchronized review state between detailed identity matches and searchable
  provider-generated identity tags
- `tests/test_milestone_7a.py` regression coverage for backup, review state, keyword
  replacement, identity decisions, persistence, foreign keys, and integrity

### Changed

- the browser is no longer globally read-only; only explicit review buttons
  write catalog metadata
- manual keywords remain separate from AI-generated tags and analyses
- rejected identity suggestions remain inspectable and resettable while being
  excluded from normal card subtitles and active identity filtering
- documentation now identifies Milestone 7B batch editing as the next step

### Fixed

- empty search/filter results now use the full grid width instead of inheriting
  stale thumbnail-column geometry and appearing clipped

### Safety

- source image files are never modified by review actions
- every write uses one SQLite transaction
- the first edit creates a SQLite-native backup that includes committed WAL data
- Florence and face-analysis result rows are not overwritten by manual review

## 0.6.2 — Milestone 6B

- Fixed search/filter result gaps by fully rebuilding and reflowing the visible
  thumbnail grid from the first cell.
- Added compact Sort and Filter dropdowns without expanding the details pane.
- Added filename, catalog date, identity-confidence, face-count, dimensions,
  and file-size sorting.
- Added face, identity, manual metadata, OCR, missing-file, and review filters.
- Persisted the selected sort, filter, and last opened catalog.
- Added `catalog_edits.py`, a transactional and currently non-UI editing
  foundation for manual keywords, review state, identity decisions, and backups.
- Added a dependency-free Milestone 6B regression test.
- Moved deferred browser polish into `WISHLIST.md`.

## v0.6.1

### Changed

- replaced the resizable/collapsible catalog split pane with a stable fixed-width details pane
- preserved the original thumbnail-to-details proportions while giving thumbnails all remaining window space
- simplified thumbnail selection to border-and-background highlighting without a checkmark badge
- added `Run LoRA Image Curator.bat` using the diagnostic launcher's reliable environment and error reporting

### Fixed

- removed graphical artifacts caused by resizing or collapsing the details pane
- restored a regular batch-file launcher to the update package

## v0.6

### Added

- Explorer-style Catalog Browser tab
- one thumbnail card per unique catalog image
- disposable WebP thumbnail cache beside the SQLite catalog
- background thumbnail generation to keep the GUI responsive
- search across filenames, paths, captions, OCR, detected objects, tags,
  identities, recommendations, review notes, and review status
- Windows-style multi-selection:
  - click selects one image
  - Ctrl-click toggles an image
  - Shift-click selects a visible range
  - Ctrl+A selects all visible results
  - Escape clears the selection
- selected-card border, tint, and checkmark badge
- compact manual-metadata badge using a distinct accent color
- missing-file badge and preview placeholder
- resizable and collapsible details pane
- larger selected-image preview
- detailed read-only metadata inspector
- double-click and Open Image actions
- `ROADMAP.md`
- `WISHLIST.md`
- `DESIGN_PHILOSOPHY.md`

### Changed

- main window now separates analysis and browsing into notebook tabs
- window starts wider so images receive most of the browser space
- the face-provider field label is now **Set keyword**
- the browser automatically follows the analysis output catalog
- a completed provider run refreshes the browser
- bug tracking now distinguishes defects from roadmap and wishlist items
- ONNX Runtime CUDA unavailability is recorded as a confirmed open bug
- documentation now describes LoRA Image Curator as a permanent AI dataset catalog
  rather than a single-project script

### Safety

- the v0.6 browser is read-only
- thumbnails are cache files and can be deleted safely
- source images are not renamed, moved, altered, or deleted
- opening an older supported catalog may apply the normal additive schema
  migration before browsing
- the Open Catalog workflow validates LoRA Image Curator metadata before allowing a
  migration, preventing accidental modification of an unrelated SQLite file

### Known limitations

- manual editing and batch actions are not implemented yet
- very large result sets currently create one Tk card per matching image;
  virtualization may be added if real catalogs demonstrate a need
- the details-pane width is not yet saved between sessions
- thumbnail size is fixed for this first browser release
- CUDAExecutionProvider remains unavailable on the tested Windows environment
- the hidden launcher remains unreliable

## v0.5

### Added

- modular `analysis_pipeline.py` orchestration boundary
- provider-neutral face-analysis interface
- optional InsightFace provider using detection and recognition modules only
- dependency/setup diagnostics that do not require manual venv activation
- explicit model-download and non-commercial-license prompt
- identity reference folder and named identity profile
- cosine-similarity matching with configurable thresholds
- separate face CSV report
- SQLite schema migration 1 → 2
- face model, run, image result, detection, identity profile, and match tables
- automatic general identity-tag suggestions
- Help and Check Setup buttons in the GUI
- local-processing and file-safety explanations in the GUI

### Changed

- main GUI calls a provider pipeline rather than Florence directly
- summary panel now includes faces detected and identity suggestions
- diagnostic launcher updated to v0.5
- model fingerprints prevent incompatible stored face-result reuse

### Fixed

- removed duplicate status-log insertion present in the prior GUI source
- face report filenames include microseconds to avoid same-second collisions
