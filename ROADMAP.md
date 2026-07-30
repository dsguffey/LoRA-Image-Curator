# LoRA Image Curator Roadmap

The roadmap contains intended work, not every interesting possibility. Exact
version numbers and ordering may change after hands-on testing.

## Current foundation

Completed foundations include:

- local Florence caption and triage analysis
- a persistent, versioned SQLite catalog
- analysis reuse for unchanged images
- modular provider orchestration
- local face detection and identity suggestions
- an Explorer-style visual catalog browser
- cached thumbnails, search, details, sorting, filtering, and multi-selection
- single-image and multi-image manual review
- 20-step transactional undo/redo with branch and conflict protection
- Explorer-style drag-box selection
- provider-owned tags, user-owned manual tags, and reversible AI exclusions
- profile-based training-text assembly
- non-destructive image, sidecar, and manifest export
- advanced Boolean search, optional history, and named catalog views
- Dataset Readiness statistics and transparent preparation scoring
- manually started, cached local sharpness and perceptual-hash analysis
- selectable Flux, SDXL, SD 1.5, and general LoRA readiness targets
- explicit catalog creation and permanent catalog deletion
- deliberately saved named image sets with browser selection and readiness scope
- metadata-only recursive/non-recursive folder import with staged catalog publication
- grouped possible-duplicate review inside the existing thumbnail workflow
- local FFmpeg video-frame extraction with staged catalog/image-set handoff
- profile-aware training-text validation and Finalize & Export handoff
- defensive shared image discovery and schema-10 legacy preview repair
- application-data preview storage with bounded incremental browser loading
- cooperative provider cancellation and explicit post-video run confirmation
- unified browser filtering by saved image set, catalog state, and all readiness
  findings
- result-wide multi-keyword selection and progressive exact image-set updates
- optional local body/pose analysis with import and browser filtering
- reversible quarantine and operating-system Recycle Bin actions
- telemetry-off privacy controls and third-party responsibility disclosures
- composable face/body/general browser filters with a conspicuous active state
- responsive body setup diagnostics and Analysis-tab body workflow controls
- provider-by-provider Run controls with persisted checked/total catalog coverage
- reversible session filter history and first/last/±10 large-catalog navigation
- up-front fixed-interval video estimates and explicit extraction/catalog
  collision choices
- scrollable provider workflow with folder counts, cooperative pause/resume,
  honest remaining-work ETA, and explicit detected-device status
- page-size-relative destructive safeguards, complete catalog-record removal,
  and a dedicated missing-file filter
- video-origin manifests and browser-visible source timestamps

## Current: Milestone 11A — Git-Ready Public Candidate

v0.27.17 is the current Git-ready candidate after the first 14,000-image cleanup,
continued functional testing through 17,000 images, the workflow-clarity update,
deterministic Tk cleanup prompted by the first v0.27.14 live-Windows gate, and
the repository-readiness documentation pass, and isolation of the long
historical Windows Tk replay. It can be published as an honest pre-1.0
active-development project after the v0.27.17 live-Windows golden gate
and repository screening pass.

- keep public version, launcher, README, bugs, roadmap, test, and release
  metadata synchronized
- present the current capabilities, local-first boundaries, third-party
  dependencies, and known limitations clearly
- distinguish provider/tool/model limitations from LoRA Image Curator defects
- verify no private datasets, catalogs, logs, models, paths, credentials, caches,
  or generated exports are included
- retain a concise architecture/module map instead of combining independent
  modules only to reduce the file count
- publish the deterministic v0.27.17 source ZIP as the pre-1.0 reference snapshot

## Completed within Milestone 11: v0.27.17

- isolated the v0.27.10-and-earlier Tk replay in a strict child process while
  preserving stderr rejection, and made source/runtime paths explicit

## Completed within Milestone 11: v0.27.16

- added a public Git-ready checklist for repository screening, publication, and
  post-publish sanity checks
- added GitHub issue templates that steer users away from posting private
  datasets, catalogs, logs, models, credentials, or local paths
- added Git attributes so text files normalize predictably while binary/model
  artifacts remain binary if they are ever present locally
- advanced the maintained regression, release inventory, and Windows GUI chains
  to keep the repository-readiness files in the signed source boundary

## Next: Milestone 11B — Large-Catalog Stress Testing

Current workstation baseline: roughly five seconds for a cold first launch and
three seconds for the first Browser load at approximately 14,000 images. A warm
second load is faster and the UI remains responsive. These observations are
useful baselines, not final performance guarantees.

- measure discovery, cataloging, thumbnail generation, paging, filtering,
  sorting, provider analysis, quarantine/delete, export, and memory use at
  several thousand and tens-of-thousands-of-image scales
- record peak memory, database size, first-page latency, page-change latency,
  filter latency, cancellation latency, and recovery after interrupted work
