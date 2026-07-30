# LoRA Image Curator Design Philosophy

## 1. The primary user comes first

LoRA Image Curator exists to solve the workflow of the person building and using it.
It must please that user before it tries to appeal to hypothetical customers or
a broad market.

A feature that benefits an imagined future audience but makes the primary
workflow slower, less clear, or less enjoyable is the wrong feature.

Other people being able to use the application is welcome. It is not the first
priority.

## 2. Generality comes from architecture, not compromise

The internals should remain modular and extensible, but the interface does not
need to become vague or generic merely to accommodate every theoretical use.

Good architecture should allow future providers, datasets, and workflows
without weakening the current experience.

Build infrastructure so likely future use cases can be added without painful
rewrites, but make the visible UI describe the functionality that exists today.
A control should not sound broader, smarter, or more general than the behavior
it currently provides.

## 3. Expose concepts, not implementations

The interface should describe what a control means to the user.

Prefer:

- Trigger Keyword
- Caption model
- Reference set
- Suggested identity

Avoid exposing internal names such as database table names, provider class
names, or raw model identifiers unless the information is useful in an
advanced or diagnostic view.

## 4. Data is permanent; analysis is replaceable

Images, tags, review decisions, reference sets, and manually entered metadata
are valuable.

Provider results can be regenerated when models improve. User work should
survive application upgrades, model changes, and provider replacement.

## 5. Prefer modularity

Analysis providers should be replaceable. The catalog, browser, review tools,
and export workflows should not be coupled to one caption model or one face
recognition package.

## 6. Hide complexity without hiding control

The normal workflow should be understandable immediately. Advanced controls
may exist, but they should not crowd the primary interface.

Power should be available without requiring the user to understand every
implementation detail.

## 7. Readability and reliability beat cleverness

Maintainable, well-documented code is preferred over compressed or ingenious
code. Important assumptions, interfaces, and design decisions should be
recorded so both people and AI tools can safely modify the project later.

## 8. Avoid feature creep

A feature should either:

- improve an existing workflow, or
- enable a meaningful new workflow.

Otherwise it belongs on the wishlist until real use demonstrates a need.

## 9. Evolve through real use

Build a coherent slice, use it, identify friction, and improve it. Real
interaction with the application should guide UI decisions more strongly than
speculation.

## 10. Do not pretend certainty

AI-assisted features should distinguish suggestions from confirmed facts.
Confidence scores, review states, and clear wording are preferable to absolute
claims the software cannot justify.

Examples include:

- Suggested identity
- Likely screenshot
- Review recommended
- Confidence or similarity scores

## 11. Privacy by default

Analysis should run locally whenever practical.

LoRA Image Curator should not silently upload images, captions, embeddings,
reference sets, identity keywords, or catalog contents. Any future online
feature must be explicit and understandable before it sends data.

Telemetry should not be added by default.

## 12. User activity is ephemeral unless persistence is chosen

Interaction state should disappear when the application closes unless keeping
it is necessary for the requested feature or the user explicitly chooses to
save it.

- partially typed searches are session state
- automatic search history can be enabled, disabled, limited, and cleared
- a named saved search exists only after an explicit Save Search action
- clearing automatic history must not silently delete deliberately named views
- quality-run progress and a one-time reanalyze choice are session state
- cached sharpness and perceptual hashes persist because decoding every image is
  expensive; deleting the owning catalog removes those measurements
- named image sets persist only after an explicit create/add/remove action
- the browser's current selection and Dataset Readiness's active set remain
  session state rather than silently becoming startup preferences

Persistence should be understandable and reversible rather than an accidental
side effect of using the interface.

## 13. The user owns the data

The catalog should remain inspectable and portable.

- SQLite is the durable source of truth.
- CSV and other open exports should remain available.
- Source images are not modified without explicit permission.
- Important formats and schema decisions should be documented.
- The user should not be locked into a proprietary service.
- Catalog creation and deletion should be explicit. Deleting a catalog may
  remove its derived analysis and user metadata, but must not silently delete
  source images or exported datasets.
- Explicit catalog-only record removal creates a current database backup.
  Optional cleanup paired with one reviewed file sent to the operating-system
  Recycle Bin does not create a disproportionate backup; cleanup of several
  records does.

If LoRA Image Curator disappeared, the user's images and metadata should still be
accessible.

## Derived outputs should remain reproducible

A training sidecar, manifest, thumbnail, or exported dataset is an output of the
catalog rather than a replacement for it. LoRA Image Curator should preserve the
provider and user layers that produced an output, record enough context to audit
important exports, and rebuild derived files when profiles or training needs
change. Export history must not be confused with undoable metadata history.

## Copy before destructive file operations

The first implementation of a workflow should prefer copying into a new,
reviewable destination. Moving, deleting, quarantining, transcoding, or
renaming source files requires a separate milestone with stricter confirmation
and recovery rules. Existing destination files should never be overwritten by a
silent default.
## Telemetry and third-party boundaries

- Telemetry and provider diagnostics are disabled by default.
- Every enable attempt must identify the collector, data categories, and
  purpose; consent to one disclosure does not silently authorize a different
  provider or future disclosure.
- Explicit model/dependency downloads are separate user-started network actions,
  not telemetry consent.
- Third-party models, packages, applications, and websites remain outside the
  application's control and responsibility. Compatibility is not a safety,
  provenance, license, or accuracy certification.
