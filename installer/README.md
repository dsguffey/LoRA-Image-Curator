# LIC Install Manager

LIC Install Manager is the recommended Windows installation and maintenance tool for [LoRA Image Curator](../README.md). It creates an app-local Python environment, verifies approved artifacts, and lets users choose Core and optional providers separately.

## Release build

The public 0.7.0 release uses **LIC Install Manager** as its normal product name and **LoRA Image Curator Install Manager** as its expanded name. The dependency profile is `2026-09-10`.

The Lite package contains LIC, CPython, and the exact Core dependency artifacts. It can prepare LIC Core without downloading those Core artifacts. Optional providers remain opt-in and are never downloaded merely by opening, browsing, or inspecting the manager.

The Full package is not listed here until its single-release-asset distribution decision is resolved. Do not treat historical Portable packages as a supported current installation route.

## Source snapshot

This directory is a sanitized current source snapshot. It contains no private Git history, user catalogs, settings, models, caches, runtime environments, build outputs, or release artifacts. It is governed by the repository root [MIT License](../LICENSE).

For a normal installation, download a verified release package from this project’s GitHub Releases page. Windows may show an unknown-publisher notice until code signing is introduced; download only from the official project release page and keep Windows security protections enabled.