- verify provider Pause/Resume, cancellation, stored-result reuse, ETA, and
  device reporting under long sustained runs
- measure false positives and false negatives in the current face/body filters,
  especially cases where non-person images are incorrectly treated as face or
  body evidence
- establish meaningful defaults from measured workflows without changing safe
  behavior speculatively
- use evidence to decide whether analysis batching, database indexes, thumbnail
  scheduling, virtualized results, or a separate staging catalog are required
- complete the deferred high-risk QA jobs: quarantine/restore, close while a
  provider is busy, and recovery after interruption
- test changing filters, sorting, page, and navigation while thumbnail work is
  still arriving
- validate Pause/Resume/Cancel with each provider rather than Florence alone

## Completed within Milestone 11: v0.27.15

- removed Tk Browser ownership from thumbnail and folder-count worker closures
- released decoded Tk images before interpreter destruction
- detached Browser and Readiness callbacks during shutdown
- added a live synthetic-image finalizer checkpoint to the golden GUI chain

## Completed within Milestone 11: v0.27.14

- organized Settings by function while naming each third-party provider/tool on
  its page
- added independent inclusive-by-default subfolder scopes for catalog import,
  Florence input, face input, and face-reference folders
- clarified single shared-progress ownership with a Current work heading and
  temporary green provider markers
- fixed Image Details wheel routing over read-only rich-text surfaces
- documented the Florence 1,024-token provider warning without misclassifying it
  as whole-job batching or truncating provider output speculatively
- synchronized stale Windows launcher banners and added release-chain coverage

## Completed within Milestone 11: v0.27.13

- removed the complete Tk application from the startup provider-device
  worker's closure; it now owns only plain values and a thread-safe queue
- strengthened the worker regression so no future `self` or Tk-variable access
  can return to the device-inspection thread
- retained the strict unraisable-exception and GUI-stderr gates for live
  Windows verification

## Completed within Milestone 11: v0.27.12

- separated scheduled redraw execution from immediate image rendering so a
  synchronous viewer action cannot lose ownership of a pending Tcl timer
- made viewer shutdown idempotent and blocked new redraw scheduling once
  destruction begins
- made the parent golden runner reject GUI stderr diagnostics, covering Tcl
  background callback errors that do not reach Python exception hooks
- added direct headless and live-GUI reproductions for both failure paths

## Completed within Milestone 11: v0.27.11

- made enlarged-view redraw ownership explicit and cancelled the retained Tcl
  callback before the viewer widget command is destroyed
- made the Windows GUI gate fail on unraisable cleanup errors and cleaned up
  destroyed historical-dialog cycles on the Tk main thread
- replaced complete installed-folder copying with a synthetic overlay fixture
  that proves overwrite and user-data preservation boundaries directly
- made previously silent clean-extraction and overlay stages announce progress

## Completed within Milestone 11: v0.27.10

- aligned compilation, source/security auditing, and release packaging on the
  signed project inventory
- excluded arbitrary unmanifested local archives from current-code rules and
  release ZIP collection without relying on a particular archive-folder name
- retained strict auditing of every shipped file and added a direct regression
  for the archived SQLite pattern reported on Windows
- added dedicated v0.27.10 regression and cumulative Windows GUI gates

## Completed within Milestone 11: v0.27.9

- made every maintained direct SQLite test connection explicitly close before
  its temporary directory is removed on Windows
- added a source-audit rule preventing the transaction-only
  `with sqlite3.connect(...)` pattern from returning
- made failed catalog initialization release its database handle immediately
- added dedicated v0.27.9 regression and cumulative Windows GUI gates

## Completed within Milestone 11: v0.27.8

- excluded the installed `output` runtime folder from source/security audit and
  release collection without moving or deleting the user's catalogs or backups
- retained strict rejection of database artifacts placed in actual release
  source
- added regressions for both audit scope and release inventory

## Completed within Milestone 11: v0.27.7

- restricted golden source compilation to Python files named by the signed
  release inventory rather than recursively scanning the installation folder
- added a regression proving malformed Python under an adjacent virtual
  environment is outside the project release gate
- retained every v0.27.6 regression, packaging, and cumulative GUI checkpoint

## Completed within Milestone 11: v0.27.6

- added one synthetic-data golden-build command covering the complete maintained
  non-GUI history, current cumulative Windows GUI chain, source/documentation
  audit, deterministic release build, clean extraction, and overwrite overlay
- added dedicated v0.27.5/v0.27.6 regression and GUI entry points
- stopped delete-associated cleanup from creating a database backup for one
  complete record while retaining backups for multi-image cleanup and explicit
  catalog-only removal
- synchronized the current version and release-verification documentation

## Completed within Milestone 11: v0.27.5

