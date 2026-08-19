"""Een bestaande database moet bij te werken zijn zonder hem weg te gooien.

De schemadefinities hieronder zijn met opzet een kopie van hoe de database
er vóór het migratieregister uitzag. Ze mogen niet meebewegen met `db.py`:
juist het verschil is wat getest wordt.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from patch_manager.db import SCHEMA_VERSION, Database, utcnow


LEGACY_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE physical_devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE ports (id TEXT PRIMARY KEY,
  physical_device_id TEXT NOT NULL REFERENCES physical_devices(id) ON DELETE CASCADE,
  number INTEGER NOT NULL, side TEXT NOT NULL DEFAULT 'front',
  peer_port_id TEXT REFERENCES ports(id) ON DELETE SET NULL, label TEXT NOT NULL DEFAULT '',
  speed_mbps INTEGER, notes TEXT NOT NULL DEFAULT '', UNIQUE(physical_device_id, number, side));
CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'device',
  origin TEXT NOT NULL CHECK(origin IN ('manual','discovered')), status TEXT NOT NULL DEFAULT 'unknown',
  status_updated_at TEXT, ip_address TEXT, mac_address TEXT, hostname TEXT,
  parent_id TEXT REFERENCES entities(id) ON DELETE SET NULL, vendor TEXT,
  ignored INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '', first_seen_at TEXT, last_seen_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE cables (id TEXT PRIMARY KEY,
  a_port_id TEXT NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
  b_port_id TEXT REFERENCES ports(id) ON DELETE CASCADE,
  b_entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, updated_by TEXT REFERENCES users(id));
-- Dit is de constraint die weg moest: exact één provider per type.
CREATE TABLE providers (id TEXT PRIMARY KEY, type TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0, poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
  config_json TEXT NOT NULL DEFAULT '{}', last_run_at TEXT, last_success_at TEXT,
  last_error TEXT, updated_at TEXT NOT NULL);
"""


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    """Een database zoals hij na een echte deploy in de meterkast zou staan."""
    path = tmp_path / "patch-manager.db"
    now = utcnow()
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        """INSERT INTO physical_devices(id,name,type,position,created_at,updated_at)
           VALUES('switch-01','TP-Link SG108E 01','switch',0,?,?)""", (now, now),
    )
    for number in range(1, 9):
        connection.execute(
            "INSERT INTO ports(id,physical_device_id,number,label,speed_mbps) VALUES(?,?,?,?,1000)",
            (f"switch-01-p{number}", "switch-01", number, f"Poort {number}"),
        )
    entity_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO entities(id,name,type,origin,status,ip_address,mac_address,created_at,updated_at)
           VALUES(?,'NAS DS223','nas','manual','up','192.168.1.50','aa:bb:cc:dd:ee:ff',?,?)""",
        (entity_id, now, now),
    )
    connection.execute(
        "INSERT INTO cables(id,a_port_id,b_entity_id,label,updated_at) VALUES(?,?,?,'P-01',?)",
        (str(uuid.uuid4()), "switch-01-p3", entity_id, now),
    )
    connection.execute(
        """INSERT INTO providers(id,type,name,enabled,poll_interval_seconds,config_json,updated_at)
           VALUES('portainer','portainer','Portainer',1,60,'{"base_url":"https://x:9443"}',?)""", (now,),
    )
    connection.execute(
        "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,'lucas','x',?)",
        (str(uuid.uuid4()), now),
    )
    connection.commit()
    connection.close()
    return path


def read(path: Path, sql: str):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_an_old_database_keeps_everything_it_had(legacy: Path) -> None:
    """Dit is de hele reden dat het register bestaat."""
    Database(legacy).initialize()
    assert read(legacy, "SELECT name,ip_address FROM entities") == [("NAS DS223", "192.168.1.50")]
    assert read(legacy, "SELECT a_port_id,label FROM cables") == [("switch-01-p3", "P-01")]
    assert read(legacy, "SELECT username FROM users") == [("lucas",)]
    assert read(legacy, "SELECT config_json FROM providers WHERE id='portainer'") == [('{"base_url":"https://x:9443"}',)]
    assert len(read(legacy, "SELECT id FROM ports WHERE physical_device_id='switch-01'")) == 8


def test_the_new_columns_and_tables_arrive(legacy: Path) -> None:
    Database(legacy).initialize()
    assert any(row[1] == "uplink_device_id" for row in read(legacy, "PRAGMA table_info(entities)"))
    assert any(row[1] == "monitor_entity_id" for row in read(legacy, "PRAGMA table_info(physical_devices)"))
    assert read(legacy, "SELECT 1 FROM sqlite_master WHERE name='app_links'") == [(1,)]


def test_the_unique_on_provider_type_is_gone(legacy: Path) -> None:
    """Een impliciete index kun je niet droppen; die tabel moet herbouwd."""
    Database(legacy).initialize()
    connection = sqlite3.connect(legacy)
    try:
        connection.execute(
            """INSERT INTO providers(id,type,name,enabled,poll_interval_seconds,config_json,updated_at)
               VALUES('portainer-2','portainer','Portainer zolder',0,60,'{}',?)""", (utcnow(),),
        )
        connection.commit()
        assert len(connection.execute("SELECT id FROM providers WHERE type='portainer'").fetchall()) == 2
    finally:
        connection.close()


def test_the_ont_is_added_to_an_existing_inventory(legacy: Path) -> None:
    """De seed draait alleen bij een lege installatie, dus dit moet de migratie doen."""
    Database(legacy).initialize()
    assert read(legacy, "SELECT name,type FROM physical_devices WHERE id='ont-01'") == [("Glasvezel-ONT", "ont")]
    assert len(read(legacy, "SELECT id FROM ports WHERE physical_device_id='ont-01'")) == 1


def test_migrating_twice_changes_nothing(legacy: Path) -> None:
    Database(legacy).initialize()
    before = read(legacy, "SELECT id,name,type FROM physical_devices ORDER BY id")
    Database(legacy).initialize()
    assert read(legacy, "SELECT id,name,type FROM physical_devices ORDER BY id") == before
    assert read(legacy, "PRAGMA user_version") == [(SCHEMA_VERSION,)]


def test_a_fresh_database_is_stamped_without_running_migrations(tmp_path: Path) -> None:
    """Verse databases krijgen alles uit SCHEMA; migraties zouden daar stuklopen."""
    path = tmp_path / "nieuw.db"
    Database(path).initialize()
    assert read(path, "PRAGMA user_version") == [(SCHEMA_VERSION,)]


def test_a_newer_database_is_refused_instead_of_damaged(tmp_path: Path) -> None:
    """Terugrollen naar een oudere image mag geen halve migratie opleveren."""
    path = tmp_path / "toekomst.db"
    Database(path).initialize()
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 5}")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="nieuwere versie"):
        Database(path).initialize()
