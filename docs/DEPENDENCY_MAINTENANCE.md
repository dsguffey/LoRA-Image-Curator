# LIC dependency maintenance

The Windows/NVIDIA profile uses Torch 2.13.0+cu130, Torchvision 0.28.0+cu130,
ONNX Runtime GPU 1.28.0, contrib OpenCV 5.0.0.93, MediaPipe 0.10.35 and
Transformers 4.56.2. `lic_dependencies/profile.json` owns the selected runtime
identities; `constraints-lic.txt` fixes the transitive repair baseline and rejects
the conflicting distribution names. `constraints-nvidia.txt` also fixes the CUDA
Torch pair. Requirements/pyproject consistency is tested.

## Why a downstream InsightFace wheel exists

Upstream InsightFace 1.0.1 imports `onnxruntime` and `cv2`, which the selected GPU
ORT and contrib OpenCV wheels supply. Its metadata nevertheless requires the
distinct distributions `onnxruntime` and `opencv-python`. Normal pip resolution
therefore installs overlapping wheels. Install order changes the overwritten
files, not the ownership conflict. `--no-deps` alone leaves `pip check` failing.

LIC maintains an explicitly identified packaging artifact. The only upstream
metadata changes are the distribution Version and these two active Windows/base
requirements:

* `onnxruntime` becomes `onnxruntime-gpu==1.28.0`.
* `opencv-python` becomes `opencv-contrib-python==5.0.0.93`.

Other dependency declarations and markers are preserved. Runtime Python files,
native assets, licenses, entry points and upstream identity are byte-preserved.
The imported upstream `insightface.__version__` remains 1.0.1; installed
distribution metadata identifies `1.0.1+lic.cuda13.1`.

Versioning is `<upstream>+lic.cuda13.<recipe revision>`. Increment the recipe
revision when approved substitutions/profile behavior changes. Never replace an
approved artifact under the same version with different bytes.

## Build and provenance

Use a disposable Python 3.14.6 environment, never the active LIC venv:

```powershell
python -m venv .build-env
.build-env\Scripts\python.exe -m pip install pip==26.2 setuptools==83.0.0 wheel==0.47.0 packaging==26.2
.build-env\Scripts\python.exe -m lic_dependencies.insightface_build --output build/candidate
```

Commands assume the repository root. Choose a disposable directory outside an
existing installation when running the first command. No tool removes an old
environment for you.

The approved 1.0.1 publisher wheel URL and SHA-256 are fixed in the profile. The
builder verifies this external hash, safe archive paths, distribution identity,
every RECORD hash, and the complete member inventory before extraction. It uses
the standard `wheel unpack` / `wheel pack` mechanism in a temporary directory,
not an installed-metadata edit or ad-hoc ZIP rewrite. This preserves upstream
runtime bytes without executing upstream setup.py or source-build hooks. Wheel
pack regenerates RECORD. Fixed SOURCE_DATE_EPOCH makes wheel bytes reproducible
with the pinned toolchain.

The wheel retains the original metadata as `LIC_UPSTREAM_METADATA.txt` and adds
`LIC_PROVENANCE.json` under dist-info. `build-metadata.json` records upstream
version/source/hash, substitutions, downstream version, output wheel/hash,
tool/recipe hashes, Python/tool versions and UTC build time. Time is in the
external report, not nondeterministic wheel contents. Compare output SHA-256s
from two separate output directories to verify reproducibility.

HTTPS acquisition is limited to PyPI and its designated file host, including
redirects. Reads are size/time bounded; metadata uses ETags; requests are serial,
rate-limited, and retry temporary failures with backoff/jitter/Retry-After.
Long Retry-After requests abort for a later run. Cache is confined to the
explicit build output; credentials are neither read nor copied.

## New upstream candidates and retirement

```powershell
.build-env\Scripts\python.exe -m lic_dependencies.insightface_build --latest --output build/latest
.build-env\Scripts\python.exe -m lic_dependencies.insightface_build --version 1.0.1 --output build/specified
```

Discovery uses official versioned PyPI metadata and its artifact hash, records
whether the version is newer than the supported profile, and requires a
non-yanked universal wheel. Different artifact formats, direct-URL dependencies,
new conflicting backend names or incompatible canonical backend requirements
stop for review. No arbitrary upstream build hooks run.

For the Windows base install (no unused extras), inspect Requires-Dist rather
than guessing from release notes. If neither problematic distribution remains,
return the official artifact byte-for-byte and report:

> downstream patch no longer required; candidate official package should be tested directly

This does not assert compatibility or approval. The official candidate still
needs the same installation, inference and human gates. Only after those gates
should a maintainer update the supported source/hash/requirements and retire
the transformation. Never replace supported pins from discovery output.

