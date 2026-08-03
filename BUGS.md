# LoRA Image Curator Bug Report

This file contains defects and unresolved technical problems. Planned features
and speculative enhancements belong in `ROADMAP.md` or `WISHLIST.md`.

## Fixed in v0.27.23

### Base setup could silently install CPU-only PyTorch on an NVIDIA computer

The base installer installed `requirements.txt` before asking the user to
select a PyTorch build. Because `timm` depends on Torch and Torchvision, pip
could satisfy that dependency with CPU-only PyTorch. The later setup check saw
that PyTorch existed and preserved it, leaving Florence on the CPU even with a
supported NVIDIA GPU.

Setup now chooses PyTorch first, detects visible NVIDIA hardware paired with a
non-CUDA PyTorch runtime, and offers a focused Windows repair using the reviewed
PyTorch 2.13.0 / Torchvision 0.28.0 CUDA 13.0 pair. The repair checks the NVIDIA
driver before changing packages, records the pre-repair environment, executes
a real CUDA tensor operation afterward, and realigns optional ONNX Runtime only
when face-analysis packages were already installed.

## Fixed in v0.27.22

### Native Florence checkpoint failed its image-token contract

v0.27.21 combined native Transformers 4.56.2 code with Microsoft's original
Florence checkpoint. Hugging Face's native processor expects image-token
metadata supplied by its converted checkpoint, so a real run could stop with
`BartTokenizerFast has no attribute image_token` even though the static
security checks passed. Florence now uses the pinned
`florence-community/Florence-2-large-ft` conversion, validates all three live
task prompts plus a bounded one-token generation before processing the first
unfinished image, and continues to forbid repository code and pickle weights.

Exact successful results previously stored under the Microsoft checkpoint with
Transformers 4.49.0 or 4.56.2 are recognized as reviewed compatible results.
With reuse enabled, a large catalog therefore resumes its unfinished images
without regenerating the completed captions and triage evidence.

## Fixed in v0.27.21

### Florence could execute changing repository code during model loading

The former Florence loader used `trust_remote_code=True` with an unpinned
Hugging Face repository. That allowed model-repository Python files to execute
locally and made the effective implementation change independently of the
application release. Florence now runs through the native, pinned Transformers
4.56.2 implementation, pins Microsoft's exact verified model snapshot,
requires safetensors weights, and rejects any non-native loaded class. Existing
4.49.0 environments are reported as requiring a base-dependency update.

## Fixed in v0.27.20

### Recycle Bin safety was misleadingly bundled with body analysis

The v0.27.19 setup menu and installer treated MediaPipe body analysis and
`Send2Trash` as one optional feature. This made a lightweight, protective file
dependency appear to require an unrelated provider and model download. Base
setup now installs `Send2Trash` automatically, the body installer owns only
MediaPipe/model setup, and deletion still stops without any permanent fallback
if native Recycle Bin handling is unavailable.

## Fixed in v0.27.17

### Cumulative Windows GUI replay could fail nondeterministically after passing

The v0.27.16 application runtime was unchanged from the Windows-passing
v0.27.15 build, but the golden GUI fixture still created and destroyed many
independent Tcl interpreters in one Python process. Python 3.14 could later
finalize objects from those historical interpreters during a newer checkpoint,
producing `main thread is not in main loop` at garbage-collection-dependent
times. The v0.27.10-and-earlier chain now runs in a strict isolated process;
stderr and non-zero exits still fail the build. The golden runner also reports
and verifies the tested source folder separately from its Python runtime.

## Fixed in v0.27.15

### Live-Windows golden replay still reported three late Tk finalizers

The v0.27.13 provider-device worker fix correctly stopped that worker from
retaining the application, but it was not the only asynchronous ownership path.
Browser thumbnail task closures still referenced the complete Browser frame,
decoded `PhotoImage` objects remained in Browser caches until later garbage
collection, and Browser/Readiness callbacks formed explicit Python cycles back
to the application. A worker or later collection pass could therefore finalize
Tk objects after their interpreter had closed and report
`RuntimeError: main thread is not in main loop`.

Thumbnail and folder-count workers now retain only plain task data and
thread-safe queues. Browser shutdown releases decoded Tk images on the GUI
thread, destroys card controllers, and detaches application callbacks;
Readiness shutdown detaches its application callbacks as well. The live GUI
gate retains a synthetic Tk image and forces collection after shutdown so this
boundary remains tested.

## Fixed in v0.27.14

### Image Details scrolling stopped over read-only text surfaces

The scoped wheel router deliberately preserves native Text-widget scrolling.
Training Tags and Image Details use disabled Text widgets only as rich read-only
display surfaces inside a larger scrollable inspector, so they had no useful
native scrolling but still prevented the outer inspector from moving. Those two
surfaces are now explicitly registered to the details canvas. Editable and
independently scrollable Text widgets retain their native wheel behavior.

### Windows launcher banners reported v0.27.7

The application, package metadata, and release documentation were v0.27.13, but
four batch-file banners had not advanced since v0.27.7. The launcher text now
matches v0.27.14 and the current-version regression inspects every launcher.

## Fixed in v0.27.13

### Startup device worker retained the destroyed Tk application

