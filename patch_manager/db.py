from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Harde retentiegrenzen: bewust vast in code, niet instelbaar. Bij ~40 entities
# blijft dit onder ~6 MB, ongeacht hoe lang de app draait.
SAMPLE_MINUTES = 5
SAMPLE_RETENTION_HOURS = 48
DAY_RETENTION_DAYS = 730
AUDIT_RETENTION_ROWS = 5000


def _as_number(value: Any) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


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
  side TEXT NOT NULL DEFAULT 'front' CHECK(side IN ('front','rear')),
  peer_port_id TEXT REFERENCES ports(id) ON DELETE SET NULL,
  label TEXT NOT NULL DEFAULT '',
  speed_mbps INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE(physical_device_id, number, side)
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
  vendor TEXT,
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

CREATE TABLE IF NOT EXISTS cables (
  id TEXT PRIMARY KEY,
  a_port_id TEXT NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
  b_port_id TEXT REFERENCES ports(id) ON DELETE CASCADE,
  b_entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  updated_by TEXT REFERENCES users(id),
  CHECK ((b_port_id IS NULL) != (b_entity_id IS NULL)),
  CHECK (b_port_id IS NULL OR a_port_id != b_port_id)
);

-- Eén kabel per poort en (bewust) één kabel per entity; "poort bezet?" moet in
-- code beide poortkolommen controleren, deze indexen zijn het vangnet per kolom.
CREATE UNIQUE INDEX IF NOT EXISTS cables_a_port ON cables(a_port_id);
CREATE UNIQUE INDEX IF NOT EXISTS cables_b_port ON cables(b_port_id) WHERE b_port_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cables_b_entity ON cables(b_entity_id) WHERE b_entity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS entity_samples (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  sampled_at TEXT NOT NULL,
  status TEXT NOT NULL,
  cpu_percent REAL,
  memory_percent REAL,
  latency_ms REAL,
  PRIMARY KEY(entity_id, sampled_at)
);

