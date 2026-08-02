LORA IMAGE CURATOR GUI v0.27.19
========================

OVERVIEW
--------

LoRA Image Curator is a local Windows application for building, curating, and
exporting permanent AI image-dataset catalogs.

The SQLite catalog is the durable center of the application. Captioning, face
analysis, review, tag curation, search, selection, and export remain separate
layers around that catalog. Provider output is preserved, user decisions are
stored independently, and final training text is derived only when it is
previewed or exported.

Version 0.27.19 is the portable-source setup candidate for Milestone 11A. The
application
has been exercised with roughly 14,000 to 17,000 local images. Current primary
workstation observations are about five seconds for a cold first launch and
three seconds for the first Browser load at approximately 14,000 images, with a
faster warm second load and a responsive UI. This release organizes Settings by
function, makes subfolder scope explicit, clarifies ownership of the shared
provider progress bar, distinguishes provider/tool diagnostics from application
defects, fixes Image Details wheel routing, synchronizes Windows launchers, and
deterministically releases Browser Tk images and application callbacks during
shutdown. It also includes public repository screening guidance and GitHub
issue templates for the first pre-1.0 source snapshot. The v0.27.17 gate
isolates the long historical Tk replay in a strict child process and reports
the tested source folder separately from the Python environment; that complete
live-Windows gate passed. v0.27.18 improves first-visitor documentation,
contributor privacy safeguards, ignore rules, and dependency-free repository
automation without changing application behavior or schema. v0.27.19 adds a
checklist-driven setup assistant, project-local environment creation, clear
required/optional dependency tiers, and first-time launcher recovery without
changing application behavior or schema. Broader
large-catalog measurements and deferred high-risk QA remain pre-1.0 work.


WHAT IS NEW IN v0.27.19
-----------------------

- `Setup and Launch LoRA Image Curator.bat` provides one numbered first-time
  setup, component-check, optional-feature, and launch menu
- the assistant creates and uses the project-local `venv`; users never need to
  activate it manually
- the required source stack and optional Face, Body/Pose, Recycle Bin, and
  FFmpeg features are reported separately
- PyTorch selection stays explicit: the assistant safely redirects a command
  copied from the official selector into the local venv or installs the
  official CPU-only build
- the face installer selects ONNX Runtime from the CUDA generation reported by
  PyTorch, including the required pre-1.27 line for CUDA 12
- ordinary launchers route a missing environment into guided setup instead of
  assuming a pre-existing installation
- no v0.27.18 release files are obsolete; catalog schema remains 12


WHAT IS NEW IN v0.27.18
-----------------------

- the public README now leads with project purpose, engineering highlights,
  honest status, clean-checkout setup, verification scope, and limitations
- clean installation is separated from overwrite-in-place upgrade guidance
- the completed v0.27.17 live-Windows golden result is recorded
- GitHub issue templates have valid metadata and pull requests receive a
  privacy, compatibility, and verification checklist
- dependency-free repository checks run source compilation, the bounded
  privacy/security audit, and current repository contracts on Windows and Linux
- common raw image/video datasets and local secret files are ignored by default
- catalog schema remains 12 and no application behavior or data migration
  changed

No v0.27.17 release files are obsolete in this update.


WHAT IS NEW IN v0.27.17
-----------------------

- the v0.27.10-and-earlier GUI history runs in a strict isolated Python/Tk
  process, preventing destroyed historical interpreters from contaminating
  current lifecycle checks while preserving stderr failure detection
- the golden runner prints both Project source and Python runtime paths and
  fails if project imports escape the folder being tested
- the maintained regression, release inventory, and cumulative Windows GUI
  chain now include the isolated-gate pass
- catalog schema remains 12 and no user-data migration is required

No v0.27.16 release files are obsolete in this update.

WHAT IS NEW IN v0.27.16
-----------------------

- `GIT_READY_CHECKLIST.md` records the first-public-repository screening steps,
  including private-data checks, public positioning, and verification commands
- GitHub bug-report and feature-request templates steer public reports toward
  workflow evidence while warning against private datasets, catalogs, logs,
  model files, credentials, and sensitive paths
- `.gitattributes` normalizes text files and keeps binary/model artifacts from
  receiving misleading text diffs if they ever appear locally
- the maintained regression, release inventory, and cumulative Windows GUI
  chain include the repository-readiness pass
- catalog schema remained 12 and no user-data migration was required

No v0.27.15 release files were obsolete in that update.


WHAT IS NEW IN v0.27.14
-----------------------

- Settings menu/pages are organized around Catalog & Paths, Image Captioning,
  Face Scanning, Body / Pose Scanning, Video Extraction, Filter Settings, and
  Privacy & Diagnostics
- Florence-2, InsightFace/ONNX Runtime, MediaPipe, and FFmpeg are named inside
  their functional pages
- catalog import, Florence input, face input, and face-reference subfolder
  choices are independent, persisted, and on by default
- only the running provider shows a green marker; Florence and Face markers
  explicitly point to the shared progress bar below
- Current work identifies the active function/provider above the shared bar
- scrolling in Image Details no longer sticks over read-only tags or details
- provider/tool limitations and app defects are explained separately
- the Florence 1,024-token terminal message is documented as a single-call
  provider/runtime warning; the app already runs one image and task at a time
- all Windows launcher banners report the current release version
- catalog schema remains 12 and no user-data migration is required

No v0.27.13 release files are obsolete in this update.


WHAT IS NEW IN v0.27.13
-----------------------

- startup provider-device inspection no longer retains the complete
  application merely to reach its result queue
- slow PyTorch or ONNX Runtime imports cannot release Tk variables from their
  worker after the GUI has closed
- regressions forbid both Tk-variable reads and any `self` access inside that
  startup worker
- the strict v0.27.12 unraisable-exception and GUI-stderr gates remain active
- no catalog schema, dependency, or user workflow changed from v0.27.12

No v0.27.12 release files are obsolete in this update.


WHAT IS NEW IN v0.27.12
-----------------------

- synchronous enlarged-view actions cancel any pending delayed redraw before
  rendering, so a timer ID cannot be lost before the viewer closes
- viewer destruction is idempotent and prevents new redraw scheduling
- the parent golden runner rejects GUI stderr diagnostics even when the GUI
  subprocess exits successfully
- live and headless regressions reproduce the exact redraw and false-success
  paths reported during v0.27.11 Windows verification
- no catalog schema, dependency, or user workflow changed from v0.27.11

No v0.27.11 release files are obsolete in this update.


WHAT IS NEW IN v0.27.11
-----------------------

- delayed enlarged-view redraws are cancelled before the viewer is destroyed
- historical GUI fixtures collect destroyed dialog cycles on the Tk main
  thread, so Python 3.14 no longer reports late `Variable.__del__` exceptions
- overwrite verification uses small synthetic user-data neighbors rather than
  copying or reading the complete installed workspace
- clean-extraction and synthetic-overlay phases print status before they run


WHAT IS NEW IN v0.27.10
-----------------------

- compilation, source/security audit, and release packaging now use the same
  signed manifest as their project-ownership boundary
- arbitrary local archives such as `Old Files to be trashed` remain untouched
  and outside current-code validation
- the release builder cannot sweep unmanifested local source or documentation
  into an archive
- shipped source retains strict documentation, security, artifact, and SQLite
  resource-lifecycle checks
- no application workflow, catalog schema, dependency, or database migration
  changed from v0.27.9

No v0.27.9 release files are obsolete in this update.


WHAT IS NEW IN v0.27.9
----------------------

- all maintained regression tests explicitly close direct SQLite connections
- Windows temporary-catalog cleanup no longer fails with `WinError 32`
- the source audit rejects bare `with sqlite3.connect(...)` contexts because
  they complete transactions but do not close the database handle