- added a direct trash-can action to the enlarged image reviewer
- routed single-image deletion through the existing Browser file-action policy
- kept the viewer from retaining deleted records by closing it after handoff

## Completed within Milestone 11: v0.27.4

- kept the Settings Save/Cancel footer visible independently of Notebook
  requested height
- renamed Browser **Quality** to **Filter Settings** and centralized shared
  target/Blur/duplicate interpretation under Settings
- retained Browser readiness checkboxes as the on/off controls and clarified
  that Possible Duplicates—not its threshold—is the visibility switch
- added a bottom-right thumbnail maximize icon and compact floating controls in
  the enlarged image reviewer
- returned releases to flat overwrite-in-place archives; obsolete-file
  preflight reports exact files to move without touching user data

## Completed within Milestone 11: v0.27.3

- fixed persistence and delete-time use of the optional complete-record cleanup
  setting
- moved quarantine, restore, Recycle Bin, and record removal behind responsive
  modal worker progress with cooperative cancellation
- batched large SQL ID sets and avoided resolving selected file locations twice
- removed the Browser's eager all-pairs nearest-duplicate calculation; bounded
  duplicate groups are indexed and single-image explanations are on demand
- added Browser-stage timing logs for query/projection and filter/page work
- added scoped mouse-wheel routing to canvas/elevator-backed Analysis, Browser,
  details, and Finalize surfaces without taking wheel ownership from native
  Text, Tree, list, slider, Spinbox, or Combobox controls
- made body/pose percentage sliders whole-number controls with a live description
  for every integer value; duplicate similarity is bounded to five described
  96–100% choices and Blur is a whole-number sharpness score
- clarified person/body, pose completeness, face, and not-analyzed states; added
  a conservative **Show Likely Non-Person** combined filter that never changes
  files, selection, or catalog data
- added the read-only **Image Quality** popup for dense Blur, duplicate,
  face/body, threshold, and provider evidence while retaining video source and
  timestamp in ordinary Image Details
- added a pre-import smoke-test diagnostic for stale top-level Python files
  (v0.27.4 retains the diagnostic while returning to flat archives)
- added dependency-light v0.27.3 coverage for settings, progress/cancellation,
  indexed duplicate accuracy, subject-evidence semantics, and UI contracts

## Completed within Milestone 11: v0.27.2

- made the Analysis tab scrollable and added recursive input-folder image counts
- added cooperative provider Pause/Resume and explicit Run/Restart wording
- corrected Florence ETA to measure remaining inference rather than reused work
- exposed detected Florence/InsightFace/MediaPipe execution devices
- added provider folder diagnostics and retained text cataloging progress
- added Browser-menu quarantine, restore, Recycle Bin, and complete catalog
  record removal with page-size-relative confirmation
- added a conservative setting for optional record removal after file deletion,
  fresh pre-removal catalog backups, and a No image file found filter
- stored video-frame source/timestamp metadata in additive schema 12 and showed
  it in Browser details

## Completed: v0.27.1 — Provider coverage and large-catalog workflow polish

- added independent Florence, face, and body Run controls to the Analysis tab
  and Tools menu while retaining the complete configured Run All path
- added active-catalog checked/total, success, error, and remaining coverage for
  each provider, including separate successful Florence triage coverage
- made filter Apply and immediate Clear complete session-only Undo/Redo actions
- physically grouped Filters beside Sort
- added First, −10, Prev, Next, +10, and Last browser navigation
- moved remember-folder and stored-result reuse choices into Settings
- saved non-empty typed input/output folder choices on focus loss
- read video duration for a complete fixed-interval frame estimate
- added Overwrite/Skip Existing/Cancel extraction collisions without guessing
  frame numbering; skip safely merges missing deterministic names
- added Replace/Merge/Cancel for an existing catalog target
- preserved schema 11 and existing provider result formats

## Completed: v0.27.0 — Browser workflow and settings reorganization

- removed the space-consuming curation pane and moved its preview-first action
  to a focused **Remove Unnecessary Images** dialog under Selection
- reorganized the single Filters window into Scope, Face, Body / Pose, Quality,
  and Readiness sections
- made face, body/pose, and general catalog-state filters independently
  composable while keeping one filter implementation
- placed the Filters button beside Sort, removed the redundant label and
  toolbar summary, and uses a bold whole-button **Filters On** state
- added an organized browser-only Filters menu whose entries focus the same
  central dialog rather than duplicating filter logic
- placed Body / Pose Analysis on Analyze & Update Catalog, where it occurs in
  the workflow, while preserving the Tools shortcut
- separated Paths & File Actions, Analysis Settings, and Privacy & Legal in the
  Settings menu
- moved Blur threshold ownership to Analysis Settings
- made body-provider/model compatibility checks render feedback immediately
  and run native model initialization away from Tk's event thread

