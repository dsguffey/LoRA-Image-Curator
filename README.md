# LoRA Image Curator

LoRA Image Curator is a local-first Windows application for cataloging, reviewing, documenting, and exporting image datasets for LoRA training. It does not train a LoRA itself.

## Recommended installation

Use **LIC Install Manager** from the [official GitHub Releases page](https://github.com/dsguffey/LoRA-Image-Curator/releases). It is the recommended Windows setup path and does not require a system Python installation.

**LIC Install Manager 0.7.0** includes LIC Install Manager, LoRA Image Curator, a private CPython runtime, and the verified artifacts needed for LIC Core. Florence captioning, MediaPipe body/pose analysis, Face Analysis, and FFmpeg remain optional, explicit choices.

### Download and Core setup

| Item | Size | What it means |
| --- | ---: | --- |
| LIC Install Manager 0.7.0 package | 72.4 MB (69.1 MiB) | The complete release ZIP download. |
| Bundled Core artifacts | About 59.3 MB (56.6 MiB) | The private CPython runtime plus seven exact Core dependency wheels. |
| Additional dependency download for Core setup | 0 B | Core uses the artifacts already included in the release ZIP. |

The manager shows the size of each optional provider before its download. It does not download an optional provider merely by opening, browsing, or inspecting the manager. Historical Portable packages are retired and are not a supported current installation route. Please [file an issue](https://github.com/dsguffey/LoRA-Image-Curator/issues) for installation problems or feedback.

The manager does not collect telemetry. It downloads only an explicitly requested missing component. LIC can operate offline after installation. Windows may identify an unsigned first release as from an unknown publisher; download only from this project’s official GitHub Releases page and keep Windows security protections enabled.

## Source and developer use

This repository also contains the LIC application source, tests, and developer documentation. Manual Python setup remains available for contributors, but ordinary users should start with LIC Install Manager.

## License

Project source is licensed under the [MIT License](LICENSE). Third-party packages, models, and tools retain their own licenses and terms.