- failed catalog initialization now releases its SQLite handle immediately
- no application workflow, catalog schema, dependency, or database migration
  changed from v0.27.8

No v0.27.8 release files are obsolete in this update.


WHAT IS NEW IN v0.27.8
----------------------

- the project audit ignores the user-managed `output` folder in an established
  overwrite-in-place installation
- active catalogs, timestamped backups, and provider/export reports under
  `output` no longer stop the golden test
- databases accidentally placed in actual release source are still rejected
- the release builder excludes all `output` contents, including text reports
- no application workflow, catalog schema, dependency, or database migration
  changed from v0.27.7

No v0.27.7 release files are obsolete in this update.


WHAT IS NEW IN v0.27.7
----------------------

- golden source compilation reads the signed release manifest and checks only
  project-owned Python files
- the adjacent `venv`, catalogs, models, caches, backups, and other
  user-managed folders are excluded from the compilation gate
- malformed third-party package tests can no longer stop golden verification
  before the historical regressions and live Windows GUI checkpoints
- no application workflow, catalog schema, dependency, or database migration
  changed from v0.27.6

No v0.27.6 release files are obsolete in this update.


WHAT IS NEW IN v0.27.6
---------------------

- `test_golden_build.py` is the single authoritative handoff command
- the test creates temporary synthetic images and a schema-current catalog; it
  never opens or edits a real dataset
- the command runs the complete maintained regression history, source and
  documentation audit, deterministic packaging, clean-extraction and
  overwrite-overlay checks, and the current cumulative Windows GUI chain
- one-image Recycle Bin cleanup no longer creates a database backup before
  removing its optional complete catalog record
- multi-image delete cleanup still creates a current backup
- explicit Remove Selected from Catalog still always creates a current backup
- current version, launcher, test, and package references agree on v0.27.6

No v0.27.5 release files are obsolete in this update.


WHAT IS NEW IN v0.27.5
---------------------

- enlarged review has a small trash-can action for the current image
- the action reuses the Browser's saved Recycle Bin/catalog-cleanup policy
- the viewer closes after deletion so it cannot retain a deleted record


WHAT IS NEW IN v0.27.4
---------------------

- Settings > Filter Settings now owns the shared dataset target, whole-number
  Blur threshold, and described 96–100% duplicate-similarity value
- the Browser filter dialog's Quality tab is now Filter Settings and summarizes
  the central values rather than owning another copy
- Readiness > Possible Duplicates is explicitly the on/off Browser filter; its
  similarity percentage controls strictness only
- every thumbnail has a small bottom-right maximize icon
- enlarged review uses a compact floating strip for Previous/Next, zoom, Fit,
  100%, external viewing, and return to Browser
- the Settings window permanently reserves space for Save and Cancel
- release ZIPs extract directly into DatasetTools for overwrite-in-place updates
- if an obsolete top-level Python file exists, the smoke test names the exact
  file to move or delete without touching user data

No v0.27.3 release files are obsolete in this update.


STILL INCLUDED FROM v0.27.3
---------------------------

- normal Browser loading no longer calculates every quality-analyzed image
  against every other image; duplicate groups are indexed and single-image
  evidence is calculated on demand
- Quarantine, Restore, Recycle Bin, and record removal show responsive modal
  progress and accept cooperative cancellation
- the optional delete-time catalog cleanup setting now remains enabled after
  Save and is honored by deletion
- mouse-wheel input works over canvas/elevator-backed Analysis, Browser,
  details, and Finalize areas without taking over native controls
- body/pose percentages and duplicate similarity use bounded whole-number
  choices with value-specific plain-language descriptions
- No Face now means completed face analysis found no face; Face analysis not run
  is separate
- Show Likely Non-Person combines completed No Face and No Body/Pose evidence as
  a visibility-only triage preset
- Enlarge / Review provides zoom, pan, Previous/Next, Fit, source-scene context,
  and external-viewer handoff
- Image Quality opens a read-only explanation of Blur, duplicate, face, body,
  and analysis-threshold evidence
- the smoke test reports unexpected stale top-level Python files before
  importing historical tests


WHAT IS NEW IN v0.27.2
---------------------

- Analyze & Update Catalog has a vertical scrollbar so bottom progress and
  messages remain reachable
- the selected input folder reports how many supported images were found,
  including subfolders
- Florence and face runs have safe Pause/Resume; the MediaPipe run window has
  matching controls
- each provider says Run / Restart, skips compatible completed work by default,
  and displays the detected CPU/GPU execution path
- resumed Florence ETA is based on newly completed remaining model work
- provider folder errors identify the exact folder that was checked
- Browser > Selected Images exposes quarantine, restore, Recycle Bin, and
  complete catalog-record removal
- a setting controls whether successful file deletion also removes the complete
  image record; the safe default is no
- database-record removal always creates a fresh catalog backup first
- confirmation is required only when a destructive action exceeds the active
  25, 50, 75, or 100-image browser page size
- No image file found filters retained missing/deleted records
- fixed-interval extraction records source-video and timestamp metadata;
  Browser details show where a useful frame came from
- schema 12 adds video-origin metadata without removing earlier catalog data

Still included from v0.26.0:

- optional Google MediaPipe Pose Landmarker analysis with local model
  compatibility checks and schema-11 cached evidence
- import may skip images with no body/pose and/or no visible-face pose evidence
- Filters can show full body, partial body, body/pose, visible-face
  evidence, multiple poses, and body-analysis-not-run results
- Full-body evidence is available as a sort order
- Body / Pose Scanning Settings owns the body model, detection/visibility, and
  60–100% full-body completeness thresholds
- Quarantine Selected moves every represented present physical file and Restore
  Selected returns it without overwriting an occupied source path
- Delete sends files to Windows Recycle Bin through Send2Trash and never falls
  back to permanent deletion
- telemetry/provider-diagnostics permission is disabled by default; current
  local MediaPipe analysis has no application-configured telemetry collector
- Settings, Help, About, and THIRD_PARTY_NOTICE.md explain that third-party
  models, packages, applications, and websites remain outside the project's
  control and responsibility
- arbitrary executable provider packages remain blocked in this release

Still included from v0.25.3:

- Browser Filters combines an image set, ordinary catalog state, and any or all
  of the eleven readiness checks in one view
- Select All Checks makes it possible to review every unresolved readiness
  finding at once
- Select by Keyword and Deselect by Keyword accept comma-separated terms and
  operate across all current result pages
- image sets use Update Image Set to replace membership with the exact current
  selection
- Select Image Set in Browser replaces selection, matching the progressively
  pruned-set workflow
- Ctrl+A, Escape, and Ctrl+I now operate on all current results; explicitly
  labeled current-page alternatives remain available
- Subject Thresholds has one shared help icon and stable numeric entry widths;
  0.25 means 0.25% of the complete image

Still included from v0.25.1:

- Catalog Browser claims Alt on key-down before Windows native menu traversal
  can reinterpret held Alt+Left/Right input
- a first-priority Tk bind tag handles the complete shortcut chord before
  focused-widget and widget-class behavior
- separate arrow press/release tracking accepts fast deliberate navigation
  while suppressing operating-system key repeat


WHAT IS NEW IN v0.25.0
---------------------

- renamed the public application to LoRA Image Curator
- added an InsightFace model-pack Browse button that validates the established
  `<root>\models\<pack>` layout and confirms that ONNX files are present
- rejected typed model names containing paths or traversal components
- added the first Alt-release guard; v0.25.1 moves ownership to Alt key-down
  after live Windows testing showed release was not the primary trigger
- added professional repository documentation, MIT licensing, privacy/security
  guidance, dependency metadata, and reproducible release tooling