## CI and human promotion

`.github/workflows/insightface-maintenance.yml` provides workflow_dispatch with
a version or `latest`. A weekly check is disabled unless repository variable
`LIC_CHECK_INSIGHTFACE=true` is set. Runs have read-only repository permissions,
one concurrency group, a timeout and 30-day candidate artifacts. They cannot
push a commit, edit supported pins, or publish a release.

Stages: detect -> build twice -> unit tests -> disposable Windows provider
installation tests -> upload candidate/reports -> real NVIDIA gate -> human
approval. The workflow does not claim a hosted CPU runner validated CUDA.

`build-metadata.json` describes build validity. `compatibility-summary.json`
binds CPU/metadata results to the wheel hash. GPU results are separate; every
generated report keeps `approved_for_lic=false`. Approval is a deliberate
maintainer review of source provenance, the identical candidate hash, all test
reports and the intended supported profile. No automatic promotion exists.
Validation also records the installed wheel's pip `direct_url.json` archive hash
and embedded LIC provenance, so GPU evidence can be matched to the candidate.

## Disposable tests

```powershell
.build-env\Scripts\python.exe -m unittest tests.test_dependency_maintenance
.build-env\Scripts\python.exe tools/test_dependency_environments.py --work build/test-envs --wheel build/candidate/insightface-1.0.1+lic.cuda13.1-py3-none-any.whl
```

The second command refuses existing target environments. It exercises face ->
body and body -> face, repeating both installations, then checks exact
ORT/OpenCV/MediaPipe distribution identities, `pip check`, imports, all hashed
package-path ownership, selected ORT/OpenCV RECORD hashes and an actual CPU ONNX
graph. It retains reports and environments for review. NVIDIA-repair routing,
CPU boundaries, metadata retirement and false advertised-GPU success are also
covered by unit tests. Routing mocks are not GPU inference evidence.

## NVIDIA gate

Use a disposable environment on the supported NVIDIA workstation. Install the
approved CUDA Torch pair before base requirements, using the official cu130
index and `constraints-nvidia.txt`. Install base, face and body through
`python -m lic_dependencies.installer <component>`. Keep model downloads off.

```powershell
python -m lic_dependencies.validation --gpu --face-model-root "$env:USERPROFILE/.insightface" --image "PATH_TO_APPROVED_FACE_FIXTURE" --output build/gpu-validation.json
```

Use the disposable interpreter explicitly in a real run. The tool copies just
the existing buffalo_l detection/recognition models into its temporary model
root. It never downloads weights or writes the source model directory. Supply
a fixture with a detectable face; otherwise the full face gate correctly fails.

The gate checks Torch CUDA matrix multiplication and cuDNN convolution, exact
Torch/CUDA family, DLL preload diagnostics, ORT CUDA session creation, ONNX
execution with CPU fallback disabled for the synthetic graph, profiled CUDA
node execution and InsightFace detection/512-dimensional finite embeddings.
It records actual execution providers for both face model sessions. A full
`gpu_validated=true` requires all of that; advertised providers alone cannot
pass. CPU and GPU checks never set human approval.
InsightFace 1.0.1 drops session options in its public model factory; the diagnostic
recreates identical model sessions with profiling enabled before calling the
unchanged upstream detection/embedding methods. Detection may assign some nodes
to CPU while executing its convolution work on CUDA; the report exposes both.

Separately smoke-test Florence caption/detection/OCR with its pinned local
snapshot, and MediaPipe pose detection with the existing .task model. Run LIC
regression/GUI checks against disposable settings and catalog copies. These
application/model checks are not implied by a package import check.

## Runtime and support boundaries

Setup & Repair now reports dependency conflicts and, when explicitly showing
setup status, an actual ORT CUDA graph result including DLL diagnostics. Normal
launch readiness remains the required base stack. Optional GPU failures are
reported without changing LIC's existing CPU-fallback policy. Face runtime
messages expose actual loaded session providers; explicit GPU validation adds
node-execution proof.

The installer never cleans conflicting active environments automatically.
Prepare a fresh venv at its final path after backup approval. Existing CPU face
inference is unchanged; CPU-only face installation and older CUDA profiles need
their own reviewed packaging recipe. CPU base/body remain explicit boundaries;
the NVIDIA recipe is never silently selected for CPU machines.
The Python 3.14.6 Windows baseline is the environment qualified by this workflow.
Broader Python/OS support is not established by these pins; changing LIC's declared
support range or qualifying additional profiles requires planning review.

This work deliberately does not reconcile RELEASE_MANIFEST.sha256. New package,
workflow, constraint and documentation files must enter the release inventory
in the separately approved release-manifest task before packaging a public LIC
release. No current source ZIP should be presented as containing these changes.
