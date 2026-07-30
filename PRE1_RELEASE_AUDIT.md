# v0.25.0 Pre-1.0 Readiness Audit

## Scope

This audit covers the final pre-1.0 source, branding, performance, security
boundaries, code documentation, repository hygiene, and release process. It is
a bounded engineering review, not a penetration test, legal opinion, or claim
that every optional model/runtime combination has been exercised.

## Performance findings

- The bounded 100-image page model remains below the known Tk canvas clipping
  boundary.
- Disk WebP previews remain disposable and outside source folders.
- The decoded thumbnail least-recently-used cache remains bounded at 320 items.
- Repeated pages reuse decoded previews; the improvement was confirmed in live
  Windows use.
- Five browser projections of the real 767-image schema-10 catalog completed in
  approximately 0.035–0.037 seconds each in the release environment.
- No additional database-query optimization is justified at the current target
  size; thumbnail decoding/widget construction remains the meaningful UI cost,
  and both are already bounded.

## Security and privacy findings

- No dynamic `eval`/`exec`, `os.system`, or subprocess `shell=True` call exists.
- FFmpeg validation and extraction continue to use argument arrays.
- InsightFace pack names are constrained to one safe path component.
- Browsed InsightFace packs must use `<root>/models/<pack>` and contain ONNX
  files before the settings are accepted.
- Catalog replacement/deletion remains gated by application identity and
  required-table validation.
- Old and new catalog identity markers are accepted explicitly; unrelated
  SQLite files remain rejected.
- Release tooling rejects databases, logs, model weights, archives, path
  traversal, duplicate ZIP members, and environment-specific private paths.
- No images, catalogs, identity embeddings, model files, settings, caches,
  credentials, or private machine paths are included.

## Documentation findings

- All 37 production/tooling Python modules have module-level contracts.
- All 213 public top-level functions/classes are documented.
- Architecture documentation records module ownership, durable state,
  concurrency, provider separation, and safety invariants.
- The public README addresses the user problem, features, architecture,
  requirements, testing, privacy, author, LinkedIn contact, licensing, and
  AI-assisted development.
- Code documentation emphasizes intent, assumptions, constraints, and
  non-obvious behavior instead of narrating syntax.

## Repository and licensing findings

- MIT license added for application source.
- Third-party models, dependencies, and FFmpeg retain separate license
  boundaries in `MODEL_LICENSES.txt`.
- `.gitignore` excludes runtime, private, generated, and heavyweight artifacts.
- Reproducible build tooling creates stable ZIP metadata and a SHA-256 member
  manifest, then verifies CRC, member uniqueness, required files, and exclusions.
- Source, tests, documentation, and release scripts remain available for a
  future GitHub repository.

## Data compatibility

- Schema remains version 10.
- New catalogs use the LoRA Image Curator application marker.
- Historical catalogs carrying the Dataset Tools marker remain openable.
- v0.25.0 starts a clean `%APPDATA%\LoRAImageCurator` settings/cache home.

## Remaining release gates

- Run `test_v0250_gui.py` on the user's Windows desktop.
- Reproduce hold Alt → Right → Right → Right → release Alt in the real browser.
- Exercise InsightFace Browse against the installed pack on the user's system.
- Complete one real source-to-export workflow and use that export for a first
  LoRA training trial.
- Resolve only release-blocking findings before creating v1.0.0-rc1.