Provider-device inspection correctly copied the InsightFace settings into
plain strings before starting its thread, but the worker still referenced
`self.message_queue`. Slow PyTorch or ONNX Runtime imports could therefore
retain the complete `DatasetToolsApp` after its Tk interpreter was destroyed.
When the worker later released that final reference, Python 3.14 could finalize
Tk variables on the worker thread and report three
`RuntimeError: main thread is not in main loop` exceptions. The worker now
captures only plain setting values and the thread-safe queue, so application and
Tk ownership remain on the GUI thread.

## Fixed in v0.27.12

### Immediate viewer redraw could disown a delayed Tcl callback

v0.27.11 stored and cancelled the currently known redraw timer, but its
synchronous Zoom, Fit, and 100% path called the renderer directly. The renderer
cleared the stored timer ID even when a delayed Configure redraw was still
queued in Tcl. Closing afterward could no longer cancel that callback, producing
`invalid command name ..._redraw`. Scheduled and immediate redraws now have
separate lifecycle paths; an immediate render cancels the pending timer before
drawing, and destruction prevents any new redraw from being queued.

### Golden verdict ignored Tcl background errors on stderr

Tcl reports a missing command from an orphaned `after` script as a background
diagnostic, not an unraisable Python exception. The v0.27.11 GUI subprocess
therefore exited with status 0 and the parent printed `GOLDEN BUILD PASSED`
despite the console error. The parent golden runner now captures the GUI
process's stderr, shows any diagnostic, and refuses the success verdict unless
both the exit status and the stderr boundary are clean.

## Fixed in v0.27.11

### Golden GUI pass still emitted Tk lifecycle errors

The v0.27.10 Windows gate reached its success line but printed three unraisable
Tk Variable finalizer exceptions and one orphaned enlarged-view `_redraw`
callback. v0.27.11 collected known historical dialog cycles on the Tk main
thread, made the current GUI gate fail if any unraisable cleanup error returns,
and gave the viewer ownership of its delayed redraw timer. Live v0.27.12
verification proved that the three variable finalizers instead came from a
startup worker retaining the destroyed application; v0.27.13 closes that
remaining ownership gap.

### Overlay verification copied the complete installed workspace

The v0.27.10 gate simulated overwrite installation with
`copytree(PROJECT_ROOT, overlay)`. In an established installation this copied
the adjacent virtual environment, runtime catalogs, backups, and archived
files into a temporary directory, causing a long silent pause and crossing the
documented user-data boundary. The gate now builds only small synthetic
user-managed neighbors, proves that release files are overwritten and those
neighbors are preserved, and announces both packaging-verification phases.

## Fixed in v0.27.10

### Project audit scanned an explicitly archived local source folder

The v0.27.9 audit recursively entered `Old Files to be trashed` and enforced
current SQLite rules against historical tests that are neither executed nor
shipped. The audit and release builder now share the signed release manifest as
their ownership boundary, matching the compiler. Unmanifested local archives
are ignored, while every manifested project file retains the complete audit.

## Fixed in v0.27.9

### Historical regressions left SQLite handles open on Python 3.14/Windows

Several older tests used `with sqlite3.connect(...)` as though leaving that
block closed the connection. SQLite's connection context manager completes the
transaction but does not close its native handle. Python 3.14 reported the
leak, and Windows then refused to remove the temporary catalog with
`WinError 32`. Every maintained direct connection now has explicit
`contextlib.closing` ownership, the audit rejects the misleading pattern, and
failed `Catalog` construction also releases its handle immediately.

## Fixed in v0.27.8

### Golden audit treated installed runtime output as release contamination

The corrected compiler reached the project audit in the user's established
`DatasetTools` installation, where the application legitimately stores its
active catalog and timestamped backups under `output`. The audit incorrectly
treated those user files as public source and stopped before the GUI
checkpoints. `output` is now excluded as a complete user-managed runtime
directory by both the audit and release builder. Forbidden databases placed in
actual source remain release failures.

## Fixed in v0.27.7

### Golden compilation scanned the adjacent virtual environment

The first v0.27.6 golden-build command recursively compiled the complete
`DatasetTools` installation folder. Because the supported layout deliberately
keeps `venv` beside the application, that check entered third-party package
tests and stopped on a malformed MediaPipe source file before reaching any GUI
checkpoints. The compiler now uses `RELEASE_MANIFEST.sha256` as its ownership
boundary and verifies only shipped project Python. Virtual environments,
catalogs, models, caches, and other user-managed folders remain outside the
project gate.

## Fixed in v0.27.6

### Single-image delete cleanup created a disproportionate database backup

The optional “also remove the complete catalog record” policy reused the bulk
cleanup rule even when the user deleted one reviewed image. Delete-associated
cleanup now creates a fresh catalog backup only when it removes more than one
complete record. Explicit **Remove Selected from Catalog** remains
always-backed-up because it has no operating-system Recycle Bin recovery path.

### Current release verification was scattered across historical commands

The project had a strong historical regression collection but no single
authoritative handoff command, and v0.27.5 added a viewer action without its own
named regression/GUI generation. `tests/test_golden_build.py` now creates a safe
synthetic fixture and owns automated, GUI, audit, deterministic-package,
clean-extraction, and overwrite-overlay verification in one fail-fast run.

## Fixed in v0.27.5

### Enlarged review had no direct single-image delete action

