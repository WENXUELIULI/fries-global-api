import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apps.authx.services import (
    create_admin_session,
    verify_admin_session,
    verify_credentials,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def admin_initialized(tmp_path, settings):
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


def test_rejects_bad_credentials(admin_initialized):
    assert verify_credentials("nope", "nope") is False


def test_round_trips_session_token(admin_initialized):
    token = create_admin_session()
    assert verify_admin_session(token) is True
    assert verify_admin_session("tampered." + token) is False
