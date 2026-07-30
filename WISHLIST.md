# LoRA Image Curator Wishlist

This file is deliberately separate from the roadmap.

Items here may be valuable someday, but they are not promises, current
milestones, or reasons to delay the main program. An item should move to the
roadmap only after real use shows that it deserves priority.

The guiding rule is:

> If a feature makes the application easier to use without reducing
> flexibility, it is a good candidate. If it adds complexity without clearly
> improving the workflow, it probably does not belong.

## Future analysis providers

- CLIP or SigLIP image embeddings
- semantic search such as “show me images like this one”
- additional captioning models
- side-by-side comparison of caption models
- alternative face-recognition providers, including user-supplied or
  commercially licensed models
- alternative body/pose providers (for example vetted ONNX-based options) after
  the MediaPipe provider contract has been proven in real datasets
- pose clustering and pose-similarity search
- aesthetic scoring
- lighting classification and clustering
- visual-style clustering
- automatic clothing classification
- character-versus-actor recognition assistance
- dataset variety and balance analysis
- dataset planning and curation support for styles, concepts, and multiple
  characters, including workflows where the desired output is not a single
  person-likeness LoRA


## Tag and vocabulary refinements

- synonym and canonical-alias groups, such as `car` and `automobile`
- optional tag renaming across the user layer without rewriting provider output
- tag ordering rules beyond the current deterministic provenance order
- import/export of reusable manual tag vocabularies

## Optional export refinements

- saved named Custom export profiles
- ZIP archive export after directory-based training workflows are proven
- configurable subfolder templates and filename numbering rules
- optional image conversion, resizing, or format normalization
- deterministic shuffle/seed controls for trainers that benefit from numbered data
- checks for trainer-specific folder conventions
- export-history browser and repeat-export action
- comparison of two profiles before export
- direct launch of a locally installed training tool only after the export format
  is stable and the user explicitly configures it

## Optional image-set refinements

- duplicate or fork an existing set under a new name
- optional set descriptions or notes after real use shows they are needed
- compare membership differences between two named sets
- import/export portable membership manifests for catalogs sharing the same
  content hashes
- cross-catalog set transfer with an explicit missing-image report

## Selection workflow refinements

These ideas came from early real-use curation, where selection usually means
"the working set I am building across the whole browser result set" rather than
"only the thumbnails visible on the current page."

- add a right-click thumbnail-browser context menu for non-search selection
  operations
- organize the right-click selection menu into submenus if the operation list
  becomes crowded
- consider context-menu shortcuts for updating the active image set only after
  real use establishes an unambiguous target-set concept
- add a **Filter Images Like This One** menu/keyboard action after the desired
  matching dimensions are defined; keep it separate from the read-only Image
  Quality explanation popup and from future embedding-based visual similarity

## Optional culling refinements

- pre-import screening for very large video-frame collections has moved to the
  roadmap's dedicated stress-test milestone
- saved named culling presets only if repeated use shows that the v0.21.0
  per-run checkbox panel is not sufficient
- richer duplicate ranking from future semantic, pose, lighting, clothing, or
  aesthetic providers while keeping the preview-and-confirm boundary
- body-level person bounding boxes from a suitable local provider so prominence
  can consider full people when faces are turned away, obscured, or absent
- specialized screenshot/profile-card detection beyond Florence's general
  screenshot/UI signal

## Optional catalog-import refinements

- saved import presets only if repeated real-world imports make the extra state useful
- portable import reports or manifests for users who need an external audit trail
- folder-watch or incremental-rescan automation only as an explicit opt-in feature
- richer import previews after the core create/merge/replace workflow has been used
- optional management of duplicate file locations without deleting source files

## Reference sets

- a reusable reference-set library stored as first-class catalog data
- multiple named reference sets per catalog
- comparison against several reference sets in one pass
- manual selection of the correct face inside a reference image
- warnings when a reference set appears to contain multiple identities
- reference-set quality summaries and outlier detection
- import and export of reference sets
- the ability to reuse a reference set across projects without copying source
  images unnecessarily

## Visual exploration

- interactive embedding maps for clusters and outliers
- similarity neighborhoods around a selected image
- pose, lighting, costume, or style cluster views
- cross-catalog statistics

## Optional ecosystem ideas

- a documented plugin SDK after the internal provider interfaces stabilize
- portable data-only provider/model packages with signed or hashed manifests,
  compatibility metadata, provenance, license, hardware, and privacy fields
- an advanced isolated third-party code-provider path only after safe virtual
  environment/subprocess boundaries and strong warnings are designed
- optional cloud synchronization controlled entirely by the user
- project-to-project metadata transfer tools

## Ideas that should remain optional

- automatic update checks
- cloud backup
- anonymous telemetry
- online model services

Any online capability must follow the privacy and data-ownership principles in
`DESIGN_PHILOSOPHY.md`.

## Source-tree and legal maintenance

- reorganize the 100+ source/test files into coherent packages only as a
  dedicated refactor with import migration, full regression, clean-extraction,
  and Windows GUI verification
- review the complete 1.0 user-facing disclaimer/license inventory before
  public release and consider qualified legal review if the project becomes a
  distributed commercial product
- keep provider/model/app responsibility, telemetry, license, and privacy
  disclosures synchronized between Settings, Help, About, and bundled notices

## Late-stage browser polish

These are intentionally postponed until real use shows which refinements matter.

- optional thumbnail-size slider
- true widget recycling/viewport virtualization if catalogs substantially
  larger than the current configurable bounded 100-image paging design make explicit pages
  inconvenient
- user-customizable keyboard shortcuts only if the documented workstation
  defaults prove insufficient in repeated use
- optional user-configurable images-per-page values below 25 only if unusually
  small displays make the current 25/50/75/100 choices inadequate
- advanced/collapsible metadata sections
- denser metadata hierarchy and typography refinements
- customizable status-bar layout
- auto-scroll while drag-selecting near the top or bottom of the grid
- optional Up/Down or Page Up/Page Down keyboard scrolling when thumbnail
  selection focus rules can remain unambiguous
- optional checkbox selection mode
- user-configurable accent colors for manual and AI-generated metadata
- additional thumbnail-card density and spacing controls
- reusable cross-catalog search templates distinct from catalog-local saved searches
- live result counts inside the Advanced Search dialog before Apply
- optional saved-search shortcuts that select all results or open Export Selected
  only after ordinary apply/select/export use demonstrates a real benefit
- user-defined readiness profiles and scoring weights after hands-on training
  experience establishes useful defaults beyond the built-in LoRA targets

## Deferred QA and release-hardening pass

These checks are useful, but most can wait until a final 1.0-style stabilization
phase unless a related workflow is being actively changed.

- broader repeated testing of workflows that passed in prior releases
- extended Windows GUI passes across every tab after UI layout settles
- stress testing large catalogs, very large image sets, and long-running video
  extraction jobs
- systematic testing of cancellation, interrupted runs, and locked files across
  provider analysis, video extraction, catalog import, and export
- change filters, sorting, page size, and first/last/±10 navigation while
  thumbnail placeholders are still resolving; watch for stale or delayed redraws
- exercise quarantine/restore and close-while-provider-busy recovery on a
  disposable real catalog
- low-priority edge cases around unusual filenames, deeply nested paths, and
  uncommon image formats
- visual polish audits for spacing, wording, and control grouping after the main
  workflow stops moving