The enlarged viewer now exposes a small trash-can control and delegates the
action to the Browser's existing Recycle Bin workflow. The saved catalog-record
setting and one-image confirmation behavior therefore remain consistent with
deletion from the grid.

## Fixed in v0.27.4

### Settings Save button could be pushed outside the visible client area

The Settings Notebook was packed before its action row. Under some Windows
display-scaling/font combinations, the Notebook's requested height could claim
the complete window and leave Save/Cancel below the visible client area. The
dialog now reserves a fixed grid row for its permanent footer, and the Windows
smoke test verifies that Save is mapped from the Paths & File Actions page.

### Shared filter controls were split across several workflow surfaces

The Browser filter dialog, Finalize & Export, and central Settings exposed a
mix of editable and read-only interpretation values. Shared dataset target,
Blur, and duplicate-similarity values now live under Settings > Filter
Settings. Browser Filters retains its on/off readiness checkboxes and clearly
identifies Possible Duplicates as the switch that limits Browser visibility.

### Versioned release folder broke established relative-path workflow

v0.27.3's versioned archive parent introduced more path confusion than it
prevented. v0.27.4 returns to flat archives intended to overwrite release files
inside `DatasetTools`. The smoke preflight still identifies obsolete top-level
Python files by exact name, but now instructs the user to move only those files
instead of requiring a clean application directory.

## Fixed in v0.27.2 build 4

### Historical GUI smoke test expected the old body-analysis label

The inherited v0.27.0 GUI checkpoint still asserted **Run Body Analysis** after
v0.27.2 intentionally changed every provider action to explicit
**Run / Restart** wording. The current application was correct; the historical
assertion now expects **Run / Restart Body**, and a dependency-light regression
protects that current test contract.

The cumulative Windows smoke test also now announces that it will open and
close several temporary application windows while replaying historical GUI
checkpoints. Those windows are test fixtures, not application restart behavior.

## Fixed in v0.27.2 build 3

### Provider device inspection accessed Tk variables from a worker thread

The startup device check read InsightFace model settings through
`tk.StringVar.get()` after launching its background thread. Rapid GUI teardown
could destroy the Tcl interpreter first, producing `RuntimeError: main thread is
not in main loop`. The resulting error-log attempt could then target an already
removed smoke-test AppData folder. The main thread now copies the two setting
values before the worker starts, and a dependency-light regression prevents Tk
variable access from returning to that worker.

### Historical GUI smoke test expected a superseded confirmation control

The inherited v0.26.0 smoke test still required
`SettingsDialog.confirm_trash_var` after v0.27.2 intentionally replaced that
checkbox with confirmation whenever a destructive selection exceeds the active
browser page size. The current dialog was correct; the stale assertion now
checks the compatibility setting and verifies that the obsolete dialog variable
is absent.

## Fixed in v0.27.3

### Browser refresh performed an all-pairs duplicate comparison

Every Browser load calculated the nearest perceptual-hash neighbor for every
image. A 14,000-image catalog therefore performed roughly 98 million pair
comparisons before showing page one. Normal Browser loading no longer computes
catalog-wide nearest neighbors. Bounded 96–100% duplicate groups use indexed
Hamming-neighborhood lookup, one Image Quality popup computes only its selected
image on demand, and selection culling uses that same bounded lookup for the
explicitly chosen candidate scope. Timing logs now separate query/projection
time from filtering, sorting, and page construction.

### Large destructive batches blocked Tk's event loop

Quarantine, restore, Recycle Bin deletion, and complete catalog-record removal
now run behind a modal worker dialog. The main application remains deliberately
locked during destructive work, while Tk stays responsive enough to repaint
determinate progress and accept cooperative cancellation. Large SQL selections
and removals use bounded parameter batches; selected file locations are resolved
once instead of queried again inside the worker.

### Extracting over an old release could import stale test modules

The release archive now contains a versioned parent folder. The v0.27.3 smoke
test also compares top-level Python files with the release manifest before
importing historical checkpoints and reports unexpected files with fresh-folder
instructions. Catalog, image, model, and cache subfolders are excluded from that
diagnostic.

### Delete-file record-removal setting was reset immediately after Save

The central Settings dialog saved the value correctly, but the Analysis tab's
shared `AppSettings` reconstruction omitted
`delete_catalog_record_with_file`, restoring its safe default of false. The
merge now retains the setting, the Browser receives the updated object, and
regression coverage verifies settings-file round-trip plus delete-workflow
wiring.

## Open bugs

### Florence triage may print a 1,024-token generation warning

**Status:** Open provider/runtime compatibility investigation  
**Impact:** The warning appears in the terminal; completed images observed so
far have produced the expected final results.

#### Verified boundary

