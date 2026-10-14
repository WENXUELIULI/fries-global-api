"""Homepage image slot keys and default public paths."""

from __future__ import annotations

SLOT_DEFAULTS: dict[str, str | None] = {
    "hero.backdrop": "/hero-port-global.png",
    "brand.symbol": "/brand-symbol.png",
    "services.case_1": "/case-industrial-tech.png",
    "services.case_2": "/case-manufacturing.png",
    "services.case_3": "/case-industrial-tech.png",
    "services.case_4": "/case-manufacturing.png",
    "quality.backdrop": None,  # optional; no public default — components may hide
    "cta.visual": None,  # optional; no public default — components may hide
    "about.lobby": None,
    "about.storefront": None,
}

SLOT_KEYS: frozenset[str] = frozenset(SLOT_DEFAULTS)


def default_public_path(slot_key: str) -> str | None:
    """Return the site `public/` fallback path for a slot, or None if optional/absent."""
    return SLOT_DEFAULTS.get(slot_key)