- new installations use `%APPDATA%\LoRAImageCurator`; existing legacy catalogs
  remain openable

Still included from v0.24.0:

- technical fields use small circled-question-mark icons beside their labels
  rather than making the pointer cover the associated input or Browse button
- help icons show concise hover guidance and follow all four application themes
- longer video-extraction guidance is available under Help > Video Extraction
- the video extraction dialog constructs first and checks FFmpeg in a
  background worker after the window is visible
- a bounded in-memory decoded-thumbnail cache keeps the current and recently
  visited pages warm without changing the existing disk cache or catalog
- repeated and final-page Alt+Left/Alt+Right events are consumed so a held Alt
  key cannot fall through to Windows/Tk menu navigation
- `test_v0240_regression.py` and `test_v0240_gui.py` cover the complete v0.24.x
  behavior; v0.25.x adds dedicated identity, compatibility, model-selection,
  Alt-chord, packaging, and Windows smoke coverage

Still included from v0.23.0:

- native File, Edit, Catalog, Tools, Settings, and Help menus
- browser-only Selection, Filters, and Browser menus appear only in Catalog Browser
- secondary browser buttons have moved to menus and shortcuts
- Remove Unnecessary Images opens from Selection or N
- Ctrl+Z/Ctrl+Y follow one chronological selection-and-catalog history
- text fields keep standard Ctrl+X/C/V/A behavior
- Ctrl+A selects all results; Ctrl+Shift+A selects the current page
- explicit current-page and all-results selection, deselection, and inversion
- bounded pages default to 100 and can be set to 25, 50, 75, or 100
- compact First, -10, Prev, Next, +10, and Last controls cover large catalogs
- Video Sources now appears above Catalog folders
- centralized help topics, shortcut reference, licensing, and About
- Clean Gray, Soft Light, Dark Workstation, and High Contrast themes
- 0.5-second live-search debounce
- Python 3.14/Tk-safe cached font objects
- `test_milestone_10_phase1c.py`, `test_v0220_gui.py`, and
  `test_v0230_gui.py` are included

MediaPipe and Send2Trash are optional dependencies. The application remains
usable without them, but body analysis and native Recycle Bin actions stay
unavailable until their vetted installer is run.


UPGRADING FROM v0.24.0
---------------------

1. Close LoRA Image Curator.

2. Extract LoRA_Image_Curator_v0.27.19.zip directly into your existing
   DatasetTools folder and allow Windows to replace older release files.

3. Keep your existing `venv`, model files, catalogs, images, settings, logs,
   and caches where they are. No v0.27.16 files need manual removal.

4. Keep your existing `venv` folder, model files, and `dataset_tools.db`
   catalog.

5. Start the application with:

       Run LoRA Image Curator.bat

Version 0.27.19 uses schema 12 and accepts both
current and historical catalog identity markers. If upgrading directly from
v0.19.0, the existing schema-10
migration removes only file records that match LoRA Image Curator's exact
legacy preview signature and image rows
left with no real file location. Existing source images, captions, valid
analyses, quality results, face results, review decisions, Trigger Keywords,
tags, exclusions, image sets, saved searches, export history, and undo/redo
history remain intact.

The old `<output folder>\thumbnail_cache` directory is not deleted
automatically. After closing v0.19.0, it is safe to delete that entire legacy
folder manually to recover disk space. v0.27.19 will ignore it if you leave it in
place and writes any new previews beneath:

    %APPDATA%\LoRAImageCurator\thumbnail_cache

Keeping a separate external backup remains sensible for valuable catalogs.


BODY ANALYSIS AND RECYCLE BIN SETUP
-----------------------------------

Run:

    Install Body and File Action Dependencies.bat

The helper identifies its sources and asks separately before:

1. installing MediaPipe and Send2Trash from PyPI
2. downloading Google's documented Pose Landmarker Full model

The recommended model is stored at:

    %APPDATA%\LoRAImageCurator\models\body\pose_landmarker_full.task

You may keep a vetted lite/full/heavy Pose Landmarker `.task` file elsewhere
and select it under:

    Settings > Body / Pose Scanning...

Then run:

    Tools > Check Body Analysis Setup

No special folder beside `app.py` is required. Models remain outside the
release ZIP. Ordinary MediaPipe analysis is local and does not require enabling
provider telemetry permission.


SELECTED IMAGE FILE AND CATALOG ACTIONS
---------------------------------------

Open Catalog Browser, select images, then use:

    Browser > Selected Images

Quarantine Selected (Ctrl+Shift+Q)

    Moves represented present files to the configured quarantine folder and
    records their original paths. The catalog records remain available.

Restore Selected from Quarantine

    Restores quarantined files without overwriting an occupied original path.

Send Selected Files to Recycle Bin (Delete)

    Uses the operating-system Recycle Bin and has no permanent-delete fallback.
    By default the image records stay in the catalog and can be found with the
    No image file found filter.

Remove Selected Records from Catalog (Ctrl+Shift+Delete)

    Leaves physical files untouched, creates a fresh catalog backup, then
    removes the image records and all dependent provider results, captions,
    tags, review data, and image-set membership.

Settings > Catalog & Paths controls whether successfully recycled files
also have their complete catalog records removed. The default is off. Any
catalog-record removal creates a fresh backup first. Because deleting records
can invalidate old undo snapshots, the current catalog edit history is cleared;
the named pre-removal backup retains the complete prior state.

A confirmation dialog appears only when a Recycle Bin or record-removal action
affects more than the current browser page size. Page size is adjustable under
Settings > Images per Browser Page: 25, 50, 75, or 100.


VIDEO SOURCES AND FFMPEG
------------------------

Video Sources appears in Analyze & Update Catalog because extracted frames are
new source material. The intended left-to-right workflow is:

    extract frames -> catalog/analyze -> review/curate -> finalize/export

Click:

    Extract Frames from Video...

The dialog appears before FFmpeg is checked. It then checks a previously
approved FFmpeg path in the background and looks for ffmpeg on the
operating-system PATH. If neither works, click Choose... and select ffmpeg.exe
manually. The selection is validated with:

    ffmpeg.exe -version

The approved path is remembered in the local LoRA Image Curator settings file. It is
revalidated before each extraction so moving or replacing the executable cannot
silently launch a stale path.

Extraction choices:

Fixed interval

    Keeps one frame every chosen number of seconds. This is predictable and is a
    good first pass when broad pose and expression coverage matters.

Scene changes

    Keeps frames at detected visual cuts using a configurable threshold. Lower
    thresholds react to subtler changes and may produce more candidates.

Maximum frames

    A required safety limit. The default is 500, and FFmpeg stops writing after
    that number even if the video or sampling rule would produce more.

Output

    JPEG uses high-quality compression. PNG is lossless but consumes more disk
    space. LoRA Image Curator writes numbered names such as movie_000001.jpg.

If the destination already contains files matching the selected prefix and
format, LoRA Image Curator asks whether to overwrite matching generated files,
keep existing files and merge only missing names, or cancel. Skip Existing uses
a same-drive staging folder so deterministic frame numbers are preserved even
when an earlier extraction contains gaps. A cancelled or failed run leaves any
published partial frames available for inspection and reports their count.

After extraction:

Save frames only

    Leaves the numbered images in the selected destination. No catalog changes
    are made.

Add frames to the current catalog

    Uses the same staged, non-destructive Merge import as Add Images. The catalog
    is published only after the complete import succeeds.

Create a new catalog from the frames

    Creates a new schema-11 catalog through the staged import workflow. If the
    selected catalog already exists, the app asks Replace, Merge, or Cancel.

