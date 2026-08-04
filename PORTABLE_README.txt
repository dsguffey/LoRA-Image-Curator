LORA IMAGE CURATOR — PORTABLE SOURCE PACKAGE
============================================

This is the slim end-user source distribution of LoRA Image Curator. It is not
the full GitHub source archive and it is not yet a self-contained executable.
Python, a virtual environment, provider models, and FFmpeg are not bundled.


FIRST-TIME SETUP
----------------

1. Extract the complete ZIP into a new ordinary folder. Do not run it from
   inside the ZIP preview.
2. Install 64-bit Python 3.11 or newer for Windows if it is not already
   available. Enable the Python launcher or Add Python to PATH.
3. Double-click "Setup and Launch LoRA Image Curator.bat".
4. Choose "1. First-time setup (recommended)" and follow the prompts.

Setup creates a private `venv` beside the application files. You do not need to
activate it manually. Required packages and optional providers are shown as
separate choices. PyTorch selection stays explicit so an NVIDIA computer is not
silently configured for slow CPU-only analysis.

Large model downloads are not started merely by opening the application or
checking setup. Florence, InsightFace buffalo_l, and the recommended MediaPipe
Pose model each require a confirmation that identifies the component, source,
approximate size, destination, and relevant terms before downloading.

FFmpeg is optional and user-installed. LoRA Image Curator never downloads an
FFmpeg executable. Select an existing ffmpeg.exe under Settings > Video
Extraction if you want to extract frames from video.


NORMAL USE AND REPAIR
---------------------

- Double-click "Run LoRA Image Curator.bat" for ordinary use. It checks the
  managed environment and displays each startup stage before opening the app.
- Use "Run LoRA Image Curator - Diagnostic.bat" when troubleshooting. It runs
  the app directly and leaves the result visible after the GUI closes.
- Use "Setup and Launch LoRA Image Curator.bat" again for status checks,
  dependency repair, PyTorch choices, optional Face or Body/Pose setup, FFmpeg
  detection, and launch.
- Tools > Open Setup & Repair inside the app closes the GUI before opening that
  same setup assistant so package changes cannot conflict with a running app.


UPGRADING THIS FOLDER
---------------------

Extract a newer compatible Portable Source ZIP over this folder and allow
release files to be replaced. Do not delete the folder first. The release does
not include or overwrite the adjacent `venv`, catalogs, settings, models,
caches, logs, datasets, source images, video frames, or exports.

Back up important catalogs and datasets as you would any valuable local data.
Never replace a working installation with files from an older release.


PRIVACY, NETWORK, AND THIRD-PARTY TERMS
---------------------------------------

Ordinary cataloging, local analysis, review, and export do not upload images,
catalogs, embeddings, or results. LoRA Image Curator does not collect telemetry
data. Explicit setup and model-download actions contact the named publisher
hosts; third-party tools remain subject to their own terms.

InsightFace pretrained model packs such as buffalo_l are restricted to
non-commercial research use unless separately licensed. They are not bundled.

See these included files before distributing or using optional components:

- LICENSE
- MODEL_LICENSES.txt
- THIRD_PARTY_NOTICE.md
- provider_registry.json
- SBOM.spdx.json

RELEASE_MANIFEST.sha256 records the exact files delivered in this package.


SUPPORT BOUNDARY
----------------

This pre-1.0 application is provided without warranty under the MIT License.
The complete development source, tests, release tools, contributor guidance,
architecture documentation, changelog, roadmap, and issue templates remain in
the GitHub repository and are intentionally omitted from this end-user ZIP.
