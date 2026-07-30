from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS physical_devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ports (
  id TEXT PRIMARY KEY,
  physical_device_id TEXT NOT NULL REFERENCES physical_devices(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  speed_mbps INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE(physical_device_id, number)
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'device',
  origin TEXT NOT NULL CHECK(origin IN ('manual','discovered')),
  status TEXT NOT NULL DEFAULT 'unknown',
  status_updated_at TEXT,
  ip_address TEXT,
  mac_address TEXT,
  hostname TEXT,
  parent_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  ignored INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS entities_mac_unique
ON entities(lower(mac_address)) WHERE mac_address IS NOT NULL AND mac_address != '';

CREATE TABLE IF NOT EXISTS port_assignments (
  port_id TEXT PRIMARY KEY REFERENCES ports(id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE RESTRICT,
  cable_label TEXT NOT NULL DEFAULT '',
  cable_color TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  updated_by TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
  config_json TEXT NOT NULL DEFAULT '{}',
  last_run_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_secrets (
  provider_id TEXT PRIMARY KEY REFERENCES providers(id) ON DELETE CASCADE,
  encrypted_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_records (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE(provider_id, external_id)
);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  value_json TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  expires_at TEXT,
  confidence REAL NOT NULL DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS observations_entity_field
ON observations(entity_id, field, observed_at DESC);

CREATE TABLE IF NOT EXISTS conflicts (
  id TEXT PRIMARY KEY,
  entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  provider_id TEXT REFERENCES providers(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  manual_value TEXT,
  observed_value TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_nodes (
  id TEXT PRIMARY KEY,
  reference_type TEXT NOT NULL CHECK(reference_type IN ('special','physical','entity','group')),
  reference_id TEXT,
  label TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  node_type TEXT NOT NULL DEFAULT 'device',
  parent_node_id TEXT REFERENCES topology_nodes(id) ON DELETE SET NULL,
  parent_source TEXT NOT NULL DEFAULT 'auto' CHECK(parent_source IN ('auto','manual')),
  lifecycle TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active','planned','phase_out')),
  x REAL,
  y REAL,
  width REAL,
  height REAL,
  manual_position INTEGER NOT NULL DEFAULT 0,
  collapsed INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(reference_type, reference_id)
);

CREATE TABLE IF NOT EXISTS topology_relations (
  id TEXT PRIMARY KEY,
  from_node_id TEXT NOT NULL REFERENCES topology_nodes(id) ON DELETE CASCADE,
  to_node_id TEXT NOT NULL REFERENCES topology_nodes(id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK(source IN ('patch','auto','manual')),
  locked INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(from_node_id,to_node_id,relation_type,source)
);

CREATE TABLE IF NOT EXISTS topology_history (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dns_records (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  record_type TEXT NOT NULL DEFAULT 'A',
  value TEXT NOT NULL,
  ttl INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL CHECK(source IN ('manual','adguard')),
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  external_id TEXT,
  entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  manual_locked INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider_id, external_id)
);

CREATE TABLE IF NOT EXISTS proxy_hosts (
  id TEXT PRIMARY KEY,
  domains_json TEXT NOT NULL,
  forward_scheme TEXT NOT NULL DEFAULT 'http',
  forward_host TEXT NOT NULL,
  forward_port INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL CHECK(source IN ('manual','nginx_proxy_manager')),
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  external_id TEXT,
  entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  service_entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider_id, external_id)
);

CREATE TABLE IF NOT EXISTS speedtest_settings (
  id INTEGER PRIMARY KEY CHECK(id=1),
  enabled INTEGER NOT NULL DEFAULT 1,
  interval_seconds INTEGER NOT NULL DEFAULT 21600,
  server_id TEXT,
  interface_name TEXT,
  duration_seconds INTEGER NOT NULL DEFAULT 10,
  telemetry_enabled INTEGER NOT NULL DEFAULT 0,
  last_run_at TEXT,
  last_error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speedtest_runs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
  download_mbps REAL,
  upload_mbps REAL,
  ping_ms REAL,
  jitter_ms REAL,
  server_name TEXT,
  server_id TEXT,
  isp TEXT,
  public_ip TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT
);
"""


DEVICE_TEMPLATES = [
    ("switch-01", "TP-Link SG108E 01", "switch", "TP-Link SG108E", 8, [1000] * 8),
    ("switch-02", "TP-Link SG108E 02", "switch", "TP-Link SG108E", 8, [1000] * 8),
    ("deco-01", "Deco XE75 Pro 01", "mesh_ap", "TP-Link Deco XE75 Pro", 3, [2500, 1000, 1000]),
    ("deco-02", "Deco XE75 Pro 02", "mesh_ap", "TP-Link Deco XE75 Pro", 3, [2500, 1000, 1000]),
    ("deco-03", "Deco XE75 Pro 03", "mesh_ap", "TP-Link Deco XE75 Pro", 3, [2500, 1000, 1000]),
]

PROVIDER_TEMPLATES = [
    ("dhcp-arp", "dhcp_arp", "DHCP / ARP discovery", 300, {"subnets": ["192.168.1.0/24"], "scan": True}),
    ("uptime-kuma", "uptime_kuma", "Uptime Kuma", 60, {"base_url": "", "status_page_slug": "homelab"}),
    ("glances", "glances", "Glances", 60, {"endpoints": [{"name": "docker-vm", "url": "http://192.168.1.12:61208/api/4"}]}),
    ("portainer", "portainer", "Portainer", 60, {"base_url": "https://192.168.1.12:9443", "verify_tls": False}),
    ("proxmox", "proxmox", "Proxmox VE", 60, {"base_url": "https://192.168.1.100:8006", "user": "readonly@pve", "token_name": "patchmanager", "verify_tls": False}),
    ("adguard", "adguard", "AdGuard Home", 300, {"base_url": "http://192.168.1.12:3000", "import_clients": True, "import_rewrites": True, "verify_tls": True}),
    ("nginx-proxy-manager", "nginx_proxy_manager", "Nginx Proxy Manager", 300, {"base_url": "http://192.168.1.12:81", "verify_tls": True}),
]


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(SCHEMA)
            entity_columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)").fetchall()}
            if "ignored" not in entity_columns:
                connection.execute("ALTER TABLE entities ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")
            if "archived" not in entity_columns:
                connection.execute("ALTER TABLE entities ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            inventory_seeded = connection.execute(
                "SELECT value FROM app_meta WHERE key='physical_inventory_seeded'"
            ).fetchone()
            if not inventory_seeded:
                # Existing installations already contain the starter inventory.
                # Mark it as seeded so deliberately deleted templates never return.
                if connection.execute("SELECT COUNT(*) FROM physical_devices").fetchone()[0] == 0:
                    self._seed_physical_devices(connection)
                connection.execute(
                    "INSERT INTO app_meta(key,value) VALUES('physical_inventory_seeded','1')"
                )
            self._seed_providers(connection)
            self._seed_topology(connection)
            connection.execute(
                """INSERT OR IGNORE INTO speedtest_settings
                   (id,enabled,interval_seconds,duration_seconds,telemetry_enabled,updated_at)
                   VALUES(1,1,21600,10,0,?)""",
                (utcnow(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_meta(key,value) VALUES('app_title','Network Patch Manager')"
            )

    def _seed_physical_devices(self, connection: sqlite3.Connection) -> None:
        now = utcnow()
        for position, (device_id, name, kind, model, count, speeds) in enumerate(DEVICE_TEMPLATES):
            connection.execute(
                """INSERT OR IGNORE INTO physical_devices
                   (id,name,type,model,position,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                (device_id, name, kind, model, position, now, now),
            )
            for number in range(1, count + 1):
                connection.execute(
                    """INSERT OR IGNORE INTO ports
                       (id,physical_device_id,number,label,speed_mbps) VALUES(?,?,?,?,?)""",
                    (f"{device_id}-p{number}", device_id, number, f"Poort {number}", speeds[number - 1]),
                )

    def _seed_providers(self, connection: sqlite3.Connection) -> None:
        now = utcnow()
        for provider_id, kind, name, interval, config in PROVIDER_TEMPLATES:
            connection.execute(
                """INSERT OR IGNORE INTO providers
                   (id,type,name,enabled,poll_interval_seconds,config_json,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (provider_id, kind, name, 0, interval, json.dumps(config), now),
            )

    def _seed_topology(self, connection: sqlite3.Connection) -> None:
        now = utcnow()
        connection.execute(
            """INSERT OR IGNORE INTO topology_nodes
               (id,reference_type,reference_id,label,subtitle,node_type,x,y,width,height,manual_position,created_at,updated_at)
               VALUES('special:internet','special','internet','internet','','external',510,24,150,52,1,?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT OR IGNORE INTO topology_nodes
               (id,reference_type,reference_id,label,subtitle,node_type,x,y,width,height,manual_position,created_at,updated_at)
               VALUES('special:router','special','router','router','Deco XE75 Pro','router',490,116,190,62,1,?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT OR IGNORE INTO topology_relations
               (id,from_node_id,to_node_id,relation_type,label,source,locked,created_at,updated_at)
               VALUES('manual:internet-router','special:internet','special:router','uplink','','manual',1,?,?)""",
            (now, now),
        )

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row else None

    def audit(self, user_id: str | None, action: str, target_type: str, target_id: str | None, details: dict[str, Any] | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, action, target_type, target_id, json.dumps(details or {}), utcnow()),
            )

    def clean_sessions(self) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))

    def create_backup(self, backup_dir: Path) -> Path:
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"patch-manager-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}.db"
        source = self.connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Back-up integriteitscontrole is mislukt")
        finally:
            destination.close()
            source.close()
        return target

    def restore_backup(self, source_path: Path) -> None:
        source = sqlite3.connect(source_path)
        destination = self.connect()
        try:
            result = source.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Back-up is geen geldige SQLite-database")
            required = {"users", "physical_devices", "entities", "providers"}
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required.issubset(tables):
                raise RuntimeError("Back-up mist vereiste Patch Manager-tabellen")
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        self.initialize()

    @staticmethod
    def prune_backups(backup_dir: Path, keep: int) -> None:
        backups = sorted(backup_dir.glob("patch-manager-*.db"), reverse=True)
        for stale in backups[keep:]:
            stale.unlink(missing_ok=True)


def expires_in(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