Both catalog handoffs can create an image set containing the extracted frames.
If Run providers is selected, v0.27.2 asks again after the completion report,
shows the actual imported workload, and starts the caption/face providers only
after that explicit confirmation.

Multi-person action frames can provide unusual poses, but they remain risky for
a single-person likeness LoRA. Video extraction does not declare them safe. Run
normal analysis, inspect them in the Catalog Browser, use Remove Unnecessary
Images as a conservative first pass, and manually restore valuable frames where
the intended subject is clearly dominant.

FFmpeg licensing:

LoRA Image Curator does not include FFmpeg binaries or FFmpeg source code. FFmpeg is
primarily LGPL 2.1-or-later, but builds compiled with optional GPL components
become GPL 2.0-or-later, and nonfree configurations have additional redistribution
limits. If FFmpeg is ever bundled in a future release, that exact build and its
license notices must be reviewed first. LoRA Image Curator continues to avoid that
packaging decision by using only a separately installed executable selected by
the user.


STARTING THE APPLICATION
------------------------

For a new GitHub/source download, first double-click:

    Setup and Launch LoRA Image Curator.bat

Choose `1. First-time setup (recommended)`. The assistant creates `venv`,
installs the required base packages, guides the PyTorch choice, offers optional
features separately, and can launch the app. Python 3.11 or newer is still
needed because this is a source distribution; a future executable/installer is
planned so ordinary users will not need to manage Python at all.

The assistant owns `venv` automatically. Do not activate it manually unless
you are intentionally using the advanced developer workflow.

Use:

    Run LoRA Image Curator.bat

The regular launcher now uses the same visible console and error reporting as
the diagnostic launcher. Closing that console still closes the Python process,
so leave it open while LoRA Image Curator is running.

For troubleshooting, the equivalent alternate launcher remains available:

    Run LoRA Image Curator - Diagnostic.bat

Both launch app.py with:

    venv\Scripts\python.exe

The hidden VBS launcher remains unreliable on the current system and is listed
in BUGS.md.


SQLITE CATALOG MANAGEMENT AND FOLDER IMPORT
-------------------------------------------

Catalog creation, opening, folder import, and deletion are all in the SQLite
Catalog section of the LoRA Image Curator tab. The thumbnail browser no longer mixes
project-container actions with image and image-set actions.

New Empty Catalog...

    Creates an intentionally empty, portable SQLite catalog. Use this for a
    manual workflow or when a later provider run will populate the catalog. If
    that filename already identifies a LoRA Image Curator catalog, the application
    asks before replacing it with a staged empty catalog.

Create from Images...

    Creates a new catalog and registers supported images from the selected
    folder. Include images in subfolders is enabled by default and can be turned
    off. Create an image set from the imported images is also enabled by default.
    This operation reads dimensions and SHA-256 content identity but does not run
    Florence captioning or face analysis. A same-name LoRA Image Curator catalog can
    be replaced only after an explicit confirmation.

Open Catalog...

    Validates and opens an existing LoRA Image Curator SQLite catalog without changing
    it. The Catalog Browser and Finalize & Export tabs follow the selected catalog.

Add Images...

    Adds images to the current catalog. Before the import form opens, LoRA Image Curator
    asks whether to Replace, Merge, or Cancel:

    Yes / Replace
        Rebuilds the catalog from the selected folder. Catalog-owned captions,
        analyses, quality data, review decisions, tags, image sets, saved searches,
        export history, and other metadata are removed. Source images and exported
        datasets are never deleted.

    No / Merge
        Preserves existing catalog content and adds supported images from the
        selected folder.

    Cancel
        Makes no changes.

    Create, Merge, Replace, and confirmed same-name creation are built in a
    private staging database. The target catalog changes only after the complete
    import succeeds. Cancelling or hitting an error leaves the original catalog
    untouched.

Delete Catalog...

    Permanently removes the selected SQLite catalog and its catalog-owned quality
    data only after confirmation. Source images and exported datasets remain.

Exact SHA-256 duplicates stay one catalog image, so duplicate files do not create
duplicate thumbnail cards. Their file locations remain traceable, and the final
report gives both the duplicate-file count and complete duplicate SHA-256 values.


USING THE CATALOG BROWSER
-------------------------

The browser follows the SQLite catalog selected on the LoRA Image Curator tab. It
keeps search, Advanced Search, sort, and filter in the compact visible toolbar.
Refresh, image sets, saved searches, search history, selection commands, export,
and curation visibility live in browser-only menus and documented shortcuts.
Catalog lifecycle controls remain in the Catalog menu and Analyze tab.

When an analysis or folder-import run finishes, the browser refreshes from the
updated catalog.

The top-level Selection, View, and Browser menus appear only while this tab is
active. Help contains tab guidance, all keyboard shortcuts, provider
explanations, licensing, and About. Circled question marks beside technical
labels provide short contextual guidance without covering the associated
control. Dynamic readiness findings and icon-only controls retain conventional
tooltips where that behavior is more appropriate.


THUMBNAIL SELECTION
-------------------

Selection is designed for fast visual batch work:

Click

    Select one image and clear the previous selection.

Ctrl-click

    Add or remove one image without disturbing other selected images.

Shift-click

    Select the visible range between the anchor image and the clicked image.

Ctrl+A

    Select every current search/filter result across all browser pages when a
    text field is not being edited.

Ctrl+Shift+A

    Select every image on the current thumbnail page.

Escape

    Deselect every selected image across pages/results.

Ctrl+D

    Deselect every selected image across pages/results.

Ctrl+Shift+D

    Deselect only the current page.

Ctrl+I

    Invert every current search/filter result across pages.

Ctrl+Shift+I

    Invert only the current page.

Drag blank grid space

    Draw a selection rectangle. Every thumbnail touched by the rectangle becomes
    selected. Hold Ctrl while dragging to add the rectangle to the existing
    selection. Automatic scrolling during a drag is postponed.

The browser-only Selection menu contains the same current-page and all-results
commands plus Select/Deselect by Keyword and Select by Image Set. Keyword
operations accept comma-separated terms, can require Any or All terms, and
always cover every page of the current browser results.

Selections that are hidden by a new search remain selected. The bottom-right
status tells you both the total selected count and how many are currently
visible.

The details pane always operates on this selection, including selected images
that are temporarily hidden by search or filters.

Ctrl+Z, Ctrl+Y, and Ctrl+Shift+Z use one chronological browser history. A
selection action reverses as a selection action; a durable tag/review edit
reverses through the catalog's transactional edit history. The bottom status
line identifies what was undone or redone.

When a search, keyword, notes, or other text field has focus, standard Windows
text shortcuts retain priority. Ctrl+A selects text rather than images, and
Ctrl+X/C/V operate on editable text.


REMOVE UNNECESSARY IMAGES
-------------------------

Remove Unnecessary Images is a conservative culling assistant, not an automatic
dataset judge.

1. Define the candidate pool by selecting thumbnails. Select All Results or
   Select Image Set in Browser can establish the complete working set quickly.

2. Open Selection > Remove Unnecessary Images, or press N.

3. Check only the evidence appropriate for this dataset. The dialog offers:

   - already marked Reject
   - missing or unreadable source
   - below readiness resolution
   - Blur score below threshold
   - screenshot, webpage, or interface
   - no person and no face detected
   - main face too small
   - multiple similarly prominent faces
   - any multiple people or faces
   - near-duplicate of a stronger candidate

4. Adjust the face-size or relative-prominence thresholds when needed, then
   click Preview Deselection.

5. Review the pop-up report. It lists every image that would be deselected and
   every concrete reason. Nothing changes while the report is open.

6. Choose Deselect Images to apply the plan, or Cancel to preserve the complete
   selection.

7. Press Ctrl+Z if the result is not what you wanted. Continue manual
   review and save an image set deliberately when the pool is ready.