LoRA Image Curator already sends one image and one Florence task per generation
call. The warning is not caused by sending the complete image collection to the
model at once. Object detection and regional OCR request 1,024 new tokens, which
matches the current
[official Transformers Florence-2 object-detection example](https://huggingface.co/docs/transformers/model_doc/florence2).
Some Transformers/model-config combinations warn because the requested new
tokens plus the short task prompt exceed the model's predefined 1,024-token
length.

#### Current handling

- label this as a provider/runtime diagnostic in Settings and the Status log
- retain per-image, per-task execution and committed-result reuse
- keep reporting failed images instead of interpreting the warning alone as an
  application failure
- do not truncate object/OCR output speculatively before representative
  stress-test evidence

#### Future investigation

- reproduce under the supported Transformers 4.49.0 environment
- compare warning frequency and output completeness across caption, object
  detection, and regional OCR tasks
- test a dynamically bounded `max_new_tokens` value against dense-object and
  text-heavy images before adopting it
- if a future provider genuinely needs smaller work units, keep automatic
  batching internal and preserve Pause/Resume/Cancel and committed-result reuse

### Large-catalog launch and Browser latency need broader measurement

**Status:** Open performance baseline  
**Observed:** About five seconds for first launch and three seconds for the first
Browser load at roughly 14,000 images on the primary test workstation. A warm
second load is faster, consistent with cache reuse. The UI remains responsive.

The current values are acceptable for the Git-ready candidate, but launch-stage,
database-query, preview-cache, memory, and cold/warm Browser timings still need
repeatable measurement before a 1.0 performance claim.

### ONNX Runtime is not exposing CUDAExecutionProvider

**Status:** Open  
**Impact:** Face analysis works, but currently falls back to CPU on the user's
RTX 5070 Ti system.

#### Observed setup

- InsightFace 1.0.1
- ONNX Runtime 1.27.0
- available providers:
  - AzureExecutionProvider
  - CPUExecutionProvider
- missing provider:
  - CUDAExecutionProvider

#### Confirmed behavior

The provider architecture, database migration, face detections, identity
suggestions, and stored-result reuse all work. The problem is limited to the
ONNX Runtime GPU execution path.

#### Current workaround

Continue using CPU fallback while the main application is developed. LoRA
Image Curator reports the actual execution provider rather than claiming GPU
use.

For new or repaired installs, v0.27.19's guided face setup reads the CUDA major
version bundled with PyTorch, removes both ONNX Runtime variants, and installs
only the matching package line. This specifically prevents the observed CUDA
12 plus ONNX Runtime 1.27 mismatch from being recreated. Existing environments
must rerun the face installer before this mitigation applies; GPU availability
still requires a successful local Windows check and remains open here until
confirmed.

#### Future investigation

- confirm whether `onnxruntime-gpu` or CPU `onnxruntime` is installed
- inspect `pip show` and package replacement order
- verify CUDA and cuDNN compatibility for the installed ONNX Runtime build
- inspect missing DLL errors with ONNX Runtime diagnostics
- test whether PyTorch CUDA libraries can be preloaded for ONNX Runtime
- avoid destabilizing the working Florence/PyTorch environment

Florence is not affected by this bug. Its PyTorch path selects CUDA and FP16
when `torch.cuda.is_available()` is true, and v0.27.2 displays/logs the detected
device so CPU fallback cannot be mistaken for GPU execution.

### Hidden/no-console VBS launcher closes or fails to preserve the GUI

**Status:** Open  
**Workaround:** Use `Run LoRA Image Curator.bat` or `Run LoRA Image Curator - Diagnostic.bat`

#### Observed behavior

The visible normal and diagnostic batch launchers open the GUI reliably. The
optional VBS/pythonw launcher intended to hide the console does not keep the GUI
running reliably on the current Windows system. This entry was reviewed during
the v0.17.0 release and remains open because the failure cannot be reproduced or
verified outside that Windows installation.

#### Current suspicion

The hidden launcher may end in a way that prevents the GUI process from
remaining active. This has not been confirmed.

#### Future investigation

- add startup logging before Tkinter initialization
- test `pythonw.exe` directly from File Explorer
- test Windows detached-process creation flags
- compare VBS and PowerShell launch behavior
- inspect security-software interference
- consider packaging the application as an executable

## Fixed in 0.27.2

### Scrollable Analysis tab initially failed during GUI startup

The first v0.27.2 archive referenced an undefined `AppTheme.background` field
while constructing the new Analysis canvas. The intended
`AppTheme.panel_background` field is now used. A dependency-light contract test
also checks every `self.theme.<field>` reference in `app.py` against the
declared theme fields, so this class of typo no longer depends on a live
Windows/Tk smoke test for detection.

### Analysis progress could be pushed below the visible tab

Provider cards made the Analyze tab taller than some practical Windows layouts,
leaving the progress bar and status messages unreachable. The complete tab is
now canvas-backed with a standard vertical scrollbar.

### Resumed Florence ETA counted reused work as new throughput

Stored Florence results were skipped correctly, but the progress denominator
and early throughput sample could make a resumed run look faster than its
remaining inference workload. The Florence stage now counts actual remaining
model work and waits for fresh completions before estimating time.

### Selected-image file actions were difficult to find

Quarantine and Recycle Bin support existed in the browser implementation but
was not exposed in the menu where users expected browser-only operations.
Quarantine, restore, Recycle Bin, and complete catalog-record removal now live
under **Browser > Selected Images**, with documented shortcuts.

### Intentional deletion left awkward missing records without a cleanup path

Recycle Bin deletion remains conservative and keeps catalog metadata by
default. A new setting may remove the complete record after successful file
deletion, and an independent browser command can remove selected catalog
records without touching files. Every removal writes a fresh database backup
first. The **No image file found** filter exposes retained missing records.

### Video frames could not be traced back to a useful scene

Successful extraction now writes a sidecar origin manifest; import stores its
source-video and timestamp fields in schema 12. Browser details display the
origin so a useful frame can lead back to the relevant clip location.

## Fixed in 0.27.1

### Filters was not physically adjacent to Sort

v0.27.0 documentation described the intended grouped layout, but the controls
still occupied separate toolbar grid cells. They now share one packed
Sort/Filters container, so their relationship remains visible across window
sizes.

### Clear Filters still required Apply

Clear reset the dialog widgets but did not publish the cleared state until
Apply was clicked. Clear now uses the normal validation/apply boundary
immediately, closes the dialog, refreshes results, and records one reversible
filter-state transition.

### Browser filter changes were absent from Undo/Redo

The shared chronological history handled selection and durable catalog edits
but not session-only visibility changes. Applying and clearing filters now
store complete before/after filter states; Undo and Redo restore image-set,
face, body, catalog-state, readiness, and interpretation settings together
without altering the catalog.

### Provider cards did not explain completed catalog coverage

Run All reports described only the most recent session, while separate provider
tables could contain reusable results from earlier or cancelled runs. Each
provider card now queries the active catalog and reports checked/total,
successful, error-only, and remaining images. Florence additionally reports
successful full-triage coverage.

### Input/output folder persistence depended on unrelated save boundaries

Folder values were persisted by Browse and application close, but typed edits
could remain only in the widget until another settings action occurred. Both
fields now save non-empty working values on focus loss, and the remember-paths
choice has moved to Settings > Paths where its effect is explicit.

### Extraction progress used the configured cap as a moving-looking total

At a 20,000-frame cap, a complete 16,952-frame movie run could not show its
likely final size in advance. Fixed-interval mode now reads duration with the
matching FFprobe executable and shows a complete-video estimate before
extraction. Scene-change mode explicitly says that its total is unavailable.

## Fixed in 0.27.0

### Check Body Analysis Setup appeared to ignore the click

The compatibility command performed native MediaPipe model initialization
before any result dialog was visible. On Windows this created a noticeable
silent delay even though the check eventually succeeded.

The command now maps a checking dialog first, runs the read-only package/model
check on a daemon worker, and polls the result on Tk's owning thread. Starting
a body-analysis run uses the same responsive setup boundary.

### Body filters were difficult to discover and could not be combined

Body, face, identity, review, OCR, and file-state choices shared one mixed
catalog-state dropdown. A user could choose only one, and the newly added body
filters were easy to overlook.

Filters now has explicit Face and Body / Pose sections plus separate Scope,
Quality, and Readiness sections. Face, body/pose, and general states compose
without duplicating the underlying filter engine.

### Curation controls permanently consumed browser width

The attached sliding pane was useful when the option set was small, but it
competed with thumbnails and image details as the application grew. Curation
now opens as a focused Selection action, keeps its session choices, and retains
the same preview-before-deselection safety boundary.

## Fixed in 0.26.0

### Browser pruning could not quarantine, restore, or safely delete source files

The browser could change selection and review metadata but had no physical-file
action matching the real pruning workflow. Users had to leave the application,
manually move files, and risk leaving catalog paths stale.

Quarantine now moves every present physical location represented by the
selected catalog images into an operation-specific folder, records original
and target paths, and restores without overwriting. Delete uses the operating
system Recycle Bin and stops if native trash support is unavailable.

### Import and browser filters could not identify full-body candidates

Existing face/person metadata did not distinguish complete figures from bust
shots or partial bodies. Optional MediaPipe Pose Landmarker analysis now stores
normalized evidence and supports opt-in import exclusion plus body/full-body/
partial-body/visible-face browser filters. Thresholds remain adjustable because
these classifications are estimates.

## Fixed in 0.25.3

### GUI smoke test passed but printed invalid Tk `after` command errors

The Windows GUI smoke test could print messages such as
`invalid command name "..._layout_cards"` after the v0.25.2 pass message.
The application behavior under test had succeeded, but Catalog Browser still
owned queued Tk `after()` callbacks when the smoke test destroyed the root
window.

#### Resolution

Catalog Browser shutdown now cancels all browser-owned scheduled callbacks
before Tk is destroyed: search debounce, card layout, load-more probing, and
thumbnail-result polling. Callback bodies also check the browser shutdown flag
before touching widgets, so a late event cannot run against destroyed Tk
commands.

## Fixed in 0.25.2

### Readiness pruning required repeated tab switching

Readiness issue buttons could send one condition to Catalog Browser, and
selection persisted across tabs, but the browser could not compose an image-set
scope with several readiness findings. Real workflow testing therefore required
moving between Analyze, Catalog Browser, and Finalize & Export while mentally
tracking a progressively pruned set.

Catalog Browser now has one filter dialog for image set, catalog state, and all
eleven readiness checks. Any/All matching supports a complete unresolved-issue
view or deliberate intersections, while Finalize & Export remains the final
summary and export boundary.

### Subject Threshold numeric fields were cramped and unclear

Separate help icons beside both Subject Threshold labels consumed the narrow
curation panel and could leave too little visible entry space. The small-face
range also looked like an unexplained decimal rather than a percentage.

The panel is wider, both Spinboxes have stable widths, and one help icon beside
the group heading explains both values—including that `0.25` means `0.25%` of
the complete image.

## Fixed in 0.25.1

### Holding Alt while pressing browser page arrows could still rubber-band

The v0.24.0 repair consumed repeated and boundary Alt+Left/Right arrow events,
and v0.25.0 additionally consumed the final Alt release. Live Windows testing
showed that the page could snap back whether Alt remained held or was released
immediately. That ruled out release as the primary cause: the native menu path
was claiming the modifier earlier in the chord.

Catalog Browser now claims Alt at key-down on Windows and routes Alt navigation
through a first-priority Tk bind tag before focused-widget, widget-class,
toplevel, and global bindings. Separate arrow press/release state accepts fast
intentional Right/Left strokes while suppressing operating-system key repeat.
The full owned chord is cleared on Alt release.

## Fixed in 0.25.0

### InsightFace model selection accepted ambiguous typed paths

The editable model-pack field represented an InsightFace pack name, while the
separate Model home field represented its root. A user could type path-like
text that did not match InsightFace's `<root>/models/<name>` contract.

The model-pack row now has a Browse button. It accepts only a pack directory
immediately beneath a `models` folder, requires at least one ONNX file, derives
the correct root and pack name, and updates both fields together. Typed pack
names are restricted to one safe path component.

## Fixed in 0.24.3

### v0.24.x GUI smoke test failed after hover help had already passed

The live Windows test successfully generated an Enter event, displayed the
expected tooltip text, and dismissed it on Leave. It then called
`focus_set()` on the Canvas help icon and assumed Windows/Tk would grant
keyboard focus synchronously. That platform-sensitive assumption failed even
though the user-visible mouse-over behavior worked.

Circled question-mark help icons are now explicitly hover-only. They are not
buttons, do not add keyboard-tab stops, and ignore clicks. The smoke test checks
the supported hover interaction and no longer treats programmatic Canvas focus
as a release requirement.

### Smoke tests bypassed the callback-owning application close path

The v0.23.0 and v0.24.x smoke tests manually shut down child panels and then
destroyed the Tk root. That bypassed `DatasetToolsApp._finish_close()`, which
owns cancellation of the menu-refresh and message-queue callbacks. The
application shutdown implementation was correct, but the tests did not use it.
Both smoke tests now close through the normal application path before their
temporary Tk interpreter is destroyed.

## Fixed in 0.24.1

### Circled question-mark icons did not display help

The tooltip attached its hover and keyboard-focus event handlers when each help
icon was constructed. The icon then attached visual hover/focus handlers to the
same Tk widget without additive binding, silently replacing the tooltip
handlers. The question mark changed appearance but could never open its help
popup. Clicking was not implemented as a help action.

## Fixed in 0.24.2

### Smoke test printed Tk callback warnings after passing

The v0.24.1 GUI smoke test completed successfully, but Tk printed
``invalid command name`` warnings for delayed callbacks that were still queued
while the test window was being destroyed. The affected callbacks were the main
message queue poller, coalesced menu refresh, and browser thumbnail-result
poller. They are now tracked and cancelled during shutdown.

### Help icons were unnecessarily clickable

The v0.24.1 hotfix made circled question-mark icons clickable so they could pin
their help text. User testing found this extra interaction unnecessary for the
current UI. Help icons now behave as simple hover/focus affordances, with longer
explanations reserved for the Help menu.

Help-icon visual handlers are now additive. Hover and keyboard focus display
the concise tip without replacing the tooltip handlers. The Windows GUI smoke
test now sends real hover/focus events, confirms click does not pin help, and
asserts that a popup containing the expected text actually becomes visible.

## Fixed in 0.24.0

### Holding Alt during page navigation could rubber-band the browser

Repeated or final-page `Alt+Right` events were returned to Tk when no additional
page action was available. On Windows, the native menu system could interpret
the still-held Alt modifier and make navigation appear to jump back. Browser
page shortcuts now consume boundary events and debounce held-key repeats while
the browser owns focus.

### Video extraction dialog paused before appearing

The dialog synchronously discovered and version-probed FFmpeg before building
its controls. A slow executable startup therefore looked like the click had
been ignored. The window now constructs first, displays a Checking FFmpeg
state, and performs read-only discovery in a background worker.

### Recently visited thumbnail pages visibly loaded again

The disk WebP preview cache avoided regenerating thumbnails, but each page
rebuild still reopened those files and recreated every Tk image object. A
bounded 320-entry least-recently-used decoded-image cache now keeps the current
and recently visited pages warm. File metadata participates in the key so
changed cache files cannot reuse stale pixels.

## Fixed in 0.23.0

### v0.23.0 GUI smoke test failed while applying a theme after catalog loading

The browser's initial empty-state label is a child of the thumbnail container.
Loading a populated catalog correctly destroys that label while rebuilding the
container, but the browser retained the Python reference. The smoke test then
selected Dark Workstation and attempted to configure the destroyed Tk command,
raising `_tkinter.TclError: invalid command name ...label`.

The empty-state reference is now explicitly optional, cleared whenever page
widgets are destroyed, and restored only when an empty result actually creates
a new label. Theme repainting also verifies that the referenced widget still
exists before configuring it. Both the Windows GUI smoke test and a
dependency-light lifecycle regression cover the repaired sequence.

### Thumbnail database stopped loading on the first browser card

The v0.23.0 font compatibility repair correctly introduced root-owned
`tkinter.font.Font` objects, but `ThumbnailCard` passed its plain Python
controller object to the font helper. The controller does not implement
Tkinter's private `._root()` method, so opening an existing catalog stopped
while the first card was created with
`AttributeError: 'ThumbnailCard' object has no attribute '_root'`.

Thumbnail cards now resolve every font through their owned Tk frame. A
dependency-light source regression rejects any future
`get_ui_font(self, ...)` call inside the non-widget `ThumbnailCard` controller,
while the Windows GUI smoke test continues to exercise creation of a full
100-card browser page. The font helper also accepts a controller whose `outer`
attribute is a real Tk widget. This defensive compatibility path prevents the
same crash when Windows extraction leaves an older `catalog_browser.py` beside
the repaired `ui_fonts.py`.

### Application and GUI smoke test crashed while constructing the first label

Python 3.14/Tk on the user's Windows installation parsed both inline tuples
such as `("Segoe UI", 20, "bold")` and attempted brace-delimited description
strings incorrectly, treating `UI` as the numeric font size. The application
therefore stopped during startup before the theme picker or browser could be
tested.

v0.23.0 now creates real `tkinter.font.Font` objects with separate `family`,
`size`, and `weight` arguments. A root-owned cache keeps the objects alive and
reuses them across application tabs, browser cards, canvas text, text tags, and
dialogs. The dependency-light release test rejects literal widget font
descriptions so this compatibility failure cannot silently return.

## Fixed in 0.22.0

### v0.22.0 GUI smoke test failed while inspecting the menu bar

The root menu inherited Tk's historical tear-off entry. On Python/Tk 3.14, the
smoke test attempted to request a `label` from that special entry and stopped
with `_tkinter.TclError: unknown option "-label"` before reaching the actual UI
assertions. v0.22.0 now disables the unused top-level tear-off entry explicitly,
and the test safely ignores any synthetic unlabeled menu entries exposed by a
Tk implementation.

### Ctrl+Z and Ctrl+Y ignored selection actions and jumped to page one

Selection-only curation used a separate button history while keyboard Undo/Redo
always targeted durable catalog edits. Undoing such an edit refreshed the
browser, which also moved the canvas to the top. v0.22.0 records selection and
catalog actions in one chronological browser history. Ctrl+Z/Ctrl+Y now reverse
the action the user actually performed most recently.

### Select Visible crossed browser page boundaries

The label implied current-page scope, but the implementation selected every
search result, including results on other bounded pages. v0.22.0 names the
operation Select Current Page, assigns it Ctrl+A outside text fields, and
provides a separate Select All Results command.

### Browser command buttons obscured the main review workflow

Refresh, image sets, saved-search actions, history, selection operations,
export, curation visibility, and separate selection Undo/Redo controls competed
for toolbar and footer space. v0.22.0 moves secondary commands into mode-aware
menus and shortcuts. Curation uses a thin attached pane marker rather than a
toolbar or Hide Panel button.

## Fixed in 0.21.0

### Thumbnail cards were cut off after deep scrolling

v0.20.0 bounded the initial work but continued appending cards to the same Tk
canvas. Large catalogs could therefore push embedded widgets beyond the
coordinate range rendered reliably on Windows. v0.21.0 keeps each ordinary
thumbnail canvas to one bounded page and explicitly replaces page widgets when
the user advances. Selection remains catalog-wide and survives page changes.

### Progress appeared to restart when Florence began

The same progress bar displayed `0–100%` separately for Cataloging and Florence
analysis. v0.21.0 maps every provider callback into one monotonic,
workload-weighted workflow bar, promotes major phase transitions above the bar,
and adds measured elapsed time and ETA.

### Remove Unnecessary Images treated every multi-person image alike

The earlier fixed policy removed any image reporting several people or faces,
even when one face was clearly dominant and others were background figures.
v0.21.0 exposes curation checks individually and uses the largest and
second-largest stored face bounding boxes for the default prominence rule. A
strict any-multiple-person rule remains available only when explicitly checked.

### Transient selection changes had no direct undo

Durable tag/review edits already had Ctrl+Z/Ctrl+Y history, but selection-only
curation did not. v0.21.0 adds a separate 30-step session selection history with
visible Undo Selection and Redo Selection controls. v0.22.0 supersedes those
separate controls with one chronological Ctrl+Z/Ctrl+Y history.

## Fixed in 0.20.0

### Browser preview cache recursively became provider input

The v0.19.0 browser stored generated WebP previews in
`<catalog folder>\thumbnail_cache`. When the catalog folder was also the
selected recursive source folder, a provider run cataloged those previews and
the browser then generated previews of the previews. A 768-frame QA source grew
to 1,535 cataloged files and later 2,302 files on disk.

v0.20.0 stores new previews under the user's LoRA Image Curator application-data
directory, excludes the exact legacy preview signature from every image
discovery path, and migrates schema-9 catalogs by removing only those
catalog-owned file/image records. It does not delete the legacy cache folder or
any source image from disk.

### Optional post-video provider run felt unsolicited

The extraction dialog's provider checkbox and report did request the run, but
closing a long report immediately launched expensive provider work without one
last workload-specific boundary. v0.20.0 asks again after the report and shows
the actual source-file/new-image counts before starting.

### Provider progress said Reporting during model inference

The progress phase was emitted only after each complete Florence inference and
CSV row, so **Reporting** made slow model work look like a slow summary step.
The phase now says **Florence analysis**, while model-loading details remain in
the status line/log.

### Large catalogs scheduled every browser preview immediately

The browser previously created every Tk card and queued every image conversion
when the tab opened. It now materializes 96 cards at a time, extends the grid as
the user reaches its end, applies completed previews in bounded GUI batches,
and uses faster disposable WebP encoding.

### Ordinary search matched shared video filenames

Unqualified search included filename and path, causing a subject name embedded
in every extracted-frame filename to return the entire catalog. Ordinary terms
now search tags and the Trigger Keyword only. The explicit `filename:` parser
remains for compatibility with an older deliberately saved query.

## Fixed in 0.19.0

### v0.18.0 GUI smoke test reported unclosed SQLite connections

The leak was in legacy `CatalogEditService` methods that treated SQLite
connection context managers as closing boundaries. Those context managers
commit or roll back but do not close the connection. Keyword/review/identity
edits, history reads, and both backup connections now use explicit closing
boundaries. The 9B regression exercises those paths and checks for SQLite
ResourceWarnings under Python development mode.

## Fixed in 0.18.0

### v0.17.0 GUI smoke test queried ttk button state incorrectly

The test used `dialog.start_button.cget("state") == "normal"` for a themed
Tkinter button. On Windows this could report a compatibility option that did
not match the actual enabled/disabled state. The smoke test now uses
`dialog.start_button.instate(("!disabled",))`, which is the ttk API intended
for state checks.

## Fixed in 0.6.2

### Filtered search results left empty grid positions

The browser now destroys and recreates visible cards, then assigns contiguous
row/column positions from index zero after every search, filter, or sort change.

## Fixed in 0.7.0

### Empty search/filter results displayed a clipped message

Grid column weights from the previous thumbnail layout survived after cards were
destroyed. The empty-state label could therefore inherit one narrow card column
and appear truncated. Milestone 7A resets the old grid columns before showing a
full-width empty-state message.


## Fixed in 0.8.0

### Batch editing could not be performed from the existing selection

Milestone 7B adds previewable, transaction-safe batch edits and durable undo.
This was a planned limitation rather than a database defect, but it is now
removed from the active limitation list.

## Fixed in 0.8.1

### Details pane showed one image while several images were selected

The inspector now replaces the single-image preview with an aggregate selection
summary whenever more than one thumbnail is selected. The same review controls
operate on the full selection, and Open Image is disabled until exactly one
image is selected.

### Batch workflow required a separate modal dialog

Single-image and multi-image review now share the details pane. The separate
Batch Edit and visible Undo buttons were removed in favor of direct selection
editing plus Ctrl+Z/Ctrl+Y history.

## Fixed in 0.9.0

### v0.8.2 GUI smoke test ended with WinError 32 on Windows

The tag-interface assertions completed successfully, but the test used
`with sqlite3.connect(...)` as though the context manager closed the connection.
It commits or rolls back without guaranteeing immediate closure, so Windows kept
the temporary `dataset_tools.db` locked when `TemporaryDirectory` tried to remove
it. The test now tears down Tk first and wraps SQLite with
`contextlib.closing`, allowing the temporary catalog to be deleted cleanly.

## Fixed in 0.12.0

### Readiness interpretation controls rebuilt unrelated sections

Changing Blur or Similarity rebuilt Dataset Composition and Common Vocabulary,
creating distracting full-page movement even though those values had not
changed. Blur now updates only its issue row and dependent overall score.
Similarity changes its descriptive cue during a drag, then updates only Possible
Duplicates after release.

### GUI smoke test logged a false thumbnail error

The earlier GUI fixture wrote arbitrary bytes with a `.jpg` filename, so the
thumbnail worker correctly reported that Pillow could not identify the image
even though the GUI assertions passed. The v0.12.0 smoke test creates valid JPEG
fixtures; future thumbnail tracebacks therefore carry useful diagnostic value.

## Fixed in 0.15.0

### v0.14.0 GUI smoke test ended with WinError 32 on dataset_tools.log

The application log remained open while Windows attempted to remove the smoke
test's temporary AppData folder. The v0.15.0 test explicitly shuts down logging
after destroying Tk, including its assertion-failure cleanup path. Normal
application close also flushes and closes logging.

### Pillow warned that Image.getdata() was deprecated

The 64-bit difference-hash implementation now reads its guaranteed 8-bit
grayscale sample through `tobytes()`. This produces the same flat pixel values
without relying on the API scheduled for removal in Pillow 14.

## Fixed in 0.17.0

### Provider analysis could diverge from a custom-named active catalog

The provider pipeline derives `dataset_tools.db` from the selected output
folder. If a differently named catalog was active, starting providers could
write to a second database in the same folder while the browser continued to
show the first one. LoRA Image Curator now detects the mismatch before provider work
begins and explains how to use the standard catalog filename. The Video Sources
dialog applies the same guard before offering its automatic post-import provider
run; extraction and metadata-only import still support another `.db` name.