## Milestone 12 — Pre-Import Screening

The target scenario is frame extraction every 0.5 seconds from a full movie,
which may create many thousands of candidates before a useful catalog exists.

- design a pre-import screening workspace for blur, unreadable files, no face,
  no body/person, full-body candidates, repeated title cards, and near-duplicates
- reuse the improved person/body cleanup rules from the browser so any future
  "remove images before import" flow can discard obvious non-person candidates
  before they inflate the working catalog
- allow preview, quarantine, or Recycle Bin removal before catalog insertion;
  never make permanent deletion the fallback
- keep thresholds user-adjustable, with recommended useful ranges and clear
  uncertainty/evidence wording
- retain reports for scanned, accepted, rejected, skipped, failed, and recovered
  files so large automated runs remain auditable
- add catalog-to-catalog merge with SHA-256 deduplication, image-set provenance,
  and a preview of affected records
- add a safe way to remove one prior catalog/image-set contribution from a
  merged catalog without guessing from filenames

## Milestone 13 — Browser UX and Workflow Review

- review the complete extract → import → analyze → filter/curate → finalize
  sequence after stress-test findings are available
- verify tab and menu ownership, wording, discoverability, visual hierarchy,
  active/toggle states, keyboard paths, and narrow-window behavior
- review right-click selected-image actions after the menu-and-shortcut workflow
  has been tested
- review every toggle-like command so the complete control—not a tiny dot—shows
  its active state consistently

## Completed within Milestone 11: v0.27.3 — Image Magnification

- added a built-in large-image review window from double-click and Image Details
  with mouse-wheel zoom, click-drag pan, arrow-key previous/next, Escape close,
  Fit, compact source-video/timestamp context, and external-viewer handoff

## Milestone 15 — Legal and Disclaimer Review

- review the MIT warranty boundary, third-party provider/model/app disclaimer,
  privacy disclosures, telemetry consent wording, model-license presentation,
  user-generated dataset responsibility, and recoverable file-action warnings
- keep Settings, Help, About, SECURITY, THIRD_PARTY_NOTICE, and installer wording
  consistent without implying that a compatibility check certifies safety,
  legality, accuracy, or fitness
- obtain qualified legal review if the project is distributed commercially or
  its provider/package installation policy expands; project documentation is
  not legal advice

## Milestone 16 — File-Structure Cleanup

- reduce the 100-plus-file source sprawl without destabilizing working features
- treat the cleanup as a dedicated refactor with its own complete regression,
  documentation audit, packaging audit, and Windows smoke test

## Milestone 17 — Real Dataset and Training Trial

- prepare and train a real LoRA through the complete application workflow
- record defects, missing capabilities, awkward steps, and inefficient handoffs
- implement and retest the workflow corrections revealed by the actual training

## Milestone 18 — v1.0 Release Candidate

- complete final regression testing, Windows QA, documentation, and release
  materials
- finish executable/installer and distribution polish
- verify a clean installation and real release-candidate workflow before v1.0

## Completed: v0.26.0 — Body-aware curation and safe file actions

- MediaPipe Pose Landmarker is the first vetted, local body-analysis provider
- schema 11 stores provider/model provenance and normalized pose, visible-face,
  completeness, classification, and landmark evidence
- import may optionally skip no-body and/or no-visible-face candidates
- browser filters and sorting expose full-body and other body evidence
- detection, visibility, and full-body thresholds are user-adjustable, with the
  completeness range running from a permissive 60% edge to 100%
- quarantine moves all present physical locations represented by selected
  catalog images and can restore them without overwriting
- Delete uses native Trash/Recycle Bin behavior and has no permanent fallback
- privacy/provider telemetry permission is disabled by default; current local
  MediaPipe analysis uses no telemetry collector
- vetted providers only; arbitrary executable provider packages remain blocked
- Settings owns quarantine/body/privacy controls; the existing first-tab
  InsightFace model-pack browser remains next to its provider controls
- third-party limitations and responsibility are visible in Settings, Help,
  About, and the release documentation
- source-tree restructuring remains a separate future refactor

## Completed: v0.25.2 — Unified browser pruning workflow

- Catalog Browser is the primary workspace for progressively pruning image sets
- one filter dialog composes image-set scope, catalog state, and eleven
  readiness findings with Any/All matching
- readiness membership is shared with Finalize & Export rather than
  reimplemented in the browser
- Select/Deselect by Keyword accepts several comma-separated terms and covers
  all result pages
- Update Image Set replaces membership with the exact browser selection
- global-result selection shortcuts are primary; Current Page actions remain
  explicit
- Finalize & Export remains the quality-analysis, final summary, and export gate
- schema remains version 10

## Completed: Milestone 7A — Manual review