The current readiness settings supply the target:

- Flux Character LoRA and SDXL Character LoRA expect a 768-pixel short side
- SD 1.5 Character LoRA and General / Other LoRA expect a 512-pixel short side
- Blur uses the current user-selected Blur threshold
- near-duplicate removal uses the current Similarity threshold

The action can use these available signals:

- source file is missing
- quality analysis could not decode the image
- image is below the active profile's resolution expectation
- largest detected face occupies too little of the complete image
- second-largest detected face is similar in size to the largest face
- measured sharpness is below the current Blur threshold
- Florence produced strong screenshot/UI evidence
- Florence or face analysis detected multiple people/faces
- a detected face is extremely small relative to the complete image
- another directly near-identical selected image has stronger available evidence

Face prominence is based on stored face bounding boxes, not identity guessing.
By default, an image with one clear lead face and much smaller background faces
is retained. Full-body person prominence without a visible face requires a
future body-detection provider and is not inferred from a person-count label.

Near-duplicate ranking first respects manual Keep and confirmed identity
decisions. It then compares source availability, single-person evidence,
identity confidence, sharpness, resolution, and detected-face visibility.
Perceptual similarity is not transitive: if A resembles B and B resembles C,
the tool keeps both A and C when they do not directly meet the threshold.

When quality, Florence, face, or similarity analysis is unavailable, the report
counts the skipped check and leaves the image selected unless another concrete
problem applies. No-person/no-face culling is disabled by default because
full-body, profile, back-view, or difficult real photographs can still add
useful variety.

The tool cannot currently judge pose, angle, crop balance, outfit, expression,
lighting, likeness, anatomy, or aesthetic quality reliably. The final training
set remains a human decision. Applying the report does not write SQLite, change
Keep/Reject state, alter tags or captions, delete files, export data, or save an
image set.


SEARCH
------

Search is case-insensitive. Ordinary positive words use AND behavior:

    woman smiling armor

Underscore variants are recognized for tag text, so `red_dress` can match a tag
stored as `red dress`.

Compact field operators are available:

    tag:woman
    manual:studio_lighting
    ai:human_face
    excluded:outdoors
    trigger:gal_gadot
    review:keep
    identity:confirmed
    file:missing
    resolution:low
    quality:analyzed
    blur:100
    duplicate:exact
    duplicate:96

Positive terms are ANDed by default. `AND` may be written for readability. `OR`
and parentheses build alternatives. Exclude a term with `NOT` or a leading
minus:

    tag:woman AND tag:red_dress NOT tag:hat
    manual:studio -excluded:outdoors
    (manual:red_dress OR manual:armor) AND NOT file:missing

Ordinary unqualified search examines tags and Trigger Keywords. Use explicit
Advanced Search fields for filenames, paths, captions, OCR, review metadata,
identity, resolution, quality, image sets, and duplicate evidence. Search never
alters the catalog.

A positive perceptual-similarity query such as `duplicate:96` is the one
presentation exception: the result remains in this same Catalog Browser tab, but
each connected group of possible matches receives its own outlined comparison
area. This grouping is not used for normal browsing, ordinary searches,
exact-copy queries, negated duplicate queries, or OR searches that mix unrelated
results.

Advanced...

    Builds the same visible query text with Include/Exclude rows and All/Any
    matching. There is no hidden second filtering system.

Search history

    Completed searches can be remembered automatically between sessions. Click
    History... to enable or disable this behavior, change the maximum, or erase
    all history. Disabling it stops new history without deleting existing
    entries; Clear History erases them. The current partially typed query is
    never stored merely because the application closes.

Saved searches

    Save Search... explicitly names the current query inside this catalog.
    Saved Searches... can apply or delete those views. Named searches are
    separate from automatic history, so clearing history does not delete them.


BROWSER FILTERS
---------------

Click Filters in Catalog Browser to compose one pruning view without
memorizing search syntax.

Scope

    Show all catalog images or only the members of one saved image set. The
    chosen set defines the dataset against which duplicate and repeated
    training-text checks are calculated. General catalog state is separate from
    subject evidence.

Face and Body / Pose

    Choose independent face and body/pose evidence. For example, Has face and
    Full body may be used together.

Quality and Readiness

    Every Finalize & Export check is available: Missing Files, Missing Trigger
    Keyword, Unreviewed, Low Resolution, No Training Text, Identity Unconfirmed,
    Multiple Faces, Repeated Training Text, Quality Not Analyzed, Blur, and
    Possible Duplicates. Blur uses the value owned by Settings > Filter
    Settings.

    Select All Checks plus Any selected check shows every image with at least
    one unresolved finding. All selected checks shows only intersections.

Readiness Interpretation

    Target profile, Blur threshold, and duplicate similarity are shared with
    Finalize & Export. Identity and possible-duplicate findings remain warnings,
    not automatic rejection rules.

Filters affect every result page but do not alter selection, review state,
image-set membership, catalog records, or source files. Clear Filters returns
to all catalog images while leaving the ordinary search and sort intact.


DETAILS PANE
------------

The right-hand pane is deliberately compact and fixed-width so thumbnails
receive most of the window. It contains the selection summary, manual-review
controls, interactive Training Tags, quality measurements, and lower-level
metadata. Training preview is intentionally handled by the separate Dataset
Reviewer and by the export dialog rather than duplicated here.

For one image, the metadata may display:

- preferred file path, dimensions, file size, and location count
- Florence caption
- suggested identity, identity review, face count, and largest/second-largest
  face size as a percentage of the image
- active AI tags, excluded AI tags, and manual tags
- OCR text and raw detected-object string
- recommendation and reason
- review status and notes
- sharpness score and closest perceptual-hash match
- SHA-256 content identity and catalog image ID

For several images, the upper preview becomes an aggregate selection summary.
The lower metadata shows counts rather than pretending one image represents the
batch.


UNIFIED SELECTION REVIEW
------------------------

The details pane is a selection editor.

One selected image

    The preview, metadata, and controls describe that image.

Several selected images

    The preview area becomes a compact selection summary. The controls show the
    shared value when all images agree, Multiple values when they differ, and a
    blank value when none is present. Single-image Open Image behavior is
    disabled because no one thumbnail represents the complete selection.

Disposition

    Choosing Unreviewed, Keep, Needs follow-up, or Reject applies immediately to
    the complete selection. Any choice other than Unreviewed counts as reviewed.

Identity review

    Confirm, Reject, and Reset apply immediately to the strongest eligible
    suggestion for each selected image. Images without a suggestion remain
    unchanged and are reported as skipped.

Trigger Keyword

    Type a replacement and click Save Trigger Keyword, or press Enter. The explicit save
    boundary prevents partially typed text from being applied to a large
    selection. Clear removes manual Trigger Keywords from all selected images. The
    display text Multiple values is never treated as a real keyword.

Large-selection confirmation

    Edits affecting 100 or more selected images ask for confirmation. Ordinary
    smaller review work remains immediate.

Undo and redo

    Ctrl+Z undoes the latest recorded selection edit. Ctrl+Y or Ctrl+Shift+Z
    redoes the next operation. Up to 20 current-branch operations are retained.
    Undo and redo restore only user-owned review metadata; they never modify
    provider analysis, source images, captions, detections, or embeddings.

    A new edit after undo creates a new branch, as in conventional desktop
    applications, and discards the obsolete redo path. If metadata was changed
    outside the recorded history, LoRA Image Curator refuses undo/redo rather than
    overwriting newer work.

The first edit attempt in each application session creates a backup beside the
catalog with a name similar to:

    dataset_tools_backup_20260721_173000.db

The backup uses SQLite's backup API so committed write-ahead-log data is
included. Every edit is transactional: either the complete selection change is
saved, or no partial edit is committed.


