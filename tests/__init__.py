"""Maintained regression and live-GUI checks for LoRA Image Curator.

Historical tests originally lived beside the application modules. Keeping the
project and test directories importable preserves those cumulative checkpoints
after the public repository cleanup without copying production code or relying
on a developer's current working directory.
"""

from __future__ import annotations

import sys

from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent

for import_root in (PROJECT_ROOT, TEST_ROOT):
    import_text = str(import_root)
    if import_text not in sys.path:
        sys.path.insert(0, import_text)
