"""Regressie: de topologiecatalogus mag niet omvallen op ouder-volgorde.

Een container die eerst door de ene bron (Glances) is gevonden en daarna door
een andere (Portainer) aan een andere host wordt gehangen, houdt zijn oude
rowid maar krijgt een ouder met een hógere rowid. `sync_topology_catalog`
voegde knopen in rowid-volgorde in en verwees dan naar een ouderknoop die nog
niet bestond -> FOREIGN KEY constraint failed -> /api/bootstrap gaf 500.
"""
from __future__ import annotations

import sqlite3
import uuid

from fastapi.testclient import TestClient

from patch_manager.main import app, database
from patch_manager.topology import topology_payload

from tests.conftest import CREDENTIALS


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _seed_child_before_parent() -> tuple[str, str]:
    """Zet een kind-entity met een lagere rowid dan zijn ouder in de database."""
    now = "2026-01-01T00:00:00+00:00"
    child_id, parent_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        # Kind eerst, zónder ouder: zo krijgt het de lagere rowid. (De
        # entities-tabel heeft zelf een FK op parent_id, dus we kunnen niet
        # meteen naar een ouder verwijzen die nog niet bestaat — precies zoals
        # in het echt: Glances maakt de container, Portainer verhangt hem pas.)
        connection.execute(
            """INSERT INTO entities(id,name,type,origin,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (child_id, "kind-container", "container", "discovered", "up", now, now),
        )
        # Ouder daarna: hogere rowid.
        connection.execute(
            """INSERT INTO entities(id,name,type,origin,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (parent_id, "ouder-host", "host", "discovered", "up", now, now),
        )
        # Nu het kind aan de later gemaakte ouder hangen: rowid-inversie.
        connection.execute("UPDATE entities SET parent_id=? WHERE id=?", (parent_id, child_id))
    return child_id, parent_id


def test_bootstrap_survives_child_with_higher_rowid_parent() -> None:
    with TestClient(app) as client:
        login(client)
        child_id, parent_id = _seed_child_before_parent()
        # De bug uitte zich als een 500 op /api/bootstrap.
        response = client.get("/api/bootstrap")
        assert response.status_code == 200, response.text
        nodes = {node["id"]: node for node in response.json()["topology"]["nodes"]}
        child_node = nodes[f"entity:{child_id}"]
        # De ouder is correct gekoppeld, en die knoop bestaat echt.
        assert child_node["parent_node_id"] == f"entity:{parent_id}"
        assert child_node["parent_node_id"] in nodes


def test_catalog_leaves_parent_empty_when_parent_has_no_node() -> None:
    """Een ouder zonder eigen knoop (genegeerd) mag geen dangling FK opleveren."""
    with TestClient(app) as client:
        login(client)
        child_id, parent_id = _seed_child_before_parent()
        # Markeer de ouder als genegeerd: die krijgt geen topologieknoop.
        with database.transaction() as connection:
            connection.execute("UPDATE entities SET ignored=1 WHERE id=?", (parent_id,))
        response = client.get("/api/bootstrap")
        assert response.status_code == 200, response.text
        nodes = {node["id"]: node for node in response.json()["topology"]["nodes"]}
        assert f"entity:{parent_id}" not in nodes
        assert nodes[f"entity:{child_id}"]["parent_node_id"] is None