TAG CURATION
------------

The Training Tags panel merges provider and user layers into one effective view.
The colors indicate provenance and state:

Blue

    An active AI object tag derived from the chosen Florence analysis.

Gray

    An AI tag excluded by the user. Click it again to restore it.

Orange

    A manual tag asserted by the user. Click it to remove the manual assignment.

The raw Florence caption and raw object-label string are never edited. Exclusion
is a separate user-owned record. If analysis is rerun and the same AI tag
reappears, the exclusion remains effective.

Add Tags...

    Enter one or many tags separated by commas, semicolons, or new lines. The
    operation applies to every selected image. Existing manual tags are skipped,
    so repeated additions do not create duplicates.

Manual and AI overlap

    If an image already has an AI tag named `woman` and you add manual `woman`,
    one orange effective chip is shown. The AI suggestion remains underneath.
    Removing the manual tag reveals the blue or gray AI state again.

Multiple selected images

    Only tags with the same effective state on every selected image are shown.
    A tag present on only some images is omitted. To make it common, use Add
    Tags... normally; LoRA Image Curator adds it only where the manual assignment is
    missing.

AI tags currently come from Florence structured object labels, which are
available when triage/object detection has been run. Natural-language captions
remain available separately for future export profiles.


IMAGE SETS
----------

Click Image Sets... in the Catalog Browser to manage deliberate saved groups.

New Set...

    Creates a named set. The current browser selection is added automatically;
    with no selection, it creates an empty set.

Update Image Set

    Replaces the highlighted set's membership with the exact current browser
    selection. This matches a progressively pruned workflow: filter and review,
    change selection, then deliberately save the new working set. An empty
    selection requires confirmation. Source files, catalog images, metadata,
    review state, and analysis results are unchanged.

Select Image Set in Browser

    Replaces the temporary browser selection with the set's current members.
    Existing review and Export Selected actions then work normally. The same
    action is available from Selection > Select by Image Set.

Rename... / Delete...

    Renaming preserves membership. Deleting a set removes only its name and
    membership records after confirmation; catalog images and source files stay
    untouched.

Named sets persist because they are explicitly saved user work. The current
browser selection and active Browser Filters remain ephemeral session state.
Choose a set directly in Browser Filters, search with
`set:"Training Candidates"`, or use Image set in Advanced Search.


FINALIZE & EXPORT
-----------------

The Finalize & Export tab combines four views of All catalog images or one
explicitly selected named image set:

- composition statistics for review states, file availability, resolution,
  Trigger Keywords, manual tags, active AI tags, and exclusions
- a transparent preparation score for the selected LoRA target
- local quality-analysis coverage, Blur results, and advisory duplicate matches
- a Training Handoff card for exporting the active scope

The score starts at 100 and deducts proportionally for missing files, missing
Trigger Keywords, unreviewed images, low or unknown resolution, missing training
text, unresolved identity suggestions, missing quality measurements, and Blur.
Each rule shows its count, actual deduction, and maximum possible deduction.
Multiple Faces and Possible Duplicates are advisory and do not reduce the score.
Exact byte copies remain one catalog image automatically and therefore do not
need a separate readiness action.

Click a check such as Missing Trigger Keyword or Low Resolution to open the
matching records in the Catalog Browser. When a named set is active, the query
remains constrained to that set. Hover over a short check label for its complete
explanation. Short labels do not imply certainty; Blur uses a tooltip to explain
false-positive risk.

Rejected and quarantined images remain visible in composition statistics but do
not count as intended training images. The score is a preparation checklist, not
a prediction that a LoRA will be good. Choose Flux, SDXL, SD 1.5, or General /
Other LoRA from the Target list. No large model or new dependency is required.

The Training Handoff card shows the active scope, eligible image count, records
excluded by review status, the currently selected LoRA target/profile, readiness
score, and unresolved finding counts. The current LoRA profile is one of the
available handoff profiles, such as Flux, SDXL, SD 1.5, or General / Other; it is
not a hidden trained model file. Export Training Data copies eligible records
only. Reject and Quarantined records remain in the catalog and readiness totals
but are not included in this final scope export.

Changing Blur or Similarity under Settings > Filter Settings updates the shared
interpretation used by Browser and Finalize & Export. Possible Duplicates is
only an active Browser visibility filter when its checkbox is selected under
Browser > Filters > Readiness.


LOCAL IMAGE-QUALITY ANALYSIS
----------------------------

Quality analysis starts only when you click Run Quality Analysis. It decodes
each available image locally and stores two replaceable measurements in the
selected catalog:

Blur

    A variance-of-Laplacian sharpness score. Higher values generally mean more
    visible edge detail. The threshold is adjustable because deliberate soft
    focus, motion, compression, and image content can affect the score.

Exact Copies

    The catalog already uses SHA-256 content identity. Identical bytes at several
    paths appear as one image with multiple known file locations and export only
    once.

Possible Duplicates

    A 64-bit perceptual difference hash suggests resized or recompressed visual
    matches. Move the 96–100 Similarity match slider to choose how strict the
    comparison is. The label beside the slider describes the match strength
    instead of presenting the advisory hash threshold as an exact duplicate
    percentage. Clicking the readiness result opens the Catalog Browser and
    places every connected group of matches in a separate outlined area. Matches
    are review candidates only; LoRA Image Curator never chooses which image to keep
    or reject automatically.

Successful current-version measurements are reused. Select Reanalyze cached
images only when you deliberately want to rebuild them. Cancellation finishes
the current image and preserves completed measurements. Source images are read
only and no data is uploaded.


DATASET EXPORT AND TRAINING HANDOFF
-----------------------------------

For a saved final set or the complete catalog, choose the scope at the top of
Finalize & Export and click:

    Export Training Data...

For an arbitrary temporary browser selection, select one or more thumbnails and
click:

    Export Selected...

Both routes open the same non-destructive dataset assembly workflow. Scope
export excludes Reject and Quarantined records. Browser export uses the exact
manual selection.

Pre-export Check

    Shows the scope, readiness score when available, unresolved readiness
    findings, and the number of empty sidecars produced by the currently chosen
    training-text profile. Export asks for confirmation when actionable findings
    remain, but it does not prevent a deliberate handoff.

Destination

    Choose a new or existing folder. LoRA Image Curator creates the folder if needed.
    It never treats the source folder as disposable.

Outputs

    Copy images

        Copies each available selected source image. The original file remains
        untouched.

    Create same-name .txt sidecars

        Writes one UTF-8 text file beside each copied image using the same stem.
        Sidecar text is derived from the selected profile.

    Create manifest.csv

        Writes one audit row per selected catalog image, including skipped or
        failed items. The manifest contains source/output paths, training text,
        Trigger Keyword, manual tags, active and excluded AI tags, raw caption,
        review state, and identity state. UTF-8 with BOM is used so the file
        opens cleanly in common Windows spreadsheet applications.

    Create training-handoff README.txt

        Writes a compact record of the scope, training-text profile, requested
        and completed counts, readiness notes, output choices, and
        non-destructive safety boundary. It deliberately does not invent
        optimizer, learning-rate, epoch, or trainer settings.

Training profiles

    Flux LoRA

        Trigger Keyword, then manual tags, then active AI tags.

    SDXL LoRA

        Trigger Keyword plus manual tags. AI tags stay preserved in the catalog but
        are omitted by this conservative preset.

    SD 1.5 LoRA

        Trigger Keyword plus manual tags, using the same conservative
        tag-oriented layering as the SDXL preset.

    General / Other LoRA

        Trigger Keyword, manual tags, and active AI tags. Preview carefully
        because trainer-specific expectations vary.

    Caption Dataset

        The latest raw natural-language provider caption only.

    Custom

        Choose any combination of Trigger Keyword, manual tags, active AI tags, and
        raw provider caption. Custom choices are remembered locally.

