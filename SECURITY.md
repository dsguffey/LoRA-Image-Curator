# Security Policy

## Supported versions

Security corrections are applied to the newest pre-release or stable version.
The project has not reached v1.0.0; older milestone archives are historical
artifacts rather than supported distributions.

## Security boundary

LoRA Image Curator is a local desktop application, not a network service. It
processes user-selected images, videos, SQLite catalogs, model packs, and an
optional FFmpeg executable. Its principal security goals are:

- do not upload or disclose local dataset content;
- do not interpret user text as code or a shell command;
- do not overwrite or delete source images during analysis or export;
- validate a catalog before replacement or deletion;
- constrain model selection to InsightFace's expected root/models/name layout;
- exclude catalogs, caches, logs, model weights, and private paths from releases.

The application does not attempt to sandbox PyTorch, Transformers,
InsightFace, ONNX Runtime, MediaPipe, or FFmpeg. Install third-party components
only from sources you trust and review their licenses. The Florence path does
not enable `trust_remote_code`: it pins a reviewed Transformers release and
Microsoft model revision, requires safetensors weights, and rejects a model or
processor implementation outside native `transformers.models.florence2` code.

## Reporting a vulnerability

Do not attach private datasets, model files, identity embeddings, catalog
databases, credentials, or logs containing private paths to a public issue.

For a non-sensitive report, open a GitHub issue once the repository is public.
For a potentially sensitive security report, contact
[David Scott Guffey through LinkedIn](https://www.linkedin.com/in/davidsguffey/)
and request a private reporting channel.

Include the affected version, operating system, reproducible behavior, and the
smallest non-sensitive test case possible.
## Source-file actions

Quarantine and native Trash/Recycle Bin actions are the only application paths
that intentionally move/remove source files. Quarantine records original and
target paths and restore refuses to overwrite. Delete uses Send2Trash and never
falls back to permanent deletion. One catalog image may represent multiple
physical locations; the application discloses both counts before acting.

## Optional providers and telemetry

Provider telemetry permission is disabled by default. LoRA Image Curator
currently implements no telemetry collector or endpoint, and the MediaPipe
body-analysis path performs local inference. Explicit dependency/model
downloads are separate user-started network actions. Arbitrary executable
provider packages are blocked in this release.

Compatibility checks do not establish provenance or safety. See
`THIRD_PARTY_NOTICE.md`.
