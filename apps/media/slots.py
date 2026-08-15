"""Homepage image slot keys and default public paths."""

from __future__ import annotations

SLOT_DEFAULTS: dict[str, str | None] = {
    "hero.backdrop": "/hero-port-global.png",
    "brand.symbol": "/brand-symbol.png",
    "categories.mascot_sheet": "/category-mascots.png",
    "services.case_a": "/case-industrial-tech.png",
    "services.case_b": "/case-manufacturing.png",
    "cta.visual": None,  # optional; no public default — components may hide
}

SLOT_KEYS: frozenset[str] = frozenset(SLOT_DEFAULTS)


def default_public_path(slot_key: str) -> str | None:
    """Return the site `public/` fallback path for a slot, or None if optional/absent."""
    return SLOT_DEFAULTS.get(slot_key)
