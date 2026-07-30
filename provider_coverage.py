"""Read-only analysis coverage summaries for one active catalog.

Provider cards should answer a durable question: what work is already stored
for the catalog currently selected by the user?  This module deliberately
queries provider result tables instead of inferring coverage from the latest
run report.  A cancelled run, app restart, or provider-by-provider workflow
therefore produces the same status.

Coverage is provider-wide rather than tied to one model fingerprint.  Model and
threshold compatibility still controls whether a future run may *reuse* a
result; this summary only reports whether each image has ever reached the named
provider and whether a successful result exists.
"""

from __future__ import annotations

import sqlite3

from dataclasses import dataclass
from pathlib import Path

from catalog import Catalog


@dataclass(slots=True, frozen=True)
class ProviderCoverage:
    """Counts for one provider across present images in the active catalog."""

    total_images: int
    checked_images: int
    successful_images: int
    error_images: int

    @property
    def unchecked_images(self) -> int:
        return max(0, self.total_images - self.checked_images)

    def status_text(self, *, label: str = "Checked") -> str:
        """Return compact UI text without hiding prior failed attempts."""
        text = (
            f"{label}: {self.checked_images:,} / {self.total_images:,} present "
            f"catalog images · successful: {self.successful_images:,}"
        )
        if self.error_images:
            text += f" · errors: {self.error_images:,}"
        if self.unchecked_images:
            text += f" · remaining: {self.unchecked_images:,}"
        return text


@dataclass(slots=True, frozen=True)
class CatalogProviderCoverage:
    """Coverage for every analysis provider represented in the schema."""

    florence: ProviderCoverage
    florence_triage_successful: int
    face: ProviderCoverage
    body: ProviderCoverage


def _coverage(
    connection: sqlite3.Connection,
    table: str,
) -> ProviderCoverage:
    """Count distinct provider attempts and successes for present images."""
    row = connection.execute(
        f"""
        WITH present_images AS (
            SELECT DISTINCT image_id
            FROM files
            WHERE status = 'present'
        ),
        provider_status AS (
            SELECT
                results.image_id,
                MAX(CASE WHEN results.status = 'success' THEN 1 ELSE 0 END)
                    AS has_success,
                MAX(CASE WHEN results.status = 'error' THEN 1 ELSE 0 END)
                    AS has_error
            FROM {table} AS results
            JOIN present_images AS present
                ON present.image_id = results.image_id
            GROUP BY results.image_id
        )
        SELECT
            (SELECT COUNT(*) FROM present_images) AS total_images,
            COUNT(provider_status.image_id) AS checked_images,
            COALESCE(SUM(provider_status.has_success), 0) AS successful_images,
            COALESCE(
                SUM(
                    CASE
                        WHEN provider_status.has_success = 0
                             AND provider_status.has_error = 1
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS error_images
        FROM provider_status
        """
    ).fetchone()
    assert row is not None
    return ProviderCoverage(
        total_images=int(row[0]),
        checked_images=int(row[1]),
        successful_images=int(row[2]),
        error_images=int(row[3]),
    )


def read_catalog_provider_coverage(
    database_path: Path,
) -> CatalogProviderCoverage:
    """Return persisted provider coverage after applying additive migrations."""
    database = database_path.expanduser().resolve()
    with Catalog(database):
        pass

    connection = sqlite3.connect(database, timeout=30.0)
    try:
        florence = _coverage(connection, "analysis_results")
        face = _coverage(connection, "face_image_results")
        body = _coverage(connection, "body_image_results")
        triage_row = connection.execute(
            """
            SELECT COUNT(DISTINCT results.image_id)
            FROM analysis_results AS results
            JOIN files
                ON files.image_id = results.image_id
               AND files.status = 'present'
            WHERE
                results.status = 'success'
                AND results.include_triage = 1
            """
        ).fetchone()
        return CatalogProviderCoverage(
            florence=florence,
            florence_triage_successful=int(triage_row[0] if triage_row else 0),
            face=face,
            body=body,
        )
    finally:
        connection.close()