CREATE TABLE IF NOT EXISTS entity_days (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  samples_total INTEGER NOT NULL DEFAULT 0,
  samples_up INTEGER NOT NULL DEFAULT 0,
  flips INTEGER NOT NULL DEFAULT 0,
  last_change_at TEXT,
  PRIMARY KEY(entity_id, day)
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
                """INSERT INTO audit_log(id,actor_user_id,action,target_type,target_id,details_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), user_id, action, target_type, target_id, json.dumps(details or {}), utcnow()),
            )

    def clean_sessions(self) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))

    def clean_observations(self) -> None:
        """Remove expired samples and collapse historical duplicates per source field."""
        with self.transaction() as connection:
            connection.execute("DELETE FROM observations WHERE expires_at IS NOT NULL AND expires_at <= ?", (utcnow(),))
            connection.execute(
                """DELETE FROM observations
                   WHERE id IN (
                     SELECT id FROM (
                       SELECT id,ROW_NUMBER() OVER (
                         PARTITION BY entity_id,provider_id,field
                         ORDER BY observed_at DESC,id DESC
                       ) AS position
                       FROM observations
                     ) WHERE position > 1
                   )"""
            )

    @staticmethod
    def current_slot() -> str:
        now = datetime.now(UTC)
        return now.replace(minute=now.minute - now.minute % SAMPLE_MINUTES, second=0, microsecond=0).isoformat(timespec="seconds")

    def sample_slot_due(self) -> bool:
        """Staat er nog niets in het huidige 5-minutenvak?

        De onderhoudslus draait elke 30 seconden; zonder deze check zouden we
        tien keer per vak alle providerdata parsen om één rij te schrijven.
        """
        row = self.fetch_one("SELECT 1 AS n FROM entity_samples WHERE sampled_at=? LIMIT 1", (self.current_slot(),))
        return row is None

    def record_samples(self, metrics: dict[str, dict[str, Any]] | None = None) -> int:
        """Leg één meetpunt per entity vast op het 5-minutenraster.

        Fijne samples leven 48 uur, daarboven blijft alleen het dagtotaal over.
        Zo groeit de historie nooit voorbij het budget in docs/ontwerp-visualisatie.md.
        """
        metrics = metrics or {}
        stamp = self.current_slot()
        day = stamp[:10]
        written = 0
        with self.transaction() as connection:
            entities = connection.execute("SELECT id,status FROM entities").fetchall()
            for entity in entities:
                entity_metrics = metrics.get(entity["id"]) or {}
                memory_percent = None
                used, total = entity_metrics.get("memory_used"), entity_metrics.get("memory_total")
                if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total:
                    memory_percent = round(used / total * 100, 1)
                cursor = connection.execute(
                    """INSERT INTO entity_samples(entity_id,sampled_at,status,cpu_percent,memory_percent,latency_ms)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(entity_id,sampled_at) DO NOTHING""",
                    (entity["id"], stamp, entity["status"], _as_number(entity_metrics.get("cpu_percent")),
                     memory_percent, _as_number(entity_metrics.get("latency_ms"))),
                )
                if not cursor.rowcount:
                    continue
                written += 1
                previous = connection.execute(
                    """SELECT status FROM entity_samples WHERE entity_id=? AND sampled_at<?
                       ORDER BY sampled_at DESC LIMIT 1""",
                    (entity["id"], stamp),
                ).fetchone()
                flipped = int(bool(previous) and previous["status"] != entity["status"])
                connection.execute(
                    """INSERT INTO entity_days(entity_id,day,samples_total,samples_up,flips,last_change_at)
                       VALUES(?,?,1,?,?,?)
                       ON CONFLICT(entity_id,day) DO UPDATE SET
                         samples_total=samples_total+1,
                         samples_up=samples_up+excluded.samples_up,
                         flips=flips+excluded.flips,
                         last_change_at=CASE WHEN excluded.flips=1 THEN excluded.last_change_at ELSE last_change_at END""",
                    (entity["id"], day, int(entity["status"] == "up"), flipped, stamp if flipped else None),
                )
        return written

    def prune_history(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(hours=SAMPLE_RETENTION_HOURS)).isoformat(timespec="seconds")
        day_cutoff = (datetime.now(UTC) - timedelta(days=DAY_RETENTION_DAYS)).date().isoformat()
        with self.transaction() as connection:
            connection.execute("DELETE FROM entity_samples WHERE sampled_at < ?", (cutoff,))
            connection.execute("DELETE FROM entity_days WHERE day < ?", (day_cutoff,))
            # Het auditlog groeit anders onbegrensd; de UI toont er 200.
            connection.execute(
                """DELETE FROM audit_log WHERE id IN (
                     SELECT id FROM audit_log ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?
                   )""",
                (AUDIT_RETENTION_ROWS,),
            )

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

    def create_backup_bundle(self, backup_dir: Path, secret_key_path: Path) -> Path:
        """Create a portable backup containing both SQLite data and its encryption key."""
        if not secret_key_path.is_file():
            raise RuntimeError("De sleutel voor providergegevens ontbreekt")
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"patch-manager-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}.pmbackup"
        try:
            with tempfile.TemporaryDirectory(prefix="patch-manager-backup-", dir=backup_dir) as temporary:
                database_copy = self.create_backup(Path(temporary))
                manifest = {
                    "format": "plugnet-backup",
                    "version": 1,
                    "created_at": utcnow(),
                }
                with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(database_copy, "database.db")
                    archive.write(secret_key_path, "provider-secrets.key")
                    archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            os.chmod(target, 0o600)
            self.validate_backup(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    @staticmethod
    def _validate_sqlite(source_path: Path) -> None:
        with sqlite3.connect(source_path) as source:
            result = source.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Back-up is geen geldige SQLite-database")
            required = {"users", "physical_devices", "entities", "providers"}
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required.issubset(tables):
                raise RuntimeError("Back-up mist vereiste Patch Manager-tabellen")

    @classmethod
    def validate_backup(cls, source_path: Path) -> dict[str, Any]:
        if not zipfile.is_zipfile(source_path):
            cls._validate_sqlite(source_path)
            return {"format": "legacy-sqlite", "portable": False}
        with zipfile.ZipFile(source_path) as archive:
            names = set(archive.namelist())
            required = {"database.db", "provider-secrets.key", "manifest.json"}
            if not required.issubset(names):
                raise RuntimeError("Back-uppakket mist database, sleutel of manifest")
            if any(archive.namelist().count(name) != 1 for name in required):
                raise RuntimeError("Back-uppakket bevat dubbele essentiële bestanden")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise RuntimeError("Back-uppakket bevat onveilige paden")
            if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
                raise RuntimeError("Uitgepakte back-up is groter dan 512 MB")
            if archive.getinfo("manifest.json").file_size > 64 * 1024 or archive.getinfo("provider-secrets.key").file_size > 1024:
                raise RuntimeError("Back-upmanifest of sleutel is ongeldig groot")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "plugnet-backup" or manifest.get("version") != 1:
                raise RuntimeError("Back-uppakket heeft een onbekend formaat")
            key = archive.read("provider-secrets.key").strip()
            cipher = Fernet(key)
            # Uitpakken naast het bronbestand: /tmp is in de container een kleine
            # tmpfs, terwijl bundels tot 512 MB database mogen bevatten.
            with tempfile.TemporaryDirectory(prefix="patch-manager-validate-", dir=source_path.parent) as temporary:
                database_copy = Path(temporary) / "database.db"
                with archive.open("database.db") as source, database_copy.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                cls._validate_sqlite(database_copy)
                with sqlite3.connect(database_copy) as source:
                    has_secrets = source.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_secrets'"
                    ).fetchone()
                    rows = source.execute("SELECT encrypted_json FROM provider_secrets").fetchall() if has_secrets else []
                    try:
                        for row in rows:
                            cipher.decrypt(row[0].encode())
                    except InvalidToken as exc:
                        raise RuntimeError("Back-upsleutel past niet bij de providergegevens") from exc
        return {"format": "plugnet-backup", "portable": True, "manifest": manifest}

    def _restore_sqlite(self, source_path: Path) -> None:
        self._validate_sqlite(source_path)
        source = sqlite3.connect(source_path)
        destination = self.connect()
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        self.initialize()

    def restore_backup(self, source_path: Path, secret_key_path: Path | None = None) -> bool:
        """Restore a portable bundle, or a legacy SQLite backup using the current key."""
        metadata = self.validate_backup(source_path)
        if metadata["format"] == "legacy-sqlite":
            if secret_key_path and secret_key_path.is_file():
                cipher = Fernet(secret_key_path.read_bytes().strip())
                with sqlite3.connect(source_path) as source:
                    has_secrets = source.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_secrets'"
                    ).fetchone()
                    rows = source.execute("SELECT encrypted_json FROM provider_secrets").fetchall() if has_secrets else []
                    try:
                        for row in rows:
                            cipher.decrypt(row[0].encode())
                    except InvalidToken as exc:
                        raise RuntimeError(
                            "Deze oude SQLite-back-up hoort bij een andere encryptiesleutel; importeer een .pmbackup-bestand"
                        ) from exc
            self._restore_sqlite(source_path)
            return False
        if secret_key_path is None:
            raise RuntimeError("Voor dit back-uppakket is een sleutelpad vereist")
        with zipfile.ZipFile(source_path) as archive, tempfile.TemporaryDirectory(
            prefix="patch-manager-restore-", dir=source_path.parent
        ) as temporary:
            database_copy = Path(temporary) / "database.db"
            with archive.open("database.db") as source, database_copy.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            secret_key_path.parent.mkdir(parents=True, exist_ok=True)
            key_copy = secret_key_path.with_name(f".{secret_key_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                key_copy.write_bytes(archive.read("provider-secrets.key").strip() + b"\n")
                os.chmod(key_copy, 0o600)
                self._restore_sqlite(database_copy)
                os.replace(key_copy, secret_key_path)
                os.chmod(secret_key_path, 0o600)
            finally:
                key_copy.unlink(missing_ok=True)
        return True

    @staticmethod
    def prune_backups(backup_dir: Path, keep: int) -> None:
        backups = sorted(
            [*backup_dir.glob("patch-manager-*.pmbackup"), *backup_dir.glob("patch-manager-*.db")],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[keep:]:
            stale.unlink(missing_ok=True)


def expires_in(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
