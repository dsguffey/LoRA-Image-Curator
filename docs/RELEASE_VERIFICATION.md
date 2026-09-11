# Release Verification

The source manifest owns the public source inventory. It records the reviewed bytes for every tracked public source file and is separate from a published installer package's own contents.

## Before a source or installer release

Review the final staged checkout. Preserve user data, models, catalogs, caches, logs, virtual environments, generated reports, build outputs, and local repair material outside the release boundary. Do not add a file to the manifest merely because it is present locally: first decide that it belongs in the public source inventory.

Regenerate and verify the source manifest with the project tooling:

```powershell
python -B -m tools.check_release --regenerate
python -B -m tools.check_release
python -B -m unittest tests.test_release_gate
python -B -m tests.test_clean_install
python -B -m tests.test_v0282_regression
python -B tools/audit_project.py
```

The default gate is non-mutating. Regeneration computes hashes for the existing approved source inventory; it does not discover or approve new files, create an archive, or turn generated/private material into release content. Tracked source omissions fail. When Git metadata is available, coverage is also checked against the tracked tree. An extracted full source tree can run the gate without Git.

The gate checks direct, lazy, and package-relative local imports without executing providers. Run a separate extracted-package import smoke test with the supported dependencies available. Provider execution, GPU compatibility, and live Tk behavior remain Windows release-gate work rather than proof from a static import check.

## Archive and reproducibility checks

Release builders must validate source-manifest coverage before packaging. Build archives in stable sorted order, verify their member hashes and CRCs after creation, and extract them into a clean temporary directory before publication. For an upgrade-capable source release, also perform a synthetic overlay check that preserves unrelated user/runtime files.

Hashes describe exact bytes, including line endings. `.gitattributes` controls Windows launcher line endings. Generate the committed manifest from the final staged checkout with those attributes applied, then verify the delivered bytes. If unrelated local edits remain uncommitted, preserve them; do not commit them only to force their hashes to match.

`LoRA_Image_Curator_Source_v0.28.4.8.zip` is retained as historical evidence of its original assembly state. Its embedded manifest contains stale hashes and its directory entries do not meet the current strict cache-exclusion rule. Do not rewrite the archive or represent it as output of the current release pipeline.