The tag-oriented order is deterministic and case-insensitively deduplicated:

    Trigger Keyword -> manual tags -> active AI tags

If a Custom profile combines tags and a raw caption, the tag text comes first
and the caption follows. The final text is derived on demand and is not written
back into the catalog.

Collision handling

    Rename safely

        Preserves existing files and appends `_2`, `_3`, and so on until both the
        image and sidecar names are unused. It also handles duplicate basenames
        inside the current selection.

    Skip existing

        Leaves the colliding destination file untouched and records the selected
        image as skipped.

No export policy silently overwrites a destination file.

Preview

    Preview performs no writes. It resolves the current destination names and
    shows selected count, image count, sidecar count, planned skips, manifest
    and README names, sample mappings, and example training text.

Progress, cancellation, and failures

    Export runs on a worker thread so the GUI remains responsive. Cancel stops
    after the current file. Files completed before cancellation remain in the
    destination and are listed in the manifest/export history.

    One bad image does not abort every later image. Item-level errors are written
    to `export_errors.csv`. The completion dialog reports exported, skipped, and
    failed counts and can open the destination folder.

Export history

    Schema version 6 records export runs and per-image outcomes in `export_runs`
    and `export_run_items`. This is an audit trail, not an undo stack. Ctrl+Z and
    Ctrl+Y continue to affect only user-owned catalog edits.

Safety boundary

    LoRA Image Curator export copies only. It does not move, delete, quarantine, rename, resize,
    convert, or rewrite source images. Those actions require a separate future
    milestone with stricter recovery rules.


METADATA PROVENANCE COLORS
--------------------------

Orange identifies user-entered or user-reviewed metadata. Blue identifies an
active AI suggestion. Dull gray identifies an AI tag that the user excluded.

Provider output is never rewritten to pretend it was manual. Provider-owned tag
suggestions, user-owned manual tags, and user-owned exclusions remain separate
in SQLite even when the interface merges equivalent names into one effective
THUMBNAIL CACHE
---------------

The browser creates:

    %APPDATA%\LoRAImageCurator\thumbnail_cache\

This is deliberately outside the catalog and source folders so recursive image
scans cannot treat application-generated previews as training images.

Cache names are derived from the image SHA-256 and preview size. This lets
identical image content reuse the same preview even when filenames differ.

The cache:

- contains only disposable WebP previews
- is not the source of truth
- does not replace source images
- can be deleted safely while LoRA Image Curator is closed
- will be rebuilt as needed
- is ignored by metadata import, Florence, and face-analysis discovery

v0.19.0 and earlier wrote a `thumbnail_cache` folder beside the catalog. Schema
10 removes any exact legacy preview records from the catalog but leaves that
folder on disk. It can be deleted manually while LoRA Image Curator is closed.

Thumbnails preserve the complete image inside a square neutral background.
They are not cropped. Ordinary result sets use bounded pages rather than
growing one canvas indefinitely. Settings > Images per Browser Page offers 25,
50, 75, or the safe maximum of 100. First, -10, Prev, Next, +10, and Last
replace page widgets while preserving the complete selection across the catalog.
Forward controls disable at the final page; backward controls disable at page one.


MISSING FILES
-------------

The catalog may retain valuable metadata after a file is moved, disconnected,
or marked missing.

When the preferred file path is unavailable:

- the card remains in the catalog
- a FILE MISSING badge appears
- the preview may be unavailable unless a thumbnail was already cached
- Open Image is disabled
- search and metadata inspection continue to work

This is intentional. Missing files should not silently erase catalog work.


PROVIDER 1 — FLORENCE-2
-----------------------

Florence creates detailed captions.

Optional triage also provides:

- object detection
- visible-text recognition (OCR)
- estimated person count
- likely screenshot or interface detection
- candidate recommendations and review reasons

Compatible results can be reused for unchanged images.

Provider workflow progress is displayed above one continuous bar:

- Cataloging occupies the first small workload segment
- Florence analysis continues from that point rather than restarting at zero
- optional Face analysis occupies the final segment
- elapsed time and remaining-time estimates are based on completed images in
  the current phase
- ETA remains "calculating" until at least five completions and two seconds of
  real timing exist
- a measured estimate of ten minutes or more shows an amber notice; Cancel Run
  remains cooperative and preserves completed catalog results


PROVIDER 2 — FACE DETECTION & IDENTITY MATCHING
------------------------------------------------

The face provider can:

- detect every visible face in an input image
- store face bounding boxes and detector confidence
- store normalized identity embeddings in SQLite
- build a reference profile from a folder of one person's images
- compare detected faces with that profile
- create reviewable identity suggestions and general identity tags
- reuse face results when the image and exact model are unchanged
- export a separate face_results_*.csv report

The GUI field for the identity/training token is:

    Trigger Keyword

This is the keyword associated with the reference set and later training
workflow. Internal database names remain compatible with existing catalogs.

See FACE_ANALYSIS.md for architecture and matching details.


FACE MODEL LICENSE
------------------

InsightFace's Python code is MIT licensed.

The pretrained model packs distributed by InsightFace, including buffalo_l,
are licensed for NON-COMMERCIAL RESEARCH USE ONLY unless the user obtains a
separate license from InsightFace.

LoRA Image Curator:

- does not include those model weights
- does not relicense them
- asks before allowing InsightFace to download a missing model
- records a model fingerprint with stored analysis
- allows a different model name and model home for future alternatives

See MODEL_LICENSES.txt.


FACE DEPENDENCY INSTALLATION
----------------------------

When the face provider is not already installed, double-click:

    Install Face Analysis Dependencies.bat

Then run:

    Check Face Analysis Setup.bat

or click Check Setup in the GUI.

The same actions are available as options 5 and 6 in the setup launcher. The
installer removes conflicting CPU/GPU ONNX Runtime variants before selecting a
line from the CUDA generation bundled with PyTorch:

- CUDA 12: `onnxruntime-gpu>=1.21,<1.27`
- CUDA 13: `onnxruntime-gpu>=1.27,<1.30`
- CPU-only PyTorch: `onnxruntime>=1.21,<1.30`

Do not install `onnxruntime` and `onnxruntime-gpu` together. Starting with ONNX
Runtime 1.27, the default GPU package uses CUDA 13, so the newest package is not
automatically compatible with a CUDA 12 PyTorch environment.

The current tested system reports CPUExecutionProvider rather than
CUDAExecutionProvider. Face analysis works through CPU fallback, but GPU
availability remains an open issue documented in BUGS.md. This does not apply
to Florence: Florence uses its separate PyTorch path, selects CUDA/FP16 when
PyTorch reports CUDA available, and displays/logs the actual device.


PRIVACY AND DATA OWNERSHIP
--------------------------

LoRA Image Curator is designed for local operation.

User activity is ephemeral unless persistence is necessary for the requested
feature or explicitly chosen by the user. Search text exists only in the current
session unless automatic history is enabled or Save Search is used deliberately.
Both forms of persistence remain local and user-controllable.

It does not upload:

- source videos
- source images
- reference images
- captions
- face embeddings
- Trigger Keywords
- catalog contents
- browser selections

Source videos are not changed by extraction. Source images are not changed by
folder import, analysis, browsing, or manual review.

The user owns the catalog. SQLite and CSV are open, inspectable formats. The
application should remain usable without a proprietary cloud service.

See DESIGN_PHILOSOPHY.md for the complete project principles.


PROJECT DOCUMENTS
-----------------

BUGS.md

    Confirmed defects and unresolved technical problems.

