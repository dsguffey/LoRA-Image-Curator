# Milestone 10 Phase 1 Audit

This audit accompanies LoRA Image Curator v0.20.0. It is a bounded review of the
current application and test tree, not a claim that static inspection can prove
every optional model/runtime combination correct.

## v0.23.0 startup-compatibility addendum

The visual theme pass originally supplied multiword font families through
tuple and description-string forms. Python 3.14/Tk on the user's Windows
installation parsed those forms incompatibly and stopped while constructing
the first label. The corrected release creates structured
`tkinter.font.Font` objects, caches them on the Tk root, and passes those
objects to every explicit widget, canvas, and text-tag font option.

The dependency-light suite now inspects the Abstract Syntax Tree (AST) of every
application module and rejects literal `font=` tuple/string/list arguments. It
also verifies that the registry passes family, size, and weight separately and
reuses cached objects. This compatibility repair changes no schema, catalog,
provider, filesystem, export, image-selection, or network boundary.

## v0.22.0 Phase 1C addendum

The workstation UI pass changes command routing and layout, not catalog,
provider, export, or filesystem ownership:

- browser-only menus are rebuilt from the active notebook tab
- text-capable widgets keep standard text-editing shortcut ownership
- thumbnail-grid shortcuts operate only while Catalog Browser is visible
- current-page and all-results selection scopes are explicit
- selection and durable catalog actions share a bounded chronological UI stack;
  durable edits remain transactionally recorded by CatalogEditService
- the curation edge marker invokes the existing preview-first panel and adds no
  new write path
- images-per-page is clamped to 25–100 and persisted as a local preference

The Phase 1C delta adds no schema migration, network access, subprocess path,
image file operation, catalog-deletion path, or external dependency.

## v0.21.0 Phase 1B addendum

The next real Windows QA pass found that bounded initial loading was not enough:
later batches still accumulated inside one canvas until Tk clipped deep
coordinates. v0.21.0 limits an ordinary canvas to 96 cards and replaces page
widgets explicitly. Selection is stored by catalog image ID and therefore
survives page changes.

The same follow-up reviewed progress and curation boundaries:

- provider callbacks now feed a dependency-light progress tracker whose overall
  percentage never moves backward across phases
- workload weights keep fast Cataloging from visually claiming half of a
  Florence-heavy run
- ETA is based on measured current-phase completions and is withheld until a
  minimum sample exists
- curation switches remain transient UI state and never become hidden catalog
  policy
- selection undo stores only bounded sets of integer image IDs
- face prominence uses two stored bounding-box areas from the newest successful
  face result for each image; it does not infer identity or body size
- the final curation report remains the write boundary, and confirmed curation
  changes selection only

The Phase 1B delta adds no network access, subprocess path, file-deletion path,
catalog schema migration, or external dependency.

## Evidence used

- v0.19.0 release source
- the user's real 768-frame schema-9 QA catalog
- the reported 2,302-file output directory
- self-contained regressions from Milestones 7D through 10 Phase 1
- prior catalog-backed regressions where the supplied catalog contained the
  required feature data
- Python development-mode compilation and ResourceWarning checks
- SQLite integrity and foreign-key verification
- static searches for subprocess, deletion, replacement, dynamic execution,
  broad exception, stale marker, and unused-import patterns

The main source and test tree contains roughly 26,000 lines across 59 Python
files. The largest modules received additional focused review because they own
the highest-risk boundaries: `app.py`, `catalog.py`, `catalog_browser.py`,
`catalog_edits.py`, `dataset_export.py`, `face_analyzer.py`, and
`florence_analyzer.py`.

## Confirmed failure

v0.19.0 stored browser previews under
`<catalog/source folder>\thumbnail_cache`. Because all three recursive image
discovery paths accepted WebP, the optional provider handoff cataloged 767
first-generation previews beside the 768 original frames. The browser then
generated a further preview generation for those new catalog records.

The supplied database proves the sequence:

| Evidence | Value |
|---|---:|
| First import discovered files | 768 |
| First import unique images | 767 |
| Second run catalog file records | 1,535 |
| Second run catalog image records | 1,532 |
| Florence results completed before forced close | 117 |
| Later total files reported on disk | 2,302 |

## Implemented corrections

- one shared image-discovery policy for metadata import, Florence, and face
  analysis
- exact legacy preview-signature exclusion
- previews relocated to per-user LoRA Image Curator application data
- schema 10 catalog-only repair of legacy preview records and orphan images
- abandoned `running` records finalized during schema-9 repair
- tag-only ordinary search; explicit legacy `filename:` syntax retained only
  for saved-query compatibility
- bounded initial browser/card/thumbnail work
- bounded preview delivery to Tk
- faster disposable preview encoding
- clearer Florence progress naming
- explicit post-video provider confirmation with workload counts
- cooperative provider cancellation and close-after-cancel behavior

## Maintainability and performance findings

- Removed unused imports detected by an Abstract Syntax Tree (AST) name-use
  pass.
- Removed an unused list that retained every face-report row in memory after
  each row had already been written.
- Reduced CSV flush frequency from every image to every 25 images. SQLite
  remains the durable result store, and partial CSV output remains recoverable.
- Changed perceptual-hash comparisons to parse each hash once before the
  pairwise loop.
- Kept the existing short-lived SQLite connection pattern; the real 1,532-image
  browser projection completed in approximately 0.05 seconds, so rewriting that
  query would not address the observed bottleneck.
- No unreferenced public top-level function or class was found by the
  whole-tree reference scan.
- Broad exception handlers were reviewed at GUI, per-item provider/export, and
  cleanup boundaries. They are retained where isolating one bad image,
  preserving a primary exception, or showing an actionable GUI error is the
  intended behavior.

## Reasonable-security review

- No `eval`, dynamic `exec`, unsafe deserialization, or shell-interpolated user
  command path is used.
- FFmpeg validation/extraction uses argument arrays and `shell=False`.
- Cross-platform open-folder/open-image helpers use argument arrays; Windows
  uses the operating system's normal file association.
- Catalog replacement is staged and validated before atomic publication.
- Catalog deletion targets only a validated database and its exact SQLite
  sidecars.
- Export writes through collision checks and temporary files; source images
  remain outside the export deletion boundary.
- Schema 10 removes catalog records only. It deliberately does not delete the
  old on-disk cache, even though the signature is known.
- Settings and preview caches contain local workflow state only and are stored
  under the user's application-data directory.
- Provider model-license boundaries remain visible. FFmpeg remains separately
  installed, and InsightFace's default pretrained weights remain identified as
  non-commercial research assets.

No high-severity security defect was found in this pass. This is not a
penetration test or legal license opinion.

## Deferred or environment-specific checks

- Run `tests/test_v0200_gui.py` on the user's Windows installation.
- Confirm the repaired real catalog shows 767 images and loads comfortably.
- Exercise cancellation during real Florence inference and verify resume/reuse.
- Complete a real Finalize & Export pass and inspect sidecars.
- Revisit the known ONNX Runtime CUDA provider problem.
- Test the hidden VBS/pythonw launcher only if it remains useful before the
  executable/installer milestone.
- Perform the final license inventory, MIT application license addition, public
  repository cleanup, and private-path scrub during Milestone 10 Phase 3.