- Keep, Needs follow-up, Reject, and Unreviewed image decisions
- manual Trigger Keyword entry, replacement, and clearing
- identity suggestion confirmation, rejection, and reset
- one transactionally consistent SQLite backup before the session's first edit

## Completed: Milestone 7B — Unified selection editing and history

- the compact details pane edits one image or the complete current selection
- shared values display directly; mixed values display as Multiple values
- explicit Save Trigger Keyword boundary for free-form text
- confirmation before edits affecting 100 or more selected images
- durable 20-step Ctrl+Z/Ctrl+Y history
- safe redo branching and conflict protection
- aggregate selection summary instead of a misleading single-image preview
- drag-box selection on blank thumbnail-grid space

## Completed: Milestone 7C — Tag and caption curation

- AI tags stored separately from raw provider output
- manual tags and AI exclusions stored in the user layer
- blue active-AI, gray excluded-AI, and orange manual tag chips
- idempotent batch Add Tags behavior without duplicates
- common-only tag display for multi-selection
- derived training text ordered as Trigger Keyword, manual tags, then active AI tags
- tag-aware search operators

## Completed: Milestone 7D — Dataset export and caption builder

- Export Selected workflow
- non-destructive image copying
- same-name TXT sidecars
- Flux LoRA, SDXL LoRA, Caption Dataset, and Custom profiles
- live training-text preview
- exact pre-export filename/count preview
- collision-safe rename or skip behavior
- optional manifest.csv
- background progress and cooperative cancellation
- item-level error isolation and export_errors.csv
- schema version 6 export audit history separate from catalog edit history
- saved local export preferences

## Completed: Milestone 8A — Advanced search and Dataset Readiness

- one search language shared by typed queries and the Advanced Search builder
- AND, OR, NOT, parentheses, and implicit-AND behavior
- field operators for tags, Trigger Keywords, review, identity, files, captions,
  paths, and resolution
- automatic search history with enable/disable, maximum, and clear controls
- current partially typed search remains ephemeral
- explicit catalog-local saved searches with apply and delete actions
- schema version 7 saved-search storage
- combined Dataset Readiness score and composition dashboard
- visible weighted deductions and advisory checks
- clickable issue counts that reveal matching records in the Catalog Browser
- no new analysis model or dependency

## Completed: Milestone 8B — Local image-quality analysis

- cached sharpness scoring presented with the short label **Blur** and an
  explanatory tooltip
- exact duplicate SHA-256 checks
- perceptual hashes for resized or recompressed duplicate candidates
- background analysis with progress and cooperative cancellation
- user-adjustable Blur threshold and stepped near-identical similarity control
- no automatic rejection or destructive file operation
- several LoRA readiness profiles rather than a Flux-only target
- explicit New Catalog and Delete Catalog actions; deleting a catalog removes
  its stored quality data but never deletes source images
- redundant training-text preview removed from the Catalog Browser because the
  separate Dataset Reviewer owns that workflow

## Completed: Milestone 8C — Image sets and set-scoped readiness

- named catalog-local image sets created from the current browser selection
- add/remove selection, rename, delete, and restore-set-as-selection actions
- image-set membership in ordinary and Advanced Search
- All catalog images or one named set as the Dataset Readiness scope
- readiness issue links remain constrained to the active named set
- existing Export Selected workflow accepts a restored set without duplicate code
- schema version 9 additive image-set storage
- active browser selection and current readiness scope remain ephemeral
- Blur and Similarity changes update only their dependent readiness widgets
- Similarity applies after slider release rather than continuously while dragging

## Completed: Milestone 8D — Catalog import and management cleanup

- catalog creation, opening, folder import, and deletion live in the Dataset
  Tools tab's SQLite Catalog section rather than the thumbnail browser
- New Empty Catalog remains available for manual workflows
- Create from Folder registers supported images and dimensions without requiring
  Florence or face analysis
- recursive scanning is enabled by default and can be turned off
- imported images can be saved immediately as a named image set
- exact SHA-256 duplicate files remain one catalog image and are reported with
  both a count and their complete hash values
- importing into an existing catalog asks Replace/Merge/Cancel before starting
- create, merge, and replace use a staging database; cancellation or failure
  never publishes a partial catalog
- Replace removes catalog-owned metadata and cached quality data but never
  modifies source images or prior exports
- Dataset Readiness continues to default to All catalog images
- schema remains version 9; no migration is required

## Completed: Milestone 8E — Grouped similarity review

- positive perceptual `duplicate:` searches remain in the Thumbnail tab and
  temporarily use a grouped comparison layout
- every connected cluster receives its own outlined area, heading, and concise
  comparison instruction
- overlapping pair relationships are merged into one logical cluster so one
  image never appears in several competing groups
