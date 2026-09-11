# LoRA Image Curator

LoRA Image Curator is a local-first Windows application for cataloging, reviewing, documenting, and exporting image datasets for LoRA training. It does not train a LoRA itself.

## Recommended installation

Use **LIC Install Manager** from this project’s GitHub Releases page. It is the recommended Windows setup path and does not require a system Python installation.

**LIC Install Manager Lite** includes LoRA Image Curator, a private CPython runtime, and the verified artifacts needed for LIC Core. Core setup can use those bundled artifacts without downloading them. Florence captioning, MediaPipe body/pose analysis, Face Analysis, and FFmpeg remain optional, explicit choices.

A Full offline package is not listed until its single-release-asset distribution decision is complete. Historical Portable packages are retired and are not a supported current installation route.

The manager does not collect telemetry. It downloads only an explicitly requested missing component. LIC can operate offline after installation. Windows may identify an unsigned first release as from an unknown publisher; download only from this project’s official GitHub Releases page and keep Windows security protections enabled.

## Source and developer use

This repository also contains the LIC application source, tests, and developer documentation. Manual Python setup remains available for contributors, but ordinary users should start with LIC Install Manager.

## License

Project source is licensed under the [MIT License](LICENSE). Third-party packages, models, and tools retain their own licenses and terms.
