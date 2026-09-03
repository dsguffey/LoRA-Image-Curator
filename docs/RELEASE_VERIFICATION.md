# Source and Portable Source release verification

The source manifest owns the public source inventory. Portable Source selects
all manifested root Python modules plus the exact files listed in
`portable_source_payload_policy.json`. Nested packages are explicit file lists;
never collect a developer installation recursively.

The dependency checkpoint adds these inputs to Portable Source:

| File | Reason |
| --- | --- |
| `lic_dependencies/__init__.py` | Python package identity |
| `lic_dependencies/profile.py`, `profile.json` | Shared runtime/setup profile |
| `lic_dependencies/installer.py` | Base, body and face setup |
| `lic_dependencies/insightface_build.py`, `upstream.py` | Verified face-wheel acquisition/build used by setup |
| `lic_dependencies/runtime_probe.py` | Setup's execution-provider diagnostic |
| `lic_dependencies/validation.py` | Supplies graph execution to the runtime probe; not solely a developer tool |
| `constraints-lic.txt`, `constraints-nvidia.txt` | Canonical dependency constraints |

The checkpoint's `docs/DEPENDENCY_MAINTENANCE.md` is source-release maintenance
documentation. `tests/test_dependency_maintenance.py` and
`tools/test_dependency_environments.py` are development/test tools; the
InsightFace maintenance workflow is CI-only. All belong in the full source
inventory, but remain outside Portable Source. No directory under
`lic_dependencies` is collected implicitly. Its caches are excluded.

`requirements-face.txt` remains intentionally excluded from Portable Source:
the shared face installer builds its verified wheel and selects requirements
directly. Base/body requirements remain included. No wheel, model, environment,
catalog, settings, local report, CI workflow or test suite is bundled in Portable
Source. The future private-runtime distribution is unchanged.

After final source review, add any new approved public files to the manifest's
inventory (using tooling to compute hashes), then run:

```powershell
python -B -m tools.check_release --regenerate
python -B -m tools.check_release
python -B -m unittest tests.test_release_gate
python -B -m tests.test_clean_install
python -B -m tests.test_v0282_regression
python -B tools/audit_project.py
```

The default gate is non-mutating. Regeneration uses the existing source builder's
hash generator and does not discover/approve files or create an archive. Tracked
source omissions fail; explicitly reviewed new manifest members may be tested
before staging. Generated/private tracked artifacts need a policy decision.
An extracted full source tree can run the gate without Git; repository coverage
is additionally checked when `.git` is present.

Both builders run coverage checks before packaging. Portable Source also requires
current source hashes. Its package manifest describes the selected archive bytes,
including the `PORTABLE_README.txt` to `README.txt` rename. A package's own manifest
is distinct from the full source manifest.

The gate checks local imports, including lazy imports and package-relative
imports, without executing providers. Run an extracted-package import smoke test
with the supported dependencies available: import `app`, `setup_assistant`, both
provider installers and every `lic_dependencies` module using isolated Python
(`-I -B`) from outside the checkout. Confirm profile and constraints resolve inside
the extraction. No model downloads, setup installations or analysis are needed.
Do not treat static local-import checking as proof of arbitrary computed imports
or third-party ABI compatibility. Provider/GPU acceptance is a separate gate.

Hashes describe exact bytes, including line endings. `.gitattributes` specifies
CRLF for Windows launchers. Generate the committed manifest from the final staged
checkout with those attributes applied, and verify the actual delivered bytes.
If unrelated local edits remain uncommitted, preserve them and keep a locally
regenerated working-tree manifest; do not commit unrelated edits merely to make
their hashes match. Verify both the checkpoint checkout and the local tree.

Historical `LoRA_Image_Curator_Source_v0.28.4.8.zip` is retained as evidence of its
original assembly state. Its embedded manifest has stale hashes; its directory
entries also fail today's strict cache exclusion. Do not rewrite that archive or
retroactively present it as verified output of the current release pipeline.