- normal browsing, general searches, exact-copy results, negated duplicate
  searches, and mixed OR searches keep the ordinary thumbnail grid
- Dataset Readiness Possible Duplicates links use the same grouped workflow and
  retain the active image-set constraint
- existing manual selection and review controls remain authoritative; no
  automatic Keep/Reject choice or destructive operation is introduced
- loading a saved image set adds its members to existing selection rather than
  replacing earlier selection work
- Create from Folder is renamed Create from Images, and Import Folder is renamed
  Add Images
- schema remains version 9; no migration is required

## Completed: Milestone 8F — Remove Unnecessary Images

- one manually started culling action in the Thumbnail tab, operating only on
  the user's current transient selection
- a complete preview report before any thumbnail is deselected
- profile-aware low-resolution checks using the active Flux, SDXL, SD 1.5, or
  General LoRA readiness target
- current user-selected Blur and Similarity thresholds shared with Dataset
  Readiness rather than hidden duplicate settings
- explainable checks for missing/unreadable sources, explicit Reject status,
  strong screenshot/UI evidence, multiple people/faces, and extremely small
  detected faces when those provider results exist
- direct near-duplicate comparison that keeps the strongest available version
  without collapsing merely transitive similarity chains
- ranking that respects manual Keep and confirmed identity decisions before
  comparing presence, single-person evidence, identity confidence, sharpness,
  resolution, and detected-face visibility
- absent optional analysis never becomes a removal reason; the report counts
  unavailable checks and leaves those images selected unless another concrete
  problem applies
- selection is the only changed state: no Keep/Reject edit, deletion, catalog
  write, tag change, source-file change, export, or automatic image-set save
- pose, angle, outfit, expression, lighting, likeness, anatomy, and aesthetic
  balance remain explicitly outside automatic judgment until reliable local
  signals exist
- schema remains version 9; no migration is required
- Windows GUI smoke-test logging is closed before temporary-directory cleanup,
  and Pillow's deprecated `Image.getdata()` path is no longer used

## Completed: Milestone 8G — Finalize & Export

- Dataset Readiness is renamed Finalize & Export to describe its real workflow
- readiness checks and a Training Handoff card share the same finalization view
- export scope is All catalog images or the named image set selected in the tab
- Reject and Quarantined records stay visible in statistics but are excluded
  from the scope export
- the pre-export dialog carries the readiness score, unresolved checks, active
  scope, and profile-specific empty-sidecar count into the final decision
- Flux, SDXL, SD 1.5, and General / Other readiness targets preselect matching
  training-text handoff profiles
- an optional collision-safe README records the scope, profile, output counts,
  readiness notes, and non-destructive safety boundary
- the existing browser Export Selected workflow remains available for arbitrary
  selections and shares the same export implementation
- schema remains version 9; no migration is required

## Completed: Milestone 8H — Video source import

Use FFmpeg as an optional local dependency for extracting still-image candidates
from video sources, especially when a project needs action poses, unusual body
angles, or scene variety that are hard to gather from still images alone.

Completed scope:

- add a compact **Video Sources** or **Extract Frames** card to the existing
  **Analyze & Update Catalog** tab rather than creating a separate top-level tab
- place the card near the existing catalog-analysis/import actions because
  frame extraction is source preparation: video frames still need to be added,
  analyzed, reviewed in thumbnails, culled, and only later finalized/exported
- detect a remembered user-installed `ffmpeg.exe`, then the operating-system
  PATH, with clear status and setup help if FFmpeg is missing
- let the user manually choose an FFmpeg executable when automatic detection
  fails or when they want to use a specific local build
- validate the chosen executable by running a harmless version/probe command,
  show the resolved path in the extraction dialog, and remember the approved
  path in local settings for future sessions
- avoid bundling FFmpeg until a known redistributable build and license-notice
  package are deliberately chosen
- extract candidate frames into a user-chosen folder without modifying the
  original video
- support fixed-interval extraction, scene-change sampling, a required maximum
  frame count, JPEG or lossless PNG output, and a safe filename prefix
- refuse to overwrite or mix a prior extraction when matching destination names
  already exist
- optionally add frames to the active catalog, create a new catalog, create an
  image set, and then start the providers already configured in the first tab
- report the exact FFmpeg command used, output count, collision/skipped-existing
  count, command-level failures, partial-output count, and destination folder
- keep extraction local and offline; no uploaded video or online analysis is
  introduced
- treat multi-person action frames as review-needed candidates rather than
  automatically safe single-subject LoRA training images
- preserve schema version 9; video preferences remain local settings rather
  than catalog data

Resolved design notes:

- the Finalize & Export tab has free space beside readiness checks, but video
  extraction is the first step of the workflow, so it should not live in the
  final handoff view