ROADMAP.md

    Work that is currently intended or expected.

WISHLIST.md

    Interesting future possibilities that are not current commitments.

DESIGN_PHILOSOPHY.md

    Product, privacy, data-ownership, and architecture principles.

CHANGELOG.md

    Release-by-release changes.

FACE_ANALYSIS.md

    Face provider and identity-matching architecture.

MODEL_LICENSES.txt

    Model and runtime licensing notes.

THIRD_PARTY_NOTICE.md

    Privacy, telemetry, compatibility, and third-party responsibility notice.

docs/DEVELOPMENT.md

    Current development, regression, golden-build, and release procedure.


REGRESSION TESTS
----------------

Ordinary use does not require running tests. They are included so a milestone
can be checked after files are copied into the project folder.

Activate the virtual environment and run the one authoritative test:

    python -X dev test_golden_build.py

It creates a temporary synthetic fixture for historical checks, compiles and
audits the source, runs every maintained non-GUI regression, rebuilds the
release twice to prove deterministic output, verifies clean extraction and the
supported overwrite-in-place overlay, then runs the current cumulative GUI
chain. Several temporary windows will open and close during the Windows
GUI phase. That is expected.

`python -X dev test_golden_build.py --no-gui` is available for headless
development checks. A build is not recorded as golden until the default command
passes on the supported live Windows desktop.

A passing default run ends with `GOLDEN BUILD PASSED`. Any traceback or nonzero
exit indicates a failed gate; the runner stops at the first failure.
`docs/GOLDEN_TEST.md` records the exact coverage and honest limits of the result.


KNOWN LIMITATIONS IN v0.27.19
---------------------------

- Florence object detection and regional OCR follow the official 1,024-token
  generation example. Some Transformers/model-config combinations print a
  warning that the call may exceed the model's predefined 1,024-token length.
  This is one image/task call, not the complete folder being submitted at once.
  Watch failed-image counts while compatibility testing continues.
- Body/full-body/visible-face classifications are probabilistic pose evidence,
  not guarantees. Extreme foreshortening, occlusion, upside-down bodies, motion
  blur, and unusual grappling poses can reduce accuracy.
- Only vetted MediaPipe Pose Landmarker bundle names are accepted. Arbitrary
  executable providers and general third-party package installation are not
  supported yet.
- LoRA Image Curator cannot restore a file from the Windows Recycle Bin itself;
  use Windows for recovery. Reversible in-app recovery is provided by
  Quarantine/Restore.
- Provider pause and cancellation are cooperative. The current model operation
  must finish before the worker can pause or stop at the next safe image boundary.
- Bounded paging prevents full-catalog widget growth, but it is not true
  viewport recycling. Very large catalogs may eventually justify
  virtualization if explicit pages become inconvenient.
- The old v0.19.0 on-disk `thumbnail_cache` is not deleted automatically.
- Repeated Training Text detects exact effective sidecar matches. It does not
  semantically judge whether intentionally shared identity anchors are useful or
  whether different images need additional pose, outfit, expression, crop, or
  scene tags.
- FFmpeg must be installed separately or selected manually. LoRA Image Curator does
  not download, update, or bundle it.
- Scene-change sampling detects visual cuts, not the most useful pose, facial
  expression, character identity, or sharpest moment inside a shot.
- Fixed-interval extraction estimates the complete output from FFprobe duration.
  Scene-change sampling cannot know its final count before visual analysis.
- Matching extraction names require an explicit Overwrite, Skip Existing, or
  Cancel choice. Skip Existing preserves deterministic names but still decodes
  the video into a same-drive staging folder before merging missing files.
- Review notes are stored by the schema but are not editable in the compact
  current interface.
- AI tag chips currently come from Florence object labels, not from parsing the
  natural-language caption or a dedicated broad-vocabulary tagger.
- Built-in profile names describe useful defaults, not a guarantee that every
  trainer or model family requires exactly those caption layers. Preview the
  output and adjust Custom when a trainer has different requirements.
- Custom profile choices are remembered, but multiple named user-created
  profiles are deferred to WISHLIST.md.
- Export is directory-based. ZIP export, resizing, conversion, and direct
  trainer launch are intentionally deferred.
- Export cancellation is cooperative and occurs after the current file finishes.
- Tag synonyms and canonical alias groups are deferred to WISHLIST.md.
- Readiness scoring summarizes preparation work; it does not predict LoRA quality.
- Similarity groups use a compact 64-bit perceptual hash. They are useful for
  near-identical review but are not semantic, pose, likeness, or anatomy models.
- Similarity Review groups candidates for manual comparison; it does not rank a
  preferred image or automatically make Keep/Reject decisions.
- Perceptual hashes are intentionally conservative review aids and can match
  legitimately different images, especially visually simple compositions.
- Remove Unnecessary Images can rank direct near-duplicates and use available
  quality/usability signals, but it does not understand pose, outfit, expression,
  lighting, likeness, anatomy, or aesthetic balance.
- Curation exposes face-size and relative-face-prominence thresholds per run;
  resolution, Blur, and similarity continue to share the active readiness
  settings so the same evidence is interpreted consistently.
- Face prominence uses face bounding boxes. It cannot measure the full-body
  prominence of a turned-away or faceless person without a future person-box
  provider.
- The identity controls review the strongest image-level suggestion rather than
  exposing every detected face and candidate match individually.
- Drag-box selection does not yet auto-scroll near the top or bottom edge.
- Thumbnail size is fixed.
- Duplicate-group comparison mode is not yet paged because it intentionally
  presents complete comparison groups; exceptionally large duplicate searches
  may still justify viewport virtualization.
- Reusable face-identity reference libraries are not yet managed as first-class
  GUI objects; named dataset image sets are available.
- CUDAExecutionProvider remains unavailable on the tested system.
- The optional hidden VBS launcher remains unreliable; use Run LoRA Image Curator.bat.

These limitations are separated into bugs, roadmap items, and wishlist ideas in
the corresponding project documents.


VERSION 0.15.0 — REMOVE UNNECESSARY IMAGES
-------------------------------------------

Version 0.15.0 adds a preview-first culling pass to the Catalog Browser thumbnail
area. It helps remove obvious weak or redundant candidates from an explicitly
selected pool while preserving human control over the final dataset.

Safety rules:

- folder import reads source images only; it never moves, renames, or modifies them
- export copies source images only and leaves them unchanged
- existing export destination files are never overwritten
- exported image/sidecar pairs are staged under temporary names before promotion
- provider output and user curation remain separate
- final training text is derived rather than stored as a mutable caption
- missing images are reported as skipped
- item failures are isolated and reported in `export_errors.csv`
- cancellation stops after the current item and preserves completed work
- manifests include partial/cancelled outcomes
- export audit history is separate from undoable catalog-edit history
- image-set deletion never deletes catalog images or source files
- folder import is staged before publication
- cancellation or failure leaves the original catalog unchanged
- Replace requires confirmation and never deletes source images or exports
- exact SHA-256 copies remain one catalog image and are reported explicitly
- similarity review never changes disposition, selection, set membership, or files
- saved image sets add to the current transient selection without clearing it
- Remove Unnecessary Images changes only the confirmed transient selection
- its report exposes unavailable checks and unsupported judgments
- schema 10 repairs legacy preview-cache records without deleting files from disk
- schema 11 adds optional body/model evidence without removing prior data
- schema 12 adds optional video-origin evidence without removing prior data
- quarantine/restore records every physical move and never overwrites an
  occupied original path
- Delete uses native Trash/Recycle Bin support and has no permanent fallback

Milestone 10 Phase 2 continues hands-on Windows QA and the user's dedicated UI
refinement pass before Git/licensing and release-candidate phases.
