"""
training_text.py

Pure helpers for constructing training text from catalog layers.

LoRA Image Curator deliberately does not store one mutable "final caption." Provider
output, trigger keywords, manual tags, and AI exclusions have different owners
and lifetimes. Export code can therefore rebuild the effective training text at
any time without rewriting raw Florence analysis or losing user decisions.

Version 0.9.0 introduces reusable export profiles. A profile chooses which
layers participate in the final sidecar text while the catalog keeps the source
layers independent:

    trigger keyword -> manual tags -> active AI tags -> optional raw caption

Names are deduplicated case-insensitively while preserving the first spelling
and the layer priority above. Built-in profiles are intentionally simple and
transparent; the Custom profile is assembled from explicit check boxes in the
export dialog and is saved only as a user preference, not as catalog data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(slots=True, frozen=True)
class TrainingTextLayers:
    """User and provider layers needed to derive one image's training text."""

    trigger_keyword: str = ""
    manual_tags: tuple[str, ...] = ()
    active_ai_tags: tuple[str, ...] = ()
    raw_caption: str = ""


@dataclass(slots=True, frozen=True)
class TrainingTextProfile:
    """
    Describe how LoRA Image Curator assembles one export sidecar.

    ``key`` is a stable machine-readable identifier used in settings and export
    history. ``label`` and ``description`` are user-facing. The component flags
    remain explicit so future training backends can add new presets without
    changing the catalog schema or mutating old provider output.
    """

    key: str
    label: str
    description: str
    include_trigger: bool = True
    include_manual_tags: bool = True
    include_ai_tags: bool = True
    include_raw_caption: bool = False
    tag_separator: str = ", "
    caption_separator: str = "; "


BUILTIN_TRAINING_PROFILES: Mapping[str, TrainingTextProfile] = {
    "flux_lora": TrainingTextProfile(
        key="flux_lora",
        label="Flux LoRA",
        description=(
            "Trigger Keyword first, then manual tags, then active AI tags. "
            "This is the richest tag-oriented preset."
        ),
        include_trigger=True,
        include_manual_tags=True,
        include_ai_tags=True,
        include_raw_caption=False,
    ),
    "sdxl_lora": TrainingTextProfile(
        key="sdxl_lora",
        label="SDXL LoRA",
        description=(
            "Trigger Keyword plus manually curated tags. AI tags remain available "
            "in the catalog but are omitted from this conservative preset."
        ),
        include_trigger=True,
        include_manual_tags=True,
        include_ai_tags=False,
        include_raw_caption=False,
    ),
    "sd15_lora": TrainingTextProfile(
        key="sd15_lora",
        label="SD 1.5 LoRA",
        description=(
            "Trigger Keyword plus manually curated tags. This conservative "
            "tag-oriented preset avoids automatically adding provider tags."
        ),
        include_trigger=True,
        include_manual_tags=True,
        include_ai_tags=False,
        include_raw_caption=False,
    ),
    "general_lora": TrainingTextProfile(
        key="general_lora",
        label="General / Other LoRA",
        description=(
            "Trigger Keyword, manual tags, and active AI tags. Review the live "
            "preview because trainer-specific caption expectations vary."
        ),
        include_trigger=True,
        include_manual_tags=True,
        include_ai_tags=True,
        include_raw_caption=False,
    ),
    "caption_dataset": TrainingTextProfile(
        key="caption_dataset",
        label="Caption Dataset",
        description=(
            "The latest raw natural-language provider caption only. Manual "
            "curation layers remain preserved in the catalog."
        ),
        include_trigger=False,
        include_manual_tags=False,
        include_ai_tags=False,
        include_raw_caption=True,
    ),
}

PROFILE_LABEL_TO_KEY = {
    profile.label: profile.key for profile in BUILTIN_TRAINING_PROFILES.values()
}


def _clean_text(value: str) -> str:
    """Collapse accidental whitespace without changing intentional wording."""
    return " ".join(str(value).split()).strip()


def _clean_unique(values: Iterable[str], seen: set[str]) -> list[str]:
    """Normalize whitespace and append only new case-insensitive concepts."""
    output: list[str] = []
    for raw_value in values:
        value = _clean_text(raw_value)
        normalized = value.casefold()
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        output.append(value)
    return output


