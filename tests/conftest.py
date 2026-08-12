import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_private(tmp_path, settings):
    """每个用例独立的 .private / ADMIN_ACCESS，避免污染本地密钥。"""
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
