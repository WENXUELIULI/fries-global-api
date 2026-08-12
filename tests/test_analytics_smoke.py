import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apps.analytics.store import country_name, get_analytics_summary, record_analytics_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeHeaders:
    """Minimal Headers-like object for analytics ingestion tests."""

    def __init__(self, data: dict[str, str]):
        self._data = {key.lower(): value for key, value in data.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key.lower(), default)

    def has(self, key: str) -> bool:
        return key.lower() in self._data


@pytest.fixture
def analytics_initialized(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    private = tmp_path / ".private"
    access = tmp_path / "ADMIN_ACCESS.txt"
    if private.exists():
        shutil.rmtree(private)
    access.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "init_admin.py")],
        cwd=tmp_path,
        check=True,
        env=env,
    )
    yield tmp_path


def test_records_visit_and_returns_summary_shape(analytics_initialized):
    record_analytics_event(
        headers=FakeHeaders(
            {
                "user-agent": "pytest",
                "cf-connecting-ip": "1.2.3.4",
                "cf-ipcountry": "US",
            }
        ),
        path="/en",
        method="GET",
        status=200,
        kind="visit",
    )
    summary = get_analytics_summary()
    assert "generatedAt" in summary
    assert "views24h" in summary
    assert "hourly" in summary
    assert summary["views24h"] >= 1
    assert len(summary["hourly"]) == 24
    assert all(item["label"] and len(item["label"]) == 5 for item in summary["hourly"])
    assert any("United States" in item["label"] for item in summary["topCountries"])


def test_country_name_matches_english_display():
    assert country_name("XX") == "Unknown"
    assert country_name("US") == "United States"
    assert country_name("CN") == "China"