def build_tag_training_text(
    layers: TrainingTextLayers,
    *,
    separator: str = ", ",
    include_trigger: bool = True,
    include_manual_tags: bool = True,
    include_ai_tags: bool = True,
) -> str:
    """
    Build deterministic, duplicate-free tag text for a LoRA sidecar.

    A manually asserted tag supersedes an equivalent AI suggestion because the
    manual layer is processed first. The returned string is derived data; it is
    not intended to be written back into the catalog.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    if include_trigger:
        ordered.extend(_clean_unique([layers.trigger_keyword], seen))
    if include_manual_tags:
        ordered.extend(_clean_unique(layers.manual_tags, seen))
    if include_ai_tags:
        ordered.extend(_clean_unique(layers.active_ai_tags, seen))
    return separator.join(ordered)


def build_training_text(
    layers: TrainingTextLayers,
    profile: TrainingTextProfile,
) -> str:
    """
    Build the exact text written to an exported sidecar for ``profile``.

    Tag-oriented components are assembled first in the deterministic order
    chosen for LoRA Image Curator. If a custom profile also requests the raw caption,
    the caption follows the tag list using ``caption_separator``. A caption-only
    profile returns the caption without added punctuation or fabricated text.
    Empty layers simply contribute nothing.
    """
    tag_text = build_tag_training_text(
        layers,
        separator=profile.tag_separator,
        include_trigger=profile.include_trigger,
        include_manual_tags=profile.include_manual_tags,
        include_ai_tags=profile.include_ai_tags,
    )
    raw_caption = _clean_text(layers.raw_caption) if profile.include_raw_caption else ""

    if tag_text and raw_caption:
        return f"{tag_text}{profile.caption_separator}{raw_caption}"
    return tag_text or raw_caption


def find_repeated_training_text_groups(
    items: Iterable[tuple[int, TrainingTextLayers]],
    profile: TrainingTextProfile,
) -> tuple[tuple[int, ...], ...]:
    """Return image-ID groups whose exported sidecar text is identical.

    This helper deliberately calls :func:`build_training_text` instead of
    maintaining a second approximation of export behavior.  Validation and
    export therefore agree about layer inclusion, case-insensitive tag
    deduplication, whitespace cleanup, and profile-specific omissions.

    Empty sidecars are excluded because the readiness model reports those as a
    separate, blocking ``No Training Text`` finding.  Group and image order are
    deterministic so GUI filtering, tests, and release reports stay stable.
    """
    grouped_ids: dict[str, list[int]] = defaultdict(list)
    for image_id, layers in items:
        training_text = build_training_text(layers, profile)
        normalized = " ".join(training_text.split()).strip().casefold()
        if normalized:
            grouped_ids[normalized].append(int(image_id))

    groups = [
        tuple(sorted(image_ids))
        for image_ids in grouped_ids.values()
        if len(image_ids) > 1
    ]
    return tuple(sorted(groups, key=lambda group: group[0]))


def custom_training_profile(
    *,
    include_trigger: bool,
    include_manual_tags: bool,
    include_ai_tags: bool,
    include_raw_caption: bool,
) -> TrainingTextProfile:
    """Create the explicit Custom profile used by the export dialog."""
    return TrainingTextProfile(
        key="custom",
        label="Custom",
        description="A locally remembered combination of catalog layers.",
        include_trigger=bool(include_trigger),
        include_manual_tags=bool(include_manual_tags),
        include_ai_tags=bool(include_ai_tags),
        include_raw_caption=bool(include_raw_caption),
    )


def get_training_profile(
    key: str,
    *,
    custom_profile: TrainingTextProfile | None = None,
) -> TrainingTextProfile:
    """Resolve a stable profile key with a safe Flux default."""
    normalized = str(key or "").strip().casefold()
    if normalized == "custom" and custom_profile is not None:
        return custom_profile
    return BUILTIN_TRAINING_PROFILES.get(
        normalized,
        BUILTIN_TRAINING_PROFILES["flux_lora"],
    )
