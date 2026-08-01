from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="plugnet-tests-"))
os.environ["PATCH_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["PATCH_BACKUP_DIR"] = str(TEST_ROOT / "backups")
# Geen OUI-bestand tenzij een test er zelf een zet.
os.environ["PATCH_OUI_FILE"] = str(TEST_ROOT / "geen-oui.csv")

from fastapi.testclient import TestClient  # noqa: E402

from patch_manager.main import app, database  # noqa: E402

CREDENTIALS = {"username": "lucas", "password": "correct horse battery staple"}


def setup_admin(client: TestClient) -> str:
    response = client.post("/api/auth/setup", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture(autouse=True)
def isolated_installation(request: pytest.FixtureRequest):
    """Elke test start op een lege database met (meestal) een ingelogde beheerder."""
    for suffix in ("", "-wal", "-shm"):
        database.path.with_name(database.path.name + suffix).unlink(missing_ok=True)
    for candidate in (TEST_ROOT / "backups").glob("*"):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)
    database.initialize()
    if "no_admin" not in request.keywords:
        with TestClient(app) as client:
            setup_admin(client)
    yield
