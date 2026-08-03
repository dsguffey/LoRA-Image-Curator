# Third-Party Products, Privacy, and Responsibility

LoRA Image Curator is a local-first application distributed under the MIT
License. LoRA Image Curator does not collect telemetry data, but some
third-party tools it's using may. The default settings for them are set to
telemetry off.

## Third-party boundary

LoRA Image Curator does not control third-party models, model weights,
provider packages, applications, websites, services, licenses, terms, privacy
practices, security, availability, outputs, accuracy, compatibility, or future
changes. Those products remain under the control and responsibility of their
respective authors and operators.

Users are responsible for reviewing applicable licenses, terms, privacy
notices, system requirements, and usage restrictions before installing or
using third-party products. A compatibility result means only that an artifact
matched the technical interface tested by that application release. It does
not certify provenance, safety, legality, accuracy, or fitness for a particular
purpose.

LoRA Image Curator is provided without warranty as described in `LICENSE`.
This notice is product information and is not legal advice.

The machine-readable release record is `provider_registry.json`. Its tested
identity is also rendered as the source/provider `SBOM.spdx.json`. A release
registry entry or compatibility result does not grant rights beyond the
component's actual license.

## Telemetry and network behavior

- Provider telemetry permission is disabled by default.
- LoRA Image Curator currently implements no telemetry collector or endpoint.
- The current MediaPipe Pose Landmarker analysis path reads local images and a
  local model; the application does not upload images, landmarks, captions,
  embeddings, hashes, or catalog records.
- Explicit dependency installations and model downloads are separate,
  user-started network operations.
- Every attempt to enable provider telemetry displays the current disclosure.
  A future provider must identify the collector, data categories, and purpose
  before that permission may be used.

## Vetted provider policy

This release supports vetted, data-model-oriented provider paths only. It does
not install arbitrary executable Python provider packages from the application
interface. The included optional installer names its package and model sources,
asks before network operations, and does not enable telemetry.

## Current optional third-party components

- Microsoft Florence-2 Large FT — MIT-licensed model; downloaded separately at
  a pinned revision and executed through native, pinned Transformers code.
- Google MediaPipe Pose Landmarker — Apache 2.0 code and version-1 Full model;
  the installer verifies the publisher-hosted model's exact size and SHA-256
  before publishing it locally.
- Send2Trash — BSD-3-Clause; used only to request native operating-system Trash
  or Recycle Bin behavior.
- InsightFace / ONNX Runtime / Florence-2 / FFmpeg — see
  `MODEL_LICENSES.txt`, their upstream licenses, and their current terms.

## Distribution boundary

The public source archive and future portable Windows archive are separate
products. The source archive includes tests, release tooling, and development
documents. The end-user portable payload will exclude those repository-only
files and will never collect an existing user/developer virtual environment,
catalogs, models, caches, logs, datasets, outputs, or exports. Only components
with an explicit redistribution decision may enter that staged payload.