- the Analyze & Update Catalog tab is already somewhat crowded, so the default
  implementation should be a compact launcher card that opens a focused
  extraction dialog for settings and progress
- video extraction remains a compact launcher plus focused dialog; it can move
  to a larger tab only if real use proves that necessary
- any bundled FFmpeg build must be reviewed for LGPL/GPL/nonfree licensing
  implications before inclusion in a release ZIP
- the app should continue to work without FFmpeg configured; only video-frame
  extraction should be disabled until a valid executable is found or selected

## Completed: Milestone 9A — Training Text Validation

- keep caption/tag term searching in the existing Catalog Browser rather than
  introducing a second contains/does-not-contain interface
- validate the exact effective sidecar text produced by the active Flux, SDXL,
  SD 1.5, or General / Other handoff profile
- use the canonical export training-text builder so readiness and export agree
  about layer inclusion, whitespace cleanup, and duplicate-tag removal
- make No Training Text profile-aware instead of treating every stored layer as
  if every export preset included it
- add Repeated Training Text as a review validation finding with a small capped
  readiness deduction, not an automatic blocker or removal rule
- open the exact affected image records in the Catalog Browser through the
  existing Boolean search workflow
- recalculate empty and repeated training text in the export dialog when the
  selected handoff profile changes
- exclude Reject and Quarantined records from the readiness validation scope
- correct the v0.17.0 Windows smoke test to query themed-button state through
  the `ttk` state API
- preserve schema version 9; no catalog migration or new dependency is required

## Completed: Milestone 9B — Workflow Clarity and Catalog Replacement

- remove the redundant **Exact Copies** readiness result so **Similarity match**
  and **Possible Duplicates** remain the single duplicate-review path
- rename **Export This Scope…** to **Export Training Data…**
- remove the redundant `Scope:` prefix from the Training Handoff value
- list the active Flux, SDXL, SD 1.5, or General / Other LoRA target directly in
  the Training Handoff card
- keep training-text checks in the validation/finalization flow rather than
  adding a separate caption-search interface
- when New Empty Catalog/Create from Images targets a pre-existing catalog name,
  show a confirmation dialog warning that continuing will overwrite the old
  database; declining makes no change
- stage and validate confirmed replacement before atomically publishing it
- keep source images and prior exports outside every catalog-replacement
  boundary
- close legacy edit/history and catalog-backup SQLite connections explicitly so
  development-mode GUI tests finish without the v0.18.0 ResourceWarning
- preserve schema version 9; no catalog migration or new dependency is required

## Milestone 10 — 1.0 Stabilization and Git-Ready Release

Milestone 10 is intentionally split into phases. Real-world QA, code
stabilization, public-repository preparation, and final release verification are
different kinds of work and should not be hidden inside one oversized pass.

### Phase 1 — Real-world stabilization and code audit (v0.20.0)

- diagnose the 768-frame QA database rather than inferring from UI symptoms
- stop the browser preview cache from becoming recursively cataloged provider
  input
- repair affected schema-9 catalogs without deleting files from disk
- make ordinary search tag/Trigger Keyword based rather than filename based
- bound initial browser card/preview work and keep Tk responsive while results
  arrive
- label provider progress according to the expensive work actually occurring
- require a workload-specific confirmation before an optional post-video
  provider run starts
- add a visible cooperative provider-cancellation path
- perform a whole-tree maintainability, performance, documentation, and
  reasonable-security review with concrete findings in `PHASE1_AUDIT.md`
- reconcile `BUGS.md`, `WISHLIST.md`, `ROADMAP.md`, `README.txt`, and
  `CHANGELOG.md` with the implemented behavior

### Phase 1B — Workflow feedback and curation follow-up (v0.21.0)

- replace the eventually oversized thumbnail canvas with bounded 96-image pages
  while preserving catalog-wide selection
- make the provider progress bar monotonic across Cataloging, Florence, and
  optional Face analysis rather than restarting at each phase
- move the current phase to a prominent heading above the bar
- add measured elapsed time, a stable Florence ETA after several completed
  images, and a time-based long-run notice with safe cancellation
- turn Remove Unnecessary Images into an in-browser checkbox panel so different
  dataset use cases can choose their own conservative curation evidence
- expose adjustable face-size and face-prominence thresholds
- distinguish one dominant face plus background faces from multiple similarly
  prominent faces using stored bounding boxes
- add independent session Undo Selection/Redo Selection controls without
  changing durable catalog edit history
- update regressions, bug records, wishlist items, and user documentation with
  the implemented behavior

### Phase 1C — Workstation UI follow-up (v0.22.0)

- add a native menu bar while keeping tabs as the three major workflow modes
- show browser-only Selection, View, and Browser menus only in Catalog Browser
- move secondary browser commands out of the crowded toolbar
- replace the Curation Filters and Hide Panel buttons with an attached edge
  marker, View menu command, and N shortcut
