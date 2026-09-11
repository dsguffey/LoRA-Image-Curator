# LoRA Image Curator

LoRA Image Curator (LIC) is a local-first Windows desktop application for turning large image collections into reviewed, documented, training-ready LoRA datasets. It combines cataloging, AI-assisted analysis, visual curation, quality review, and non-destructive export in one workflow.

LIC prepares image datasets; it does **not** train a LoRA itself. Its SQLite catalog keeps image identity, provider evidence, and review decisions durable while source images remain outside destructive application control during analysis and export.

## Project demo

[![Watch the LoRA Image Curator project demo](https://img.youtube.com/vi/YiKs0tyUasY/maxresdefault.jpg)](https://youtu.be/YiKs0tyUasY)

**[Watch the LoRA Image Curator project demo on YouTube](https://youtu.be/YiKs0tyUasY)**

This demonstration shows an earlier LIC version moving from a deliberately messy synthetic portrait collection through analysis, human-guided curation, validation, and export. It is a workflow demonstration, not a claim that every control exactly matches the current UI.

## What LIC can do

- Catalog images from one or more folders, including extracted video frames.
- Review a paged thumbnail catalog, enlarge images, search, filter, tag, and organize selected images into named image sets.
- Measure local quality evidence, including sharpness, perceptual duplicates, and review candidates.
- Generate Florence image captions and optional object/OCR triage evidence.
- Detect faces and compare them with user-supplied reference identities through the optional Face Analysis capability.
- Record optional MediaPipe body and pose evidence for review filters.
- Extract frames from video with optional FFmpeg support, preserving source video provenance.
- Assess dataset-readiness evidence, review the final selection, and export images, sidecars, manifests, and a training handoff without altering sources.

Provider results are evidence for a person to review; they do not replace human curation or guarantee a good trained model.

## Typical workflow

1. Import image folders or extract frames from a video into a catalog.
2. Update the catalog and run the local analyses that are useful for the job.
3. Review, search, compare, group, and curate the images into a named set.
4. Check dataset-readiness evidence for the intended LoRA target.
5. Export the prepared selection and its captions/provenance for training in a separate training tool.

## Installation and downloads

For ordinary Windows use, download **LIC Install Manager** from the [official GitHub Releases page](https://github.com/dsguffey/LoRA-Image-Curator/releases). It does not require a separately installed system Python.

| Component | Required? | Downloaded from | Fresh-install download | What it adds |
| --- | --- | --- | ---: | --- |
| LIC Install Manager + LIC Core | Yes | GitHub Releases | 72.4 MB | A working basic LIC installation, including everything required for Core. |
| Florence captioning | Optional | LIC Install Manager | ~3.5 GB | Local image captions plus optional object and OCR triage. |
| MediaPipe body and pose | Optional | LIC Install Manager | ~87.5 MB | Local body and pose evidence for catalog filters. |
| Face Analysis | Optional | LIC Install Manager | See note | Local face detection and reference-identity comparison with YuNet + SFace. |
| FFmpeg video extraction | Optional | LIC Install Manager | ~146.1 MB | Local video inspection and frame extraction. |

The 72.4 MB release package contains LIC Install Manager, LIC, CPython 3.14.6, and the complete provider-neutral Core environment. After downloading that ZIP, **the Manager needs no additional Core dependency download**. Optional-feature sizes are fresh-install estimates. The Manager shows the current required download before installation, and the amount can be smaller when it verifies reusable existing files or cache entries.

### Install LIC Core

1. Download `LIC-Install-Manager-0.7.0.zip` from the official GitHub Release.
2. Extract the ZIP.
3. Run `LIC Install Manager.exe`.
4. Choose **Install Core**.
5. Add only the optional capabilities you want from the Manager.

LIC Install Manager is not currently code-signed. Windows may show an Unknown Publisher or SmartScreen warning. Download it only from the official project GitHub Release and keep Windows security protections enabled.

## Optional capabilities

Florence is the supported local image-captioning and vision-analysis option. A fresh Florence setup includes the pinned 1.923 GB CUDA PyTorch wheel, a 9.2 MB Torchvision wheel, and the pinned 1.546 GB Florence snapshot, plus smaller pinned runtime wheels. The large PyTorch wheel belongs to Florence's optional dependency closure; it is not part of LIC Core.

MediaPipe's ~87.5 MB fresh-install estimate includes the 53.8 MB approved OpenCV contrib wheel, 10.9 MB MediaPipe wheel, 9.5 MB Matplotlib wheel, 9.4 MB Pose Landmarker task, and smaller locked runtime wheels. Face Analysis downloads the verified YuNet and SFace ONNX pair (38.9 MB), but a clean Core-only installation does not yet have a qualified OpenCV runtime added by the Face plan. Its complete standalone fresh-install total is therefore not established for this release; use it after the approved OpenCV runtime is present, such as through the MediaPipe profile. FFmpeg downloads the qualified 146.1 MB BtbN LGPL archive. See the Manager's Details view before installing any provider for its identity, source, terms, and restrictions.

## Privacy and local-first behavior

- LIC does not upload images, catalogs, captions, embeddings, or identity names.
- LIC and the Manager do not collect telemetry.
- Core can operate offline after installation.
- Optional providers are explicit choices. Opening, browsing, inspecting, or checking the Manager does not silently acquire an optional provider.
- Source images are read during analysis and copied for export; destructive actions require confirmation and use recovery-aware paths.

Third-party providers, models, tools, websites, and their terms remain under their respective owners' control. Read [MODEL_LICENSES.txt](MODEL_LICENSES.txt), [THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md), and [SECURITY.md](SECURITY.md) before enabling optional components.

## Documentation and project status

| Resource | Purpose |
| --- | --- |
| [Detailed user reference](README.txt) | Full application guide and source/developer setup reference. |
| [Architecture](docs/ARCHITECTURE.md) | Module ownership, invariants, and concurrency rules. |
| [Development and release guide](docs/DEVELOPMENT.md) | Contributor setup and focused checks. |
| [Golden test guide](docs/GOLDEN_TEST.md) | Current Windows release-gate scope and limits. |
| [Release verification guide](docs/RELEASE_VERIFICATION.md) | Source manifest and release-boundary checks. |
| [Bugs](BUGS.md) | Confirmed current defects. |
| [Roadmap](ROADMAP.md) | Active planned work and known limits. |
| [Wishlist](WISHLIST.md) | Deferred ideas. |
| [Contributing](CONTRIBUTING.md) | Focused contribution and reporting guidance. |
| [Security](SECURITY.md) | Local-data and third-party boundary policy. |
| [Changelog](CHANGELOG.md) | Completed history. |

LIC is in active pre-1.0 stabilization. Windows 11 is the primary tested platform. Provider evidence is probabilistic and needs review; large-catalog timing is a workstation observation rather than a performance guarantee; and readiness checks evaluate dataset preparation evidence rather than predict final LoRA quality. The first complete exported-dataset training trial remains pre-1.0 work.

## Development and contributing

This repository contains LIC source, tests, tools, and documentation. The Manager supplements that project; it does not replace source/developer setup. Contributors can use the documented source workflow in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Please read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes, and never include private images, catalogs, model files, credentials, or personal paths in a public issue.

## Authorship

Product direction, workflow design, acceptance criteria, hands-on Windows QA, and release decisions are led by **David Scott Guffey**. LIC has been developed iteratively with AI assistance and validated through explicit regressions, catalog-integrity checks, security-boundary reviews, and release verification.

- [David Scott Guffey on LinkedIn](https://www.linkedin.com/in/davidsguffey/)

## License

Project source is licensed under the [MIT License](LICENSE). Third-party packages, models, and separately installed tools retain their own licenses and terms.
