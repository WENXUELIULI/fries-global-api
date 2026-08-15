from apps.engage.sections import SECTION_IDS


def test_section_ids():
    assert "hero" in SECTION_IDS
    assert "cta" in SECTION_IDS
    assert "categories" in SECTION_IDS
    assert "services" in SECTION_IDS
    assert "process" in SECTION_IDS
    assert "why-us" in SECTION_IDS
    assert "global-network" in SECTION_IDS
    assert "footer" in SECTION_IDS
    assert "nav" in SECTION_IDS