- unify chronological selection and durable catalog Undo/Redo under Ctrl+Z and
  Ctrl+Y
- make image-selection shortcuts focus-aware so text editing keeps normal
  Windows Cut, Copy, Paste, and Select All behavior
- provide explicit current-page versus all-results selection commands
- make the safe page size configurable at 25, 50, 75, or 100 images
- centralize help, shortcuts, provider guidance, and licensing in the Help menu
- add tooltips to the primary workflow and browser controls
- update regressions, bug records, wishlist items, audit, and user documentation

### Phase 1D — Responsiveness and contextual help (v0.24.0)

- replace control-covering help on primary technical fields with concise,
  hover-only question-mark icons beside their labels
- keep longer video and face guidance in the Help menu
- construct the video extraction dialog before probing FFmpeg and keep the
  external executable check off the Tk event thread
- retain a bounded in-memory working set of decoded thumbnails so recently
  visited pages return without visible reloads
- prevent held/boundary Alt page-navigation events from falling through to the
  native menu system
- preserve schema 10 and the existing local-only, non-destructive boundaries
- update regressions, bug records, wishlist items, and user documentation

### Phase 2 — Remaining hands-on QA and UI refinement

- complete another real source-to-export pass on the repaired 767-image catalog
- test v0.22.0 menus, shortcuts, attached curation marker, unified Undo/Redo,
  configurable paging, curation thresholds, Florence ETA,
  cancellation, resume/reuse, and Finalize & Export on Windows
- collect and prioritize any further UI suggestions after the workstation pass
- fix any remaining release-blocking defects without expanding into speculative
  providers or automation
- decide whether a v1.0 release candidate is justified after the real workflow
  is comfortable, not merely technically functional

### Completed: Phase 3 — Professional Git and licensing release (v0.25.0)

- public application renamed to LoRA Image Curator
- MIT application-source license added while third-party model/tool boundaries
  remain separately inventoried
- professional README, architecture, development, security, contribution,
  author/contact, and AI-assisted-development documentation added
- Git ignore policy excludes catalogs, caches, logs, models, environments,
  generated exports, and release archives
- deterministic release tooling includes source, tests, docs, and scripts while
  rejecting private/generated artifacts and unsafe archive paths
- static release audit checks documentation coverage, environment-specific
  paths, credential signatures, dynamic evaluation, and shell-enabled
  subprocesses
- LinkedIn is the public author contact; no email address is published

Screenshots and the demo reel remain later portfolio assets rather than blockers
for the source-tree cleanup.

### Phase 4 — 1.0 release candidate and final verification

- build a release candidate from the exact Git-ready source state
- test from a fresh extracted archive and a preserved upgraded catalog
- run the complete regression, warning, integrity, foreign-key, and Windows GUI
  smoke-test chain
- complete the final refactor/documentation/security delta review
- publish v1.0.0 only after the release candidate and real Windows workflow pass

## Milestone 12 — Executable and Installer

- create a proper Windows executable for ordinary use without manually invoking
  Python
- create an installer or installer-like distribution with clear install,
  upgrade, uninstall, and troubleshooting behavior
- decide how optional dependencies such as FFmpeg and face-analysis packages are
  handled, documented, or detected at install time
- verify the installed application launches cleanly, writes settings/catalogs in
  predictable user locations, and does not require source-tree write access
- preserve diagnostic launch and log-capture paths for support

## Milestone 13 — Demo Reel

- produce a short demo reel showing the complete LoRA Image Curator workflow:
  catalog creation, analysis, thumbnail review, video extraction, readiness
  validation, and LoRA training-data export
- emphasize the problem solved, workflow design, and engineering choices rather
  than every control
- use non-sensitive sample data and avoid private paths, personal images, model
  files, or credentials
- create a linkable video suitable for the Git README, LinkedIn, resume, and
  social-media use

## Milestone 14 — Portfolio and Social Listing

- publish or update the Git project page with professional formatting,
  documentation, screenshots, release artifacts, and the demo-reel link
- list the project on LinkedIn as a standalone portfolio item
- prepare concise descriptions for LinkedIn, resume, X/Twitter, and other
  relevant profiles
- choose practical hosting/linking for the demo reel from Git, LinkedIn, and
  social posts
- frame the project around Python desktop tooling, SQLite catalog design,
  dataset curation, validation workflows, release engineering, documentation,
  and AI-assisted software development

## Later planned workflow areas

- quarantine and exclusion workflows
- reusable identity-reference management as first-class database objects
- project views over the permanent catalog
- settings and contextual help where they improve real use

## Application polish

Ongoing polish should remain subordinate to actual workflow needs. Deferred
ideas belong in `WISHLIST.md` rather than displacing core milestones.
