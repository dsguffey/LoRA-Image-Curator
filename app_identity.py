"""Stable application identity and compatibility identifiers.

The public product name changed from ``Dataset Tools`` to
``LoRA Image Curator`` in v0.25.0. Existing catalogs and per-user settings must
remain readable, so the legacy database marker and application-data directory
are deliberately retained as compatibility identifiers. Keeping that boundary
explicit prevents a future branding edit from accidentally orphaning user data.
"""

from __future__ import annotations


APP_NAME = "LoRA Image Curator"
APP_VERSION = "0.27.21"
AUTHOR_NAME = "David Scott Guffey"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/davidsguffey/"

# New installations receive the public product identity. Catalog validation
# still accepts the historical marker so existing databases remain portable.
CATALOG_APPLICATION_ID = APP_NAME
LEGACY_CATALOG_APPLICATION_ID = "Dataset Tools"
SUPPORTED_CATALOG_APPLICATION_IDS = frozenset(
    {CATALOG_APPLICATION_ID, LEGACY_CATALOG_APPLICATION_ID}
)
APP_DATA_DIRECTORY_NAME = "LoRAImageCurator"
