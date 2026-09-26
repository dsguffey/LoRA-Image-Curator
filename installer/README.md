# LIC Install Manager

LIC Install Manager is the recommended Windows installation and maintenance tool for [LoRA Image Curator](../README.md). It is Windows-only at present, creates an app-local Python environment, verifies approved artifacts, and lets users choose Core and optional providers separately.

## Release build

The 0.7.2 release candidate uses **LIC Install Manager** as its normal product name and **LoRA Image Curator Install Manager** as its expanded name. The dependency profile is `2026-09-26.1`.

The 0.7.2 package contains LIC, CPython, and the exact Core dependency artifacts. It can prepare LIC Core without downloading those Core artifacts. Optional providers remain opt-in and are never downloaded merely by opening, importing, or inspecting the manager. Import copies exact compatible local resources into the selected LIC root's managed `Data` library; Install downloads only what is missing, and Pause preserves work for Resume. The Manager remembers the last successfully validated LIC location and rechecks it before enabling installation actions.

Do not treat historical Portable packages as a supported current installation route.

## Source snapshot

This directory is a sanitized current source snapshot. It contains no private Git history, user catalogs, settings, models, caches, runtime environments, build outputs, or release artifacts. It is governed by the repository root [MIT License](../LICENSE).

For a normal installation, download a verified release package from the [official GitHub Releases page](https://github.com/dsguffey/LoRA-Image-Curator/releases). Windows may show an unknown-publisher notice until code signing is introduced; download only from the official project release page and keep Windows security protections enabled.

Developers can use this source snapshot to build or test the manager. Its layout separates `src/` application code, `tests/`, `tools/`, approved `recipes/` and `profiles/`, and third-party notice records. Report bugs or feedback through the [project issue tracker](https://github.com/dsguffey/LoRA-Image-Curator/issues).
