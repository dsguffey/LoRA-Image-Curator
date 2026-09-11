"""Declarative, product-neutral capability presentation contracts."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    name: str
    summary: str
    skipped_consequence: str
    tier: str
    default_selected: bool
    provider: str
    details: dict[str, object] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    actions: tuple[str, ...] = ("details",)

    def __post_init__(self) -> None:
        if self.tier not in {"required", "optional"}:
            raise ValueError("capability tier must be required or optional")
        if self.tier == "required" and not self.default_selected:
            raise ValueError("required capability cannot be omitted")
        if self.tier == "optional" and self.default_selected:
            raise ValueError("optional capabilities require an explicit documented default policy")
        if not all((self.capability_id, self.name, self.summary, self.skipped_consequence, self.provider)):
            raise ValueError("capability disclosure is incomplete")


def lic_capabilities() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor(
            "catalog-workspace", "Catalog and curate image sets",
            "Browse, organize, review and export an LIC image catalog.",
            "LIC cannot provide its baseline catalog workflow.", "required", True,
            "LoRA Image Curator", {"component": "LIC Lite 0.28.4"}),
        CapabilityDescriptor(
            "captioning", "Generate image captions",
            "Create local AI-assisted captions and basic image triage.",
            "Automatic captions and Florence triage are unavailable; catalog, editing and export remain usable.",
            "optional", False,
            "Florence community / Microsoft", {"model": "Florence-2-large-ft",
            "revision": "26b734a54fdfbf9c398351eedfabb7f27fc470b7",
            "source_host": "huggingface.co", "storage": "Shared/Models"}),
    )


FLOW = ("install-and-update", "move-installation", "help")


def first_run_contract(root: str, *, model_root: str | None = None,
                       start_menu: bool = True, desktop: bool = False) -> dict:
    capabilities = lic_capabilities()
    return {
        "flow": FLOW, "can_revisit": True, "install_action": "component-state-action",
        "required": [c.__dict__ for c in capabilities if c.tier == "required"],
        "optional": [c.__dict__ for c in capabilities if c.tier == "optional"],
        "storage": {"application": root, "models": model_root or f"{root}/Shared/Models",
                    "independent": True},
        "shortcuts": {"start_menu": start_menu, "desktop": desktop},
        "completion": {"title": "LoRA Image Curator is ready", "launch_action": True},
    }
