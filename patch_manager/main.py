from __future__ import annotations

import asyncio
import csv
import hmac
import io
import ipaddress
import json
import logging
import socket
import sqlite3
import time
import uuid
import zipfile
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__, categories
from .db import PROVIDER_TEMPLATES, Database, expires_in, utcnow
from .providers import ProviderManager, normalize_mac
from .secret_store import SecretStore
from .security import hash_password, new_token, token_digest, verify_password
from .settings import get_settings
from .speedtest import SpeedtestManager
from .topology import (
    capture_topology,
    create_group,
    create_relation,
    entity_metrics,
    topology_payload,
    trace_from_port,
    undo_topology,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
database = Database(settings.database_path)
SECRET_KEY_PATH = settings.data_dir / "provider-secrets.key"
provider_secrets = SecretStore(database, SECRET_KEY_PATH)
providers = ProviderManager(database, provider_secrets, settings.trusted_subnets)
speedtests = SpeedtestManager(database)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_COOKIE = "plugnet_session"
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 300
BACKUP_IMPORT_MAX_BYTES = 512 * 1024 * 1024
DUMMY_PASSWORD_HASH = hash_password("timing-equalizer-not-a-real-password")
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def create_portable_backup() -> Path:
    return database.create_backup_bundle(settings.backup_dir, SECRET_KEY_PATH)


@dataclass
class AuthContext:
    user_id: str
    username: str
    csrf_token: str
    session_token: str


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class TrimmedInput(BaseModel):
    """Trim zichtbare tekst vóór lengtevalidatie en opslag.

    Zonder deze configuratie voldoet een reeks spaties aan ``min_length`` en
    wordt hij pas later als lege string opgeslagen.
    """

    model_config = ConfigDict(str_strip_whitespace=True)


class UplinkInput(TrimmedInput):
    """Aan welk netwerkapparaat iets hangt zonder dat er een poort bij hoort."""

    physical_device_id: str | None = Field(default=None, max_length=64)


class EntityInput(TrimmedInput):
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="device", max_length=40)
    ip_address: str | None = Field(default=None, max_length=64)
    mac_address: str | None = Field(default=None, max_length=32)
    hostname: str | None = Field(default=None, max_length=255)
    notes: str = Field(default="", max_length=2000)

    @field_validator("mac_address", mode="before")
    @classmethod
    def validate_mac_address(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        normalized = normalize_mac(str(value))
        if normalized is None:
            raise ValueError("MAC-adres moet exact het formaat aa:bb:cc:dd:ee:ff hebben")
        return normalized


class PhysicalDeviceInput(TrimmedInput):
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="switch", max_length=40)
    model: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)
    ports: int = Field(default=1, ge=1, le=96)
    # De observatie die vertelt of dit apparaat aan staat; een switch draait
    # geen agent maar is wel te pingen of te monitoren.
    monitor_entity_id: str | None = Field(default=None, max_length=64)


class CableInput(TrimmedInput):
    a_port_id: str
    b_port_id: str | None = None
    b_entity_id: str | None = None
    label: str = Field(default="", max_length=100)
    color: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=2000)


class PortCableInput(TrimmedInput):
    """Zelfde als CableInput, maar de a-poort komt uit het pad."""
    b_port_id: str | None = None
    b_entity_id: str | None = None
    move_cable_id: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=100)
    color: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=2000)


class PortInput(TrimmedInput):
    label: str = Field(default="", max_length=100)
    speed_mbps: int | None = Field(default=None, ge=10, le=100000)
    notes: str = Field(default="", max_length=2000)


class ProviderInput(TrimmedInput):
    # Met twee Portainers moet je ze uit elkaar kunnen houden.
    name: str | None = Field(default=None, max_length=100)
    enabled: bool
    poll_interval_seconds: int = Field(ge=15, le=86400)
    config: dict[str, Any]
    credentials: dict[str, str | None] = Field(default_factory=dict)
    clear_credentials: list[str] = Field(default_factory=list)


class AppLinkInput(TrimmedInput):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=200)
    icon: str = Field(default="", max_length=8)
    group_name: str = Field(default="", max_length=60)
    monitor_entity_id: str | None = Field(default=None, max_length=64)
    position: int = Field(default=0, ge=0, le=999)


class ProviderCreateInput(TrimmedInput):
    """Een tweede omgeving van een soort die je al hebt."""

    type: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)


class AppSettingsInput(TrimmedInput):
    title: str = Field(min_length=2, max_length=80)


class ConflictInput(TrimmedInput):
    resolution: str = Field(min_length=2, max_length=100)


class TopologyNodeInput(TrimmedInput):
    label: str = Field(min_length=1, max_length=100)
    subtitle: str = Field(default="", max_length=150)
    parent_node_id: str | None = None
    lifecycle: str = Field(default="active", pattern="^(active|planned|phase_out)$")
    x: float | None = None
    y: float | None = None
    collapsed: bool = False
    hidden: bool = False


class TopologyPositionInput(TrimmedInput):
    x: float
    y: float


class TopologyPositionsInput(TrimmedInput):
    positions: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class TopologyGroupSelectionInput(TrimmedInput):
    node_ids: list[str] = Field(min_length=1, max_length=200)
    parent_node_id: str


class TopologyGroupInput(TrimmedInput):
    label: str = Field(min_length=1, max_length=100)
    subtitle: str = Field(default="", max_length=150)
    node_ids: list[str] = Field(default_factory=list, max_length=200)


class TopologyRelationInput(TrimmedInput):
    from_node_id: str
    to_node_id: str
    relation_type: str = Field(default="dependency", pattern="^(dependency|network|service|uplink)$")
    label: str = Field(default="", max_length=100)


class SpeedtestSettingsInput(TrimmedInput):
    enabled: bool
    interval_seconds: int = Field(ge=900, le=86400 * 7)
    server_id: str | None = Field(default=None, max_length=80)
    interface_name: str | None = Field(default=None, max_length=80)
    duration_seconds: int = Field(default=10, ge=5, le=30)


class DnsRecordInput(TrimmedInput):
    name: str = Field(min_length=1, max_length=253)
    record_type: str = Field(default="A", pattern="^(A|AAAA|CNAME)$")
    value: str = Field(min_length=1, max_length=253)
    ttl: int | None = Field(default=None, ge=30, le=86400)
    enabled: bool = True
    entity_id: str | None = None


class DiscoveryStateInput(TrimmedInput):
    ignored: bool = False
    archived: bool = False


class EntityMergeInput(TrimmedInput):
    target_entity_id: str


class ProviderMappingInput(TrimmedInput):
    entity_id: str | None = None


class ProviderTestInput(TrimmedInput):
    config: dict[str, Any]
    credentials: dict[str, str | None] = Field(default_factory=dict)


class PromoteInput(TrimmedInput):
    name: str | None = Field(default=None, max_length=100)
    type: str | None = Field(default=None, max_length=40)


class WizardStateInput(TrimmedInput):
    dismissed: bool


PROVIDER_CREDENTIAL_FIELDS: dict[str, list[dict[str, str]]] = {
    "glances": [
        {"key": "username", "label": "Gebruikersnaam", "type": "text"},
        {"key": "password", "label": "Wachtwoord", "type": "password"},
    ],
    "adguard": [
        {"key": "username", "label": "Gebruikersnaam", "type": "text"},
        {"key": "password", "label": "Wachtwoord", "type": "password"},
    ],
    "nginx_proxy_manager": [
        {"key": "token", "label": "API-token", "type": "password"},
        {"key": "identity", "label": "Gebruikersnaam/e-mail", "type": "text"},
        {"key": "secret", "label": "Wachtwoord", "type": "password"},
    ],
    "portainer": [
        {"key": "api_key", "label": "API-key", "type": "password"},
    ],
    "proxmox": [
        {"key": "token_secret", "label": "API-tokengeheim", "type": "password"},
    ],
}

# Configuratie die de wizard náást de basis-URL moet uitvragen. Zonder dit
# stuurt de wizard de sjabloonwaarden uit db.py mee en krijg je een 401 op een
# token dat je nooit hebt kunnen invullen. Providers die genoeg hebben aan een
# URL staan hier niet in.
PROVIDER_CONFIG_FIELDS: dict[str, list[dict[str, str]]] = {
    "proxmox": [
        {"key": "user", "label": "Gebruiker (user@realm)", "placeholder": "root@pam"},
        {"key": "token_name", "label": "Token-ID (deel na de !)", "placeholder": "patchmanager"},
    ],
    "uptime_kuma": [
        {"key": "status_page_slug", "label": "Statuspagina-slug", "placeholder": "homelab"},
    ],
}


def app_title() -> str:
    row = database.fetch_one("SELECT value FROM app_meta WHERE key='app_title'")
    return row["value"] if row else "Network Patch Manager"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.session_secure,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )


def create_session(user_id: str) -> tuple[str, str]:
    token = new_token()
    csrf = new_token()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)",
            (token_digest(token), user_id, csrf, expires_in(), utcnow()),
        )
    return token, csrf


def current_auth(plugnet_session: str | None = Cookie(default=None)) -> AuthContext:
    if not plugnet_session:
        raise HTTPException(401, "Log eerst in")
    row = database.fetch_one(
        """SELECT s.user_id,s.csrf_token,s.expires_at,u.username
           FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""",
        (token_digest(plugnet_session),),
    )
    if not row or row["expires_at"] < utcnow():
        raise HTTPException(401, "Sessie is verlopen")
    return AuthContext(row["user_id"], row["username"], row["csrf_token"], plugnet_session)


def write_auth(
    auth: AuthContext = Depends(current_auth),
    x_csrf_token: str | None = Header(default=None),
) -> AuthContext:
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, auth.csrf_token):
        raise HTTPException(403, "Ongeldig CSRF-token")
    return auth


def _prune_login_attempts() -> None:
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    for key in list(LOGIN_ATTEMPTS):
        recent = [stamp for stamp in LOGIN_ATTEMPTS[key] if stamp >= cutoff]
        if recent:
            LOGIN_ATTEMPTS[key] = recent
        else:
            del LOGIN_ATTEMPTS[key]


def _maintenance_housekeeping() -> list[dict[str, Any]]:
    """Run blocking SQLite housekeeping outside the event loop."""
    database.clean_sessions()
    _prune_login_attempts()
    # Metrics parsen is het duurste deel; alleen doen als er echt een nieuw
    # meetpunt aan de beurt is (elke 5 minuten, niet elke 30 seconden).
    if database.sample_slot_due():
        database.clean_observations()
        database.record_samples(entity_metrics(database))
        database.prune_history()
    with database.transaction() as connection:
        connection.execute(
            """UPDATE entities SET status='unknown',status_updated_at=?
               WHERE status!='unknown' AND NOT EXISTS (
                 SELECT 1 FROM observations o
                 WHERE o.entity_id=entities.id AND o.field='status' AND o.expires_at>?
               )""",
            (utcnow(), utcnow()),
        )
    return database.fetch_all("SELECT id,poll_interval_seconds,last_run_at FROM providers WHERE enabled=1")


async def maintenance_loop() -> None:
    last_backup_date = None
    while True:
        now = datetime.now()
        try:
            due = await asyncio.to_thread(_maintenance_housekeeping)
            for provider in due:
                last = datetime.fromisoformat(provider["last_run_at"]) if provider["last_run_at"] else None
                if not last or (datetime.now(UTC) - last).total_seconds() >= provider["poll_interval_seconds"]:
                    with suppress(Exception):
                        await providers.sync_one(provider["id"])
            speedtest_settings = await asyncio.to_thread(
                database.fetch_one, "SELECT * FROM speedtest_settings WHERE id=1"
            )
            if speedtest_settings and speedtest_settings["enabled"]:
                last_speedtest = datetime.fromisoformat(speedtest_settings["last_run_at"]) if speedtest_settings["last_run_at"] else None
                if not last_speedtest or (datetime.now(UTC) - last_speedtest).total_seconds() >= speedtest_settings["interval_seconds"]:
                    task = asyncio.create_task(speedtests.run())
                    BACKGROUND_TASKS.add(task)
                    task.add_done_callback(BACKGROUND_TASKS.discard)
            if now.hour == settings.backup_schedule_hour and last_backup_date != now.date():
                await asyncio.to_thread(create_portable_backup)
                await asyncio.to_thread(
                    database.prune_backups, settings.backup_dir, settings.backup_retention_daily
                )
                last_backup_date = now.date()
        except Exception:
            logger.exception("Onderhoudslus is mislukt; nieuwe poging over 30 seconden")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    speedtests.recover_stale_runs()
    task = asyncio.create_task(maintenance_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Network Patch Manager", version=__version__, lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/public/settings")
def public_settings() -> dict[str, str]:
    return {"title": app_title()}


@app.get("/api/auth/status")
def auth_status(plugnet_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user_count = database.fetch_one("SELECT COUNT(*) AS count FROM users")["count"]
    result: dict[str, Any] = {"setup_required": user_count == 0, "authenticated": False}
    if plugnet_session:
        try:
            auth = current_auth(plugnet_session)
            result.update(authenticated=True, username=auth.username, csrf_token=auth.csrf_token)
        except HTTPException:
            pass
    return result


@app.post("/api/auth/setup")
def setup(payload: Credentials, response: Response) -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        with database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise HTTPException(409, "De eerste beheerder bestaat al")
            connection.execute(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                (user_id, payload.username.strip(), password_hash, utcnow()),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "De eerste beheerder bestaat al") from exc
    token, csrf = create_session(user_id)
    set_session_cookie(response, token)
    database.audit(user_id, "auth.setup", "user", user_id)
    return {"username": payload.username.strip(), "csrf_token": csrf}


@app.post("/api/auth/login")
def login(payload: Credentials, request: Request, response: Response) -> dict[str, Any]:
    client_key = request.client.host if request.client else "unknown"
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    attempts = [timestamp for timestamp in LOGIN_ATTEMPTS.get(client_key, []) if timestamp >= cutoff]
    LOGIN_ATTEMPTS[client_key] = attempts
    if len(attempts) >= 5:
        raise HTTPException(429, "Te veel inlogpogingen; probeer het over vijf minuten opnieuw")
    user = database.fetch_one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (payload.username.strip(),))
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    if not verify_password(payload.password, password_hash) or not user:
        attempts.append(time.monotonic())
        raise HTTPException(401, "Onjuiste gebruikersnaam of wachtwoord")
    LOGIN_ATTEMPTS.pop(client_key, None)
    token, csrf = create_session(user["id"])
    set_session_cookie(response, token)
    database.audit(user["id"], "auth.login", "user", user["id"])
    return {"username": user["username"], "csrf_token": csrf}


@app.post("/api/auth/logout")
def logout(response: Response, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(auth.session_token),))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def serialize_provider(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["enabled"] = bool(row["enabled"])
    row["config"] = json.loads(row.pop("config_json") or "{}")
    fields = PROVIDER_CREDENTIAL_FIELDS.get(row["type"], [])
    configured = provider_secrets.get(row["id"])
    row["credential_fields"] = fields
    row["config_fields"] = PROVIDER_CONFIG_FIELDS.get(row["type"], [])
    row["credentials_configured"] = {field["key"]: field["key"] in configured for field in fields}
    return row


def load_cable_context() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    ports = {row["id"]: row for row in database.fetch_all("SELECT * FROM ports")}
    cables = database.fetch_all("SELECT * FROM cables")
    by_port: dict[str, dict[str, Any]] = {}
    for cable in cables:
        by_port[cable["a_port_id"]] = cable
        if cable["b_port_id"]:
            by_port[cable["b_port_id"]] = cable
    return ports, cables, by_port


def port_display_name(port: dict[str, Any], devices_by_id: dict[str, dict[str, Any]]) -> str:
    device = devices_by_id.get(port["physical_device_id"], {})
    side = " (achter)" if port["side"] == "rear" else ""
    return f"{device.get('name', '?')} · poort {port['number']}{side}"


def nested_physical_devices() -> list[dict[str, Any]]:
    devices = database.fetch_all("SELECT * FROM physical_devices ORDER BY position,name")
    devices_by_id = {device["id"]: device for device in devices}
    ports, _cables, by_port = load_cable_context()
    entities = {
        row["id"]: row
        for row in database.fetch_all("SELECT id,name,type,status,ip_address,hostname FROM entities")
    }
    by_device: dict[str, list[dict[str, Any]]] = {}
    for port in sorted(ports.values(), key=lambda item: (item["physical_device_id"], item["side"], item["number"])):
        cable = by_port.get(port["id"])
        port["cable_id"] = cable["id"] if cable else None
        port["cable_label"] = cable["label"] if cable else ""
        port["cable_color"] = cable["color"] if cable else ""
        port["cable_notes"] = cable["notes"] if cable else ""
        port["link_kind"] = None
        port["target_port_id"] = None
        port["target_port_label"] = None
        entity = None
        if cable:
            if cable["b_entity_id"] and cable["a_port_id"] == port["id"]:
                port["link_kind"] = "entity"
                entity = entities.get(cable["b_entity_id"])
            else:
                port["link_kind"] = "port"
                other = cable["b_port_id"] if cable["a_port_id"] == port["id"] else cable["a_port_id"]
                port["target_port_id"] = other
                if other in ports:
                    port["target_port_label"] = port_display_name(ports[other], devices_by_id)
                _, final_entity_id = trace_from_port(port["id"], ports, by_port)
                entity = entities.get(final_entity_id) if final_entity_id else None
        port["entity_id"] = entity["id"] if entity else None
        port["entity_name"] = entity["name"] if entity else None
        port["entity_type"] = entity["type"] if entity else None
        port["entity_status"] = entity["status"] if entity else None
        port["ip_address"] = entity["ip_address"] if entity else None
        port["hostname"] = entity["hostname"] if entity else None
        by_device.setdefault(port["physical_device_id"], []).append(port)
    monitors = {
        row["id"]: row
        for row in database.fetch_all("SELECT id,name,status,status_updated_at FROM entities")
    }
    for device in devices:
        device["ports"] = by_device.get(device["id"], [])
        # Een netwerkapparaat heeft geen eigen status; die leent het van de
        # observatie die erover gaat. Zonder koppeling blijft dat 'unknown'.
        monitor = monitors.get(device["monitor_entity_id"])
        device["status"] = monitor["status"] if monitor else "unknown"
        device["status_updated_at"] = monitor["status_updated_at"] if monitor else None
        device["monitor_name"] = monitor["name"] if monitor else None
    return devices


def inventory_counts() -> dict[str, Any]:
    linked = {
        row["b_entity_id"]
        for row in database.fetch_all("SELECT b_entity_id FROM cables WHERE b_entity_id IS NOT NULL")
    }
    open_discoveries = database.fetch_all(
        """SELECT id,uplink_device_id,
                  (SELECT COUNT(*) FROM physical_devices d WHERE d.monitor_entity_id=entities.id) AS monitors
           FROM entities WHERE origin='discovered' AND ignored=0 AND archived=0"""
    )
    return {
        "ports": database.fetch_one("SELECT COUNT(*) AS n FROM ports WHERE side='front'")["n"],
        "patched": database.fetch_one("SELECT COUNT(*) AS n FROM cables")["n"],
        "up": database.fetch_one("SELECT COUNT(*) AS n FROM entities WHERE status='up'")["n"],
        "down": database.fetch_one("SELECT COUNT(*) AS n FROM entities WHERE status='down'")["n"],
        # Een monitor die de status van een netwerkapparaat levert heeft ook
        # een plek gekregen, al hangt hij nergens aan een poort.
        "unlinked": sum(
            1 for row in open_discoveries
            if row["id"] not in linked and not row["uplink_device_id"] and not row["monitors"]
        ),
        "conflicts": database.fetch_one("SELECT COUNT(*) AS n FROM conflicts WHERE status='open'")["n"],
    }


@app.get("/api/bootstrap")
def bootstrap(_: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    entities = database.fetch_all("SELECT * FROM entities ORDER BY origin,name")
    provider_rows = [serialize_provider(row) for row in database.fetch_all("SELECT * FROM providers ORDER BY name")]
    conflicts = database.fetch_all(
        """SELECT c.*,e.name AS entity_name,p.name AS provider_name FROM conflicts c
           LEFT JOIN entities e ON e.id=c.entity_id LEFT JOIN providers p ON p.id=c.provider_id
           ORDER BY c.status,c.created_at DESC"""
    )
    counts = inventory_counts()
    return {
        "site": {"title": app_title(), "timezone": "Europe/Amsterdam"},
        "categories": categories.payload(),
        "app_links": serialize_app_links(),
        "counts": counts,
        "physical_devices": nested_physical_devices(),
        "entities": entities,
        "providers": provider_rows,
        "conflicts": conflicts,
        "backups": list_backups(),
        "topology": topology_payload(database),
        "dns_records": database.fetch_all(
            """SELECT d.*,e.name AS entity_name,p.name AS provider_name FROM dns_records d
               LEFT JOIN entities e ON e.id=d.entity_id LEFT JOIN providers p ON p.id=d.provider_id
               ORDER BY d.name,d.value"""
        ),
        "proxy_hosts": [
            {**row, "domains": json.loads(row["domains_json"] or "[]")}
            for row in database.fetch_all(
                """SELECT ph.*,e.name AS entity_name FROM proxy_hosts ph
                   LEFT JOIN entities e ON e.id=ph.entity_id ORDER BY ph.updated_at DESC"""
            )
        ],
        "speedtest": speedtests.payload(),
        "provider_records": database.fetch_all(
            """SELECT pr.id,pr.provider_id,pr.external_id,pr.entity_id,pr.kind,pr.last_seen_at,
                      p.name AS provider_name,e.name AS entity_name
               FROM provider_records pr JOIN providers p ON p.id=pr.provider_id
               LEFT JOIN entities e ON e.id=pr.entity_id ORDER BY p.name,pr.kind,pr.external_id"""
        ),
        "audit_log": database.fetch_all(
            """SELECT a.*,u.username FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id
               ORDER BY a.created_at DESC LIMIT 200"""
        ),
    }


@app.get("/api/summary")
def summary(_: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    """Licht poll-endpoint: statussen en tellers, geen volledige inventaris."""
    return {
        "counts": inventory_counts(),
        # De appstatus komt van dezelfde entities, maar het dashboard wil de
        # tegels los kunnen bijwerken zonder een volledige bootstrap.
        "app_links": serialize_app_links(),
        "entities": database.fetch_all(
            "SELECT id,status,status_updated_at,last_seen_at FROM entities"
        ),
        "metrics": entity_metrics(database),
        "providers": database.fetch_all(
            "SELECT id,enabled,last_run_at,last_success_at,last_error FROM providers"
        ),
        "speedtest": {
            "latest": database.fetch_one(
                "SELECT * FROM speedtest_runs WHERE status='success' ORDER BY completed_at DESC LIMIT 1"
            ),
            "running": database.fetch_one(
                "SELECT id,started_at FROM speedtest_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1"
            ),
        },
    }


@app.get("/api/physical-devices/{device_id}/history")
def physical_device_history(device_id: str, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    """De historie van het apparaat is die van zijn statusmonitor.

    Een switch levert zelf niets aan; de observatie die erover gaat wél. Zonder
    koppeling is er dus niets te tonen, en dat zeggen we ook.
    """
    device = database.fetch_one(
        "SELECT id,name,monitor_entity_id FROM physical_devices WHERE id=?", (device_id,)
    )
    if not device:
        raise HTTPException(404, "Netwerkapparaat niet gevonden")
    if not device["monitor_entity_id"]:
        return {"monitor_entity_id": None, "days": [], "samples": []}
    history = entity_history(device["monitor_entity_id"])
    return {"monitor_entity_id": device["monitor_entity_id"], **history}


@app.get("/api/entities/{entity_id}/history")
def entity_history(entity_id: str, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    if not database.fetch_one("SELECT id FROM entities WHERE id=?", (entity_id,)):
        raise HTTPException(404, "Device niet gevonden")
    days = database.fetch_all(
        "SELECT * FROM entity_days WHERE entity_id=? ORDER BY day DESC LIMIT 30", (entity_id,)
    )
    samples = database.fetch_all(
        """SELECT sampled_at,status,cpu_percent,memory_percent,latency_ms FROM entity_samples
           WHERE entity_id=? ORDER BY sampled_at DESC LIMIT 576""",
        (entity_id,),
    )
    total = sum(row["samples_total"] for row in days)
    up = sum(row["samples_up"] for row in days)
    return {
        "days": list(reversed(days)),
        "samples": list(reversed(samples)),
        "uptime_percent": round(up / total * 100, 1) if total else None,
        "flips": sum(row["flips"] for row in days),
    }


def local_subnet_suggestion() -> str | None:
    """Het subnet van deze host binnen PATCH_TRUSTED_SUBNETS, als voorstel."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: stuurt niets, kiest alleen route
            local_ip = ipaddress.ip_address(probe.getsockname()[0])
    except OSError:
        return None
    for value in settings.trusted_subnets:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if local_ip.version == network.version and local_ip in network and network.num_addresses <= 1024:
            return str(network)
    return None


@app.get("/api/wizard/info")
def wizard_info(_: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    row = database.fetch_one("SELECT value FROM app_meta WHERE key='wizard_dismissed'")
    return {
        "dismissed": bool(row and row["value"] == "1"),
        "suggested_subnet": local_subnet_suggestion(),
        "trusted_subnets": list(settings.trusted_subnets),
    }


@app.patch("/api/wizard/info")
def wizard_state(payload: WizardStateInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO app_meta(key,value) VALUES('wizard_dismissed',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            ("1" if payload.dismissed else "0",),
        )
    database.audit(auth.user_id, "wizard.state", "application", "wizard", payload.model_dump())
    return {"dismissed": payload.dismissed}


CSV_COLUMNS = ("name", "type", "ip_address", "mac_address", "hostname", "notes")
# Ruim boven wat ~40 apparaten nodig hebben; groter is vrijwel zeker een
# vergissing en verdient een melding, geen halve import.
CSV_MAX_BYTES = 1_000_000


@app.post("/api/entities/import-csv")
async def import_entities_csv(
    file: UploadFile = File(...), auth: AuthContext = Depends(write_auth)
) -> dict[str, Any]:
    """Handmatige devices in bulk, voor de eerste vulling.

    Bewust alleen aanmaken en bijwerken, nooit verwijderen: een half bestand
    mag je inventaris niet leegmaken. Rijen die niet kloppen worden benoemd in
    plaats van stil overgeslagen.
    """
    # Eén byte meer lezen dan we accepteren: zo weten we of er méér was. Stil
    # afkappen zou halverwege een regel gebeuren en duizenden apparaten laten
    # verdwijnen terwijl de melding "gelukt" zegt.
    payload = await file.read(CSV_MAX_BYTES + 1)
    if len(payload) > CSV_MAX_BYTES:
        raise HTTPException(413, f"Bestand is groter dan {CSV_MAX_BYTES // 1_000_000} MB; splits het op")
    raw = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "name" not in [name.strip().lower() for name in reader.fieldnames]:
        raise HTTPException(422, f"Eerste regel moet kolomnamen bevatten, met minimaal 'name'. Toegestaan: {', '.join(CSV_COLUMNS)}")
    created = updated = 0
    problems: list[str] = []
    for number, row in enumerate(reader, start=2):
        values = {
            key.strip().lower(): (value or "").strip()
            for key, value in row.items() if key and key.strip().lower() in CSV_COLUMNS
        }
        name = values.get("name", "")
        if not name:
            problems.append(f"regel {number}: geen naam")
            continue
        mac = normalize_mac(values.get("mac_address")) if values.get("mac_address") else None
        if values.get("mac_address") and not mac:
            problems.append(f"regel {number}: '{values['mac_address']}' is geen MAC-adres")
            continue
        kind = categories.normalize(values.get("type") or "device")
        # Op MAC bijwerken, anders op naam: hetzelfde bestand twee keer
        # inlezen mag geen dubbele apparaten opleveren.
        existing = None
        if mac:
            existing = database.fetch_one("SELECT id FROM entities WHERE lower(mac_address)=?", (mac,))
        if not existing:
            existing = database.fetch_one(
                "SELECT id FROM entities WHERE origin='manual' AND lower(name)=lower(?)", (name,)
            )
        now = utcnow()
        with database.transaction() as connection:
            if existing:
                connection.execute(
                    """UPDATE entities SET type=?,ip_address=COALESCE(?,ip_address),
                       mac_address=COALESCE(?,mac_address),hostname=COALESCE(?,hostname),
                       notes=CASE WHEN ?!='' THEN ? ELSE notes END,updated_at=? WHERE id=?""",
                    (kind, values.get("ip_address") or None, mac, values.get("hostname") or None,
                     values.get("notes", ""), values.get("notes", ""), now, existing["id"]),
                )
                updated += 1
            else:
                connection.execute(
                    """INSERT INTO entities(id,name,type,origin,status,ip_address,mac_address,hostname,
                                            notes,created_at,updated_at)
                       VALUES(?,?,?,'manual','unknown',?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), name, kind, values.get("ip_address") or None, mac,
                     values.get("hostname") or None, values.get("notes", ""), now, now),
                )
                created += 1
    database.audit(auth.user_id, "entity.import_csv", "entity", None, {"created": created, "updated": updated})
    return {"created": created, "updated": updated, "problems": problems[:20]}


@app.get("/api/search")
def search(q: str, _: AuthContext = Depends(current_auth)) -> list[dict[str, Any]]:
    """Eén zoekveld over alles wat een naam of adres heeft.

    Bewust geen fuzzy matching of scoring: bij deze omvang is een
    substring-treffer per kolom precies genoeg, en je kunt zien waaróm iets
    matcht.
    """
    term = q.strip()
    if len(term) < 2:
        return []
    like = f"%{term.lower()}%"
    results: list[dict[str, Any]] = []
    for row in database.fetch_all(
        """SELECT id,name,type,origin,status,ip_address,mac_address,hostname FROM entities
           WHERE archived=0 AND (lower(name) LIKE ? OR lower(COALESCE(ip_address,'')) LIKE ?
                 OR lower(COALESCE(mac_address,'')) LIKE ? OR lower(COALESCE(hostname,'')) LIKE ?)
           ORDER BY origin,name LIMIT 25""",
        (like, like, like, like),
    ):
        results.append({
            "kind": "entity", "id": row["id"], "label": row["name"],
            "sub": row["ip_address"] or row["hostname"] or categories.label_for(row["type"]),
            "status": row["status"], "goto": "patch",
        })
    for row in database.fetch_all(
        """SELECT id,name,type,model,location FROM physical_devices
           WHERE lower(name) LIKE ? OR lower(model) LIKE ? OR lower(location) LIKE ?
           ORDER BY position LIMIT 25""",
        (like, like, like),
    ):
        results.append({
            "kind": "physical", "id": row["id"], "label": row["name"],
            "sub": row["model"] or categories.label_for(row["type"]), "goto": "patch",
        })
    for row in database.fetch_all(
        """SELECT id,name,url,group_name FROM app_links
           WHERE lower(name) LIKE ? OR lower(url) LIKE ? OR lower(group_name) LIKE ?
           ORDER BY name LIMIT 25""",
        (like, like, like),
    ):
        results.append({
            "kind": "app", "id": row["id"], "label": row["name"],
            "sub": row["url"], "goto": "apps",
        })
    return results


@app.get("/api/changes")
def changes(days: int = 7, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    """Wat is er sinds kort verschenen of verdwenen.

    De auditlog vertelt wat jíj hebt gedaan; dit vertelt wat het netwerk deed.
    Beide velden bestonden al, ze werden alleen nergens naast elkaar gezet.
    """
    days = max(1, min(days, 90))
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    appeared = database.fetch_all(
        """SELECT id,name,type,ip_address,mac_address,vendor,origin,first_seen_at
           FROM entities WHERE archived=0 AND first_seen_at IS NOT NULL AND first_seen_at>=?
           ORDER BY first_seen_at DESC LIMIT 50""",
        (since,),
    )
    # 'Verdwenen' is niet hetzelfde als 'down': dit zijn dingen waar al een tijd
    # geen enkele bron meer iets over gezegd heeft.
    vanished = database.fetch_all(
        """SELECT id,name,type,ip_address,mac_address,vendor,origin,last_seen_at,status
           FROM entities WHERE archived=0 AND ignored=0 AND last_seen_at IS NOT NULL
             AND last_seen_at<? AND status!='up'
           ORDER BY last_seen_at DESC LIMIT 50""",
        (since,),
    )
    return {"days": days, "since": since, "appeared": appeared, "vanished": vanished}


@app.get("/api/discoveries")
def discoveries(_: AuthContext = Depends(current_auth)) -> list[dict[str, Any]]:
    """Open discoveries voor het bulk-toewijsscherm (ook los van de wizard)."""
    linked = {
        row["b_entity_id"]
        for row in database.fetch_all("SELECT b_entity_id FROM cables WHERE b_entity_id IS NOT NULL")
    }
    rows = database.fetch_all(
        """SELECT e.id,e.name,e.type,e.status,e.ip_address,e.mac_address,e.hostname,e.vendor,
                  e.first_seen_at,e.last_seen_at,e.uplink_device_id,d.name AS uplink_device_name,
                  d.type AS uplink_device_type,
                  (SELECT m.name FROM physical_devices m WHERE m.monitor_entity_id=e.id) AS monitor_for,
                  (SELECT GROUP_CONCAT(p.name,', ') FROM provider_records pr
                     JOIN providers p ON p.id=pr.provider_id WHERE pr.entity_id=e.id) AS sources
           FROM entities e
           LEFT JOIN physical_devices d ON d.id=e.uplink_device_id
           WHERE e.origin='discovered' AND e.ignored=0 AND e.archived=0
           ORDER BY e.first_seen_at DESC,e.name"""
    )
    for row in rows:
        # Een poortloze uplink telt net zo goed als toegewezen: het ding heeft
        # een plek gekregen, alleen zonder kabel.
        row["linked"] = row["id"] in linked or bool(row["uplink_device_id"]) or bool(row["monitor_for"])
    return rows


@app.post("/api/entities/{entity_id}/promote")
def promote_entity(entity_id: str, payload: PromoteInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    """Neem een discovery over als handmatig device; providers blijven observeren."""
    entity = database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))
    if not entity:
        raise HTTPException(404, "Device niet gevonden")
    if entity["origin"] != "discovered":
        raise HTTPException(409, "Dit device is al handmatig")
    name = (payload.name or entity["name"]).strip()
    if not name:
        raise HTTPException(422, "Naam mag niet leeg zijn")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE entities SET origin='manual',name=?,type=?,ignored=0,archived=0,updated_at=? WHERE id=?",
            (name, payload.type or entity["type"], utcnow(), entity_id),
        )
    database.audit(auth.user_id, "entity.promote", "entity", entity_id, {"name": name})
    return database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))


@app.post("/api/entities")
def create_entity(payload: EntityInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    if payload.mac_address and database.fetch_one(
        "SELECT id FROM entities WHERE lower(mac_address)=?", (payload.mac_address,)
    ):
        raise HTTPException(409, "Dit MAC-adres hoort al bij een ander device")
    entity_id = str(uuid.uuid4())
    now = utcnow()
    try:
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO entities
                   (id,name,type,origin,status,ip_address,mac_address,hostname,notes,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (entity_id, payload.name, payload.type, "manual", "unknown", payload.ip_address,
                 payload.mac_address, payload.hostname, payload.notes, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Dit MAC-adres hoort al bij een ander device") from exc
    database.audit(auth.user_id, "entity.create", "entity", entity_id, payload.model_dump())
    return database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))


@app.patch("/api/entities/{entity_id}")
def update_entity(entity_id: str, payload: EntityInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    entity = database.fetch_one("SELECT origin FROM entities WHERE id=?", (entity_id,))
    if not entity:
        raise HTTPException(404, "Device niet gevonden")
    if entity["origin"] != "manual":
        raise HTTPException(409, "Een geïmporteerd device bewerk je in de databron of via samenvoegen")
    if payload.mac_address and database.fetch_one(
        "SELECT id FROM entities WHERE lower(mac_address)=? AND id<>?", (payload.mac_address, entity_id)
    ):
        raise HTTPException(409, "Dit MAC-adres hoort al bij een ander device")
    try:
        with database.transaction() as connection:
            connection.execute(
                """UPDATE entities SET name=?,type=?,ip_address=?,mac_address=?,hostname=?,notes=?,updated_at=?
                   WHERE id=?""",
                (payload.name, payload.type, payload.ip_address,
                 payload.mac_address, payload.hostname, payload.notes, utcnow(), entity_id),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Dit MAC-adres hoort al bij een ander device") from exc
    database.audit(auth.user_id, "entity.update", "entity", entity_id, payload.model_dump())
    return database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))


@app.put("/api/entities/{entity_id}/uplink")
def set_entity_uplink(entity_id: str, payload: UplinkInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    """Hang een device aan een netwerkapparaat zonder poort (wifi, of poort onbekend).

    Een echte kabel blijft de sterkere koppeling: bestaat die al, dan zou een
    uplink ernaast alleen maar verwarren.
    """
    entity = database.fetch_one("SELECT id,type FROM entities WHERE id=?", (entity_id,))
    if not entity:
        raise HTTPException(404, "Device niet gevonden")
    device_id = (payload.physical_device_id or "").strip() or None
    if device_id and not categories.can_attach(entity["type"]):
        raise HTTPException(
            409,
            f"Een {categories.label_for(entity['type']).lower()} heeft geen netwerkpoort; "
            "koppel de host waarop het draait.",
        )
    if device_id:
        device = database.fetch_one("SELECT id FROM physical_devices WHERE id=?", (device_id,))
        if not device:
            raise HTTPException(404, "Netwerkapparaat niet gevonden")
        cable = database.fetch_one("SELECT id FROM cables WHERE b_entity_id=?", (entity_id,))
        if cable:
            raise HTTPException(409, "Dit device zit al met een kabel op een poort; koppel die eerst los")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE entities SET uplink_device_id=?,updated_at=? WHERE id=?",
            (device_id, utcnow(), entity_id),
        )
    database.audit(auth.user_id, "entity.uplink", "entity", entity_id, {"physical_device_id": device_id})
    return database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))


@app.patch("/api/entities/{entity_id}/discovery-state")
def update_discovery_state(entity_id: str, payload: DiscoveryStateInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    entity = database.fetch_one("SELECT origin FROM entities WHERE id=?", (entity_id,))
    if not entity:
        raise HTTPException(404, "Device niet gevonden")
    if entity["origin"] != "discovered":
        raise HTTPException(409, "Alleen discoveries kunnen worden genegeerd of gearchiveerd")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE entities SET ignored=?,archived=?,updated_at=? WHERE id=?",
            (int(payload.ignored), int(payload.archived), utcnow(), entity_id),
        )
        connection.execute(
            "UPDATE topology_nodes SET hidden=? WHERE id=?",
            (int(payload.ignored or payload.archived), f"entity:{entity_id}"),
        )
    database.audit(auth.user_id, "entity.discovery_state", "entity", entity_id, payload.model_dump())
    return {"ok": True}


@app.post("/api/entities/{entity_id}/merge")
def merge_entity(entity_id: str, payload: EntityMergeInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    source = database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))
    target = database.fetch_one("SELECT * FROM entities WHERE id=?", (payload.target_entity_id,))
    if not source or not target:
        raise HTTPException(404, "Bron- of doeldevice niet gevonden")
    if source["origin"] != "discovered" or source["id"] == target["id"]:
        raise HTTPException(409, "Alleen een discovery kan in een ander device worden samengevoegd")
    source_cable = database.fetch_one("SELECT id FROM cables WHERE b_entity_id=?", (entity_id,))
    target_cable = database.fetch_one("SELECT id FROM cables WHERE b_entity_id=?", (target["id"],))
    if source_cable and target_cable:
        raise HTTPException(409, "Beide devices hebben een fysieke poort; koppel er eerst één los")
    source_node = f"entity:{entity_id}"
    target_node = f"entity:{target['id']}"
    manual_relations = database.fetch_all(
        "SELECT * FROM topology_relations WHERE source='manual' AND (from_node_id=? OR to_node_id=?)",
        (source_node, source_node),
    )
    # Een provider kan een target onder de discovery hebben gehangen. Bij de
    # eenvoudige "alle kinderen naar target"-update werd target dan zijn eigen
    # parent (of ontstond via een tussenlaag een cyclus). Haal het doel eerst
    # uit de subtree en zet het op de plaats van de bron.
    target_is_descendant = False
    ancestor_id = target["parent_id"]
    visited: set[str] = set()
    while ancestor_id and ancestor_id not in visited:
        if ancestor_id == source["id"]:
            target_is_descendant = True
            break
        visited.add(ancestor_id)
        ancestor = database.fetch_one("SELECT parent_id FROM entities WHERE id=?", (ancestor_id,))
        ancestor_id = ancestor["parent_id"] if ancestor else None
    with database.transaction() as connection:
        if source_cable:
            connection.execute("UPDATE cables SET b_entity_id=? WHERE b_entity_id=?", (target["id"], entity_id))
        for table in ("provider_records", "observations", "conflicts"):
            connection.execute(f"UPDATE {table} SET entity_id=? WHERE entity_id=?", (target["id"], entity_id))
        if target_is_descendant:
            replacement_parent = source["parent_id"] if source["parent_id"] != target["id"] else None
            connection.execute(
                "UPDATE entities SET parent_id=? WHERE id=?", (replacement_parent, target["id"])
            )
        connection.execute(
            "UPDATE entities SET parent_id=? WHERE parent_id=? AND id<>?",
            (target["id"], entity_id, target["id"]),
        )
        connection.execute("UPDATE dns_records SET entity_id=? WHERE entity_id=?", (target["id"], entity_id))
        connection.execute("UPDATE proxy_hosts SET entity_id=? WHERE entity_id=?", (target["id"], entity_id))
        connection.execute("UPDATE proxy_hosts SET service_entity_id=? WHERE service_entity_id=?", (target["id"], entity_id))
        connection.execute("DELETE FROM topology_nodes WHERE id=?", (source_node,))
        for relation in manual_relations:
            from_id = target_node if relation["from_node_id"] == source_node else relation["from_node_id"]
            to_id = target_node if relation["to_node_id"] == source_node else relation["to_node_id"]
            if from_id != to_id:
                connection.execute(
                    """INSERT OR IGNORE INTO topology_relations
                       (id,from_node_id,to_node_id,relation_type,label,source,locked,metadata_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (relation["id"], from_id, to_id, relation["relation_type"], relation["label"], "manual",
                     relation["locked"], relation["metadata_json"], relation["created_at"], utcnow()),
                )
        connection.execute("DELETE FROM entities WHERE id=?", (entity_id,))
    database.audit(auth.user_id, "entity.merge", "entity", entity_id, {"target_entity_id": target["id"], "source_name": source["name"]})
    return {"ok": True}


def entity_deletion_impact(entity_id: str) -> dict[str, Any]:
    entity = database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))
    if not entity:
        raise HTTPException(404, "Device niet gevonden")
    counts = database.fetch_one(
        """SELECT
             (SELECT COUNT(*) FROM cables WHERE b_entity_id=?) AS cables,
             (SELECT COUNT(*) FROM entities WHERE parent_id=?) AS children,
             (SELECT COUNT(*) FROM provider_records WHERE entity_id=?) AS provider_links,
             (SELECT COUNT(*) FROM dns_records WHERE entity_id=?) AS dns_records,
             (SELECT COUNT(*) FROM proxy_hosts WHERE entity_id=? OR service_entity_id=?) AS proxy_hosts,
             (SELECT COUNT(*) FROM topology_relations WHERE from_node_id=? OR to_node_id=?) AS topology_relations""",
        (entity_id, entity_id, entity_id, entity_id, entity_id, entity_id,
         f"entity:{entity_id}", f"entity:{entity_id}"),
    )
    return {
        "id": entity_id,
        "name": entity["name"],
        "origin": entity["origin"],
        "deletable": entity["origin"] == "manual",
        "counts": counts,
    }


@app.get("/api/entities/{entity_id}/deletion-impact")
def get_entity_deletion_impact(entity_id: str, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    return entity_deletion_impact(entity_id)


@app.delete("/api/entities/{entity_id}")
def delete_entity(entity_id: str, confirm: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    impact = entity_deletion_impact(entity_id)
    if not impact["deletable"]:
        raise HTTPException(409, "Een geïmporteerd device verwijder of deactiveer je in de databron")
    if confirm != impact["name"]:
        raise HTTPException(422, "De bevestigingsnaam komt niet overeen")
    with database.transaction() as connection:
        connection.execute("DELETE FROM cables WHERE b_entity_id=?", (entity_id,))
        connection.execute("DELETE FROM topology_nodes WHERE id=?", (f"entity:{entity_id}",))
        connection.execute("DELETE FROM entities WHERE id=?", (entity_id,))
    database.audit(auth.user_id, "entity.delete", "entity", entity_id, impact)
    return {"ok": True}


def resolve_monitor(entity_id: str | None) -> str | None:
    """De entity waarvan een netwerkapparaat zijn status leent."""
    entity_id = (entity_id or "").strip() or None
    if entity_id and not database.fetch_one("SELECT id FROM entities WHERE id=?", (entity_id,)):
        raise HTTPException(404, "Statusmonitor niet gevonden")
    return entity_id


def create_device_ports(connection: sqlite3.Connection, device_id: str, device_type: str, first: int, last: int) -> None:
    for number in range(first, last + 1):
        if device_type == "patch_panel":
            front, rear = f"{device_id}-f{number}", f"{device_id}-r{number}"
            connection.execute(
                "INSERT INTO ports(id,physical_device_id,number,side,label) VALUES(?,?,?,?,?)",
                (front, device_id, number, "front", f"Poort {number}"),
            )
            connection.execute(
                "INSERT INTO ports(id,physical_device_id,number,side,peer_port_id,label) VALUES(?,?,?,?,?,?)",
                (rear, device_id, number, "rear", front, f"Poort {number} (achter)"),
            )
            connection.execute("UPDATE ports SET peer_port_id=? WHERE id=?", (rear, front))
        else:
            connection.execute(
                "INSERT INTO ports(id,physical_device_id,number,label) VALUES(?,?,?,?)",
                (f"{device_id}-p{number}", device_id, number, f"Poort {number}"),
            )


@app.post("/api/physical-devices")
def create_physical_device(payload: PhysicalDeviceInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    device_id = str(uuid.uuid4())
    now = utcnow()
    position = database.fetch_one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM physical_devices")["n"]
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO physical_devices(id,name,type,model,location,notes,position,monitor_entity_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (device_id, payload.name.strip(), payload.type, payload.model, payload.location, payload.notes,
             position, resolve_monitor(payload.monitor_entity_id), now, now),
        )
        create_device_ports(connection, device_id, payload.type, 1, payload.ports)
    database.audit(auth.user_id, "physical_device.create", "physical_device", device_id, payload.model_dump())
    return {"id": device_id}


@app.patch("/api/physical-devices/{device_id}")
def update_physical_device(device_id: str, payload: PhysicalDeviceInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    device = database.fetch_one("SELECT * FROM physical_devices WHERE id=?", (device_id,))
    if not device:
        raise HTTPException(404, "Netwerkapparaat niet gevonden")
    if (device["type"] == "patch_panel") != (payload.type == "patch_panel"):
        raise HTTPException(409, "Wissel niet tussen patchpanel en ander type; maak een nieuw apparaat aan")
    port_count = database.fetch_one(
        "SELECT COUNT(DISTINCT number) AS n FROM ports WHERE physical_device_id=?", (device_id,)
    )["n"]
    if payload.ports < port_count:
        blocked = database.fetch_all(
            """SELECT DISTINCT p.number FROM ports p
               JOIN cables c ON p.id IN (c.a_port_id, c.b_port_id)
               WHERE p.physical_device_id=? AND p.number>? ORDER BY p.number""",
            (device_id, payload.ports),
        )
        if blocked:
            raise HTTPException(409, f"Maak eerst poort(en) {', '.join(str(item['number']) for item in blocked)} vrij")
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE physical_devices SET name=?,type=?,model=?,location=?,notes=?,monitor_entity_id=?,updated_at=? WHERE id=?",
            (payload.name.strip(), payload.type, payload.model, payload.location, payload.notes,
             resolve_monitor(payload.monitor_entity_id), now, device_id),
        )
        if payload.ports < port_count:
            connection.execute("DELETE FROM ports WHERE physical_device_id=? AND number>?", (device_id, payload.ports))
        create_device_ports(connection, device_id, payload.type, port_count + 1, payload.ports)
    database.audit(auth.user_id, "physical_device.update", "physical_device", device_id, payload.model_dump())
    return {"ok": True}


def physical_device_deletion_impact(device_id: str) -> dict[str, Any]:
    device = database.fetch_one("SELECT * FROM physical_devices WHERE id=?", (device_id,))
    if not device:
        raise HTTPException(404, "Netwerkapparaat niet gevonden")
    counts = database.fetch_one(
        """SELECT
             (SELECT COUNT(*) FROM ports WHERE physical_device_id=? AND side='front') AS ports,
             (SELECT COUNT(*) FROM cables c JOIN ports p ON p.id IN (c.a_port_id, c.b_port_id) WHERE p.physical_device_id=?) AS cables,
             (SELECT COUNT(*) FROM topology_relations WHERE from_node_id=? OR to_node_id=?) AS topology_relations""",
        (device_id, device_id, f"physical:{device_id}", f"physical:{device_id}"),
    )
    return {"id": device_id, "name": device["name"], "deletable": True, "counts": counts}


@app.get("/api/physical-devices/{device_id}/deletion-impact")
def get_physical_device_deletion_impact(device_id: str, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    return physical_device_deletion_impact(device_id)


@app.delete("/api/physical-devices/{device_id}")
def delete_physical_device(device_id: str, confirm: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    impact = physical_device_deletion_impact(device_id)
    if confirm != impact["name"]:
        raise HTTPException(422, "De bevestigingsnaam komt niet overeen")
    with database.transaction() as connection:
        connection.execute("DELETE FROM topology_nodes WHERE id=?", (f"physical:{device_id}",))
        connection.execute("DELETE FROM physical_devices WHERE id=?", (device_id,))
    database.audit(auth.user_id, "physical_device.delete", "physical_device", device_id, impact)
    return {"ok": True}


@app.patch("/api/ports/{port_id}")
def update_port(port_id: str, payload: PortInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    with database.transaction() as connection:
        cursor = connection.execute(
            "UPDATE ports SET label=?,speed_mbps=?,notes=? WHERE id=?",
            (payload.label, payload.speed_mbps, payload.notes, port_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Poort niet gevonden")
    database.audit(auth.user_id, "port.update", "port", port_id, payload.model_dump())
    return {"ok": True}


def validate_cable(
    payload: CableInput,
    replace_port_id: str | None = None,
    replace_cable_id: str | None = None,
) -> None:
    """Controleer uiteinden en bezetting; replace_port_id mag zijn eigen kabel vervangen."""
    if (payload.b_port_id is None) == (payload.b_entity_id is None):
        raise HTTPException(422, "Kies precies één ander uiteinde: een poort of een device")
    if not database.fetch_one("SELECT id FROM ports WHERE id=?", (payload.a_port_id,)):
        raise HTTPException(404, "Poort niet gevonden")
    if payload.b_port_id:
        if payload.b_port_id == payload.a_port_id:
            raise HTTPException(422, "Een kabel kan niet twee keer in dezelfde poort")
        if not database.fetch_one("SELECT id FROM ports WHERE id=?", (payload.b_port_id,)):
            raise HTTPException(404, "Doelpoort niet gevonden")
    if payload.b_entity_id and not database.fetch_one("SELECT id FROM entities WHERE id=?", (payload.b_entity_id,)):
        raise HTTPException(404, "Device niet gevonden")
    for port_id in filter(None, (payload.a_port_id, payload.b_port_id)):
        occupied = database.fetch_one(
            "SELECT id FROM cables WHERE (a_port_id=? OR b_port_id=?)", (port_id, port_id)
        )
        if occupied and not (
            (replace_port_id and port_id == replace_port_id)
            or (replace_cable_id and occupied["id"] == replace_cable_id)
        ):
            raise HTTPException(409, f"Poort {port_id} is al bezet; koppel die eerst los")
    if payload.b_entity_id:
        linked = database.fetch_one(
            "SELECT id,a_port_id FROM cables WHERE b_entity_id=?", (payload.b_entity_id,)
        )
        if linked and not (
            (replace_port_id and linked["a_port_id"] == replace_port_id)
            or (replace_cable_id and linked["id"] == replace_cable_id)
        ):
            raise HTTPException(409, "Dit device is al aan een andere poort gekoppeld")


def _insert_cable(
    connection: sqlite3.Connection, payload: CableInput, user_id: str
) -> dict[str, Any]:
    cable_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO cables(id,a_port_id,b_port_id,b_entity_id,label,color,notes,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (cable_id, payload.a_port_id, payload.b_port_id, payload.b_entity_id,
         payload.label, payload.color, payload.notes, utcnow(), user_id),
    )
    # Een kabel is preciezer dan "hangt ergens aan": de losse uplink zou
    # er alleen naast blijven staan en tegenspreken waar het ding zit.
    if payload.b_entity_id:
        connection.execute(
            "UPDATE entities SET uplink_device_id=NULL WHERE id=?", (payload.b_entity_id,)
        )
    row = connection.execute("SELECT * FROM cables WHERE id=?", (cable_id,)).fetchone()
    return dict(row)


def insert_cable(
    payload: CableInput,
    user_id: str,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    if connection is not None:
        return _insert_cable(connection, payload, user_id)
    with database.transaction() as active_connection:
        return _insert_cable(active_connection, payload, user_id)


@app.get("/api/ports/{port_id}/trace")
def port_trace(port_id: str, _: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    ports, _cables, by_port = load_cable_context()
    if port_id not in ports:
        raise HTTPException(404, "Poort niet gevonden")
    devices = {row["id"]: row for row in database.fetch_all("SELECT id,name,type FROM physical_devices")}
    hops, entity_id = trace_from_port(port_id, ports, by_port)
    steps = [{"label": port_display_name(ports[port_id], devices), "kind": "start"}]
    for hop in hops:
        if hop["kind"] == "through":
            steps.append({"label": port_display_name(ports[hop["peer_port_id"]], devices), "kind": "through"})
        elif hop.get("entity_id"):
            entity = database.fetch_one("SELECT name,type,status FROM entities WHERE id=?", (hop["entity_id"],))
            steps.append({"label": entity["name"] if entity else "?", "kind": "entity",
                          "status": entity["status"] if entity else None, "cable_id": hop["cable_id"]})
        else:
            steps.append({"label": port_display_name(ports[hop["port_id"]], devices), "kind": "port",
                          "cable_id": hop["cable_id"]})
    return {"port_id": port_id, "entity_id": entity_id, "steps": steps}


@app.post("/api/cables")
def cable_create(payload: CableInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    validate_cable(payload)
    cable = insert_cable(payload, auth.user_id)
    database.audit(auth.user_id, "cable.create", "cable", cable["id"], payload.model_dump())
    return cable


@app.delete("/api/cables/{cable_id}")
def cable_delete(cable_id: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        cursor = connection.execute("DELETE FROM cables WHERE id=?", (cable_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Kabel niet gevonden")
    database.audit(auth.user_id, "cable.delete", "cable", cable_id)
    return {"ok": True}


@app.put("/api/ports/{port_id}/cable")
def port_cable_set(port_id: str, payload: PortCableInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    """Gemaksvorm voor de poortdrawer: vervang de kabel op deze poort in één stap."""
    cable_input = CableInput(
        a_port_id=port_id, **payload.model_dump(exclude={"move_cable_id"})
    )
    validate_cable(
        cable_input,
        replace_port_id=port_id,
        replace_cable_id=payload.move_cable_id,
    )
    with database.transaction() as connection:
        if payload.move_cable_id:
            cursor = connection.execute("DELETE FROM cables WHERE id=?", (payload.move_cable_id,))
            if cursor.rowcount == 0:
                raise HTTPException(404, "Te verplaatsen kabel niet gevonden")
        connection.execute("DELETE FROM cables WHERE a_port_id=? OR b_port_id=?", (port_id, port_id))
        cable = insert_cable(cable_input, auth.user_id, connection=connection)
    database.audit(auth.user_id, "port.cable.set", "port", port_id, payload.model_dump())
    return cable


@app.delete("/api/ports/{port_id}/cable")
def port_cable_clear(port_id: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        connection.execute("DELETE FROM cables WHERE a_port_id=? OR b_port_id=?", (port_id, port_id))
    database.audit(auth.user_id, "port.cable.clear", "port", port_id)
    return {"ok": True}


def app_link_url(value: str) -> str:
    """Alleen http(s).

    Een tegel is een <a href>; zonder deze controle kun je javascript: in je
    eigen dashboard zetten, en dat draait dan met je sessie erbij.
    """
    url = value.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Een link moet met http:// of https:// beginnen")
    return url


def serialize_app_links() -> list[dict[str, Any]]:
    return database.fetch_all(
        """SELECT a.*, e.name AS monitor_name, COALESCE(e.status,'unknown') AS status,
                  e.status_updated_at
           FROM app_links a LEFT JOIN entities e ON e.id=a.monitor_entity_id
           ORDER BY a.group_name, a.position, a.name"""
    )


@app.post("/api/app-links")
def create_app_link(payload: AppLinkInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    link_id = str(uuid.uuid4())
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO app_links(id,name,url,description,icon,group_name,monitor_entity_id,position,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (link_id, payload.name.strip(), app_link_url(payload.url), payload.description,
             payload.icon.strip(), payload.group_name.strip(),
             resolve_monitor(payload.monitor_entity_id), payload.position, now, now),
        )
    database.audit(auth.user_id, "app_link.create", "app_link", link_id, {"name": payload.name})
    return database.fetch_one("SELECT * FROM app_links WHERE id=?", (link_id,))


@app.patch("/api/app-links/{link_id}")
def update_app_link(link_id: str, payload: AppLinkInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    if not database.fetch_one("SELECT id FROM app_links WHERE id=?", (link_id,)):
        raise HTTPException(404, "App niet gevonden")
    with database.transaction() as connection:
        connection.execute(
            """UPDATE app_links SET name=?,url=?,description=?,icon=?,group_name=?,
               monitor_entity_id=?,position=?,updated_at=? WHERE id=?""",
            (payload.name.strip(), app_link_url(payload.url), payload.description,
             payload.icon.strip(), payload.group_name.strip(),
             resolve_monitor(payload.monitor_entity_id), payload.position, utcnow(), link_id),
        )
    database.audit(auth.user_id, "app_link.update", "app_link", link_id, {"name": payload.name})
    return database.fetch_one("SELECT * FROM app_links WHERE id=?", (link_id,))


@app.delete("/api/app-links/{link_id}")
def delete_app_link(link_id: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    link = database.fetch_one("SELECT name FROM app_links WHERE id=?", (link_id,))
    if not link:
        raise HTTPException(404, "App niet gevonden")
    with database.transaction() as connection:
        connection.execute("DELETE FROM app_links WHERE id=?", (link_id,))
    database.audit(auth.user_id, "app_link.delete", "app_link", link_id, {"name": link["name"]})
    return {"ok": True}


@app.post("/api/providers")
def create_provider(payload: ProviderCreateInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    """Nog een omgeving van een bestaand soort: een tweede Portainer of AdGuard."""
    template = next((item for item in PROVIDER_TEMPLATES if item[1] == payload.type), None)
    if not template:
        raise HTTPException(422, "Onbekend providertype")
    provider_id = str(uuid.uuid4())
    # Het sjabloon bevat voorbeeldadressen; bij een tweede omgeving zouden die
    # alleen maar suggereren dat er al iets is ingevuld.
    config = dict(template[4])
    if "base_url" in config:
        config["base_url"] = ""
    if "endpoints" in config:
        config["endpoints"] = []
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO providers(id,type,name,enabled,poll_interval_seconds,config_json,updated_at)
               VALUES(?,?,?,0,?,?,?)""",
            (provider_id, payload.type, payload.name.strip(), template[3], json.dumps(config), utcnow()),
        )
    database.audit(auth.user_id, "provider.create", "provider", provider_id, {"type": payload.type})
    return serialize_provider(database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,)))


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: str, confirm: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    provider = database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,))
    if not provider:
        raise HTTPException(404, "Provider niet gevonden")
    if confirm != provider["name"]:
        raise HTTPException(422, "De bevestigingsnaam komt niet overeen")
    # De laatste van een soort weghalen laat de app zonder die adapter achter;
    # uitzetten doet hetzelfde zonder de instellingen te verliezen.
    remaining = database.fetch_one(
        "SELECT COUNT(*) AS n FROM providers WHERE type=?", (provider["type"],)
    )["n"]
    if remaining <= 1:
        raise HTTPException(409, "Dit is de enige bron van dit type; zet hem uit in plaats van te verwijderen")
    with database.transaction() as connection:
        connection.execute("DELETE FROM providers WHERE id=?", (provider_id,))
    database.audit(auth.user_id, "provider.delete", "provider", provider_id, {"name": provider["name"]})
    return {"ok": True}


@app.patch("/api/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    provider = database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,))
    if not provider:
        raise HTTPException(404, "Provider niet gevonden")
    allowed = {field["key"] for field in PROVIDER_CREDENTIAL_FIELDS.get(provider["type"], [])}
    supplied = set(payload.credentials) | set(payload.clear_credentials)
    if not supplied.issubset(allowed):
        raise HTTPException(422, "Onbekend credentialveld voor deze provider")
    with database.transaction() as connection:
        connection.execute(
            """UPDATE providers SET name=?,enabled=?,poll_interval_seconds=?,config_json=?,updated_at=? WHERE id=?""",
            ((payload.name or provider["name"]).strip() or provider["name"], int(payload.enabled),
             payload.poll_interval_seconds, json.dumps(payload.config), utcnow(), provider_id),
        )
    provider_secrets.update(provider_id, payload.credentials, payload.clear_credentials)
    database.audit(auth.user_id, "provider.update", "provider", provider_id, {"enabled": payload.enabled})
    return serialize_provider(database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,)))


@app.patch("/api/settings")
def update_app_settings(payload: AppSettingsInput, auth: AuthContext = Depends(write_auth)) -> dict[str, str]:
    title = " ".join(payload.title.split())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO app_meta(key,value) VALUES('app_title',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (title,),
        )
    database.audit(auth.user_id, "settings.update", "application", "title", {"title": title})
    return {"title": title}


@app.post("/api/providers/{provider_id}/test")
async def test_provider(
    provider_id: str, payload: ProviderTestInput, _: AuthContext = Depends(write_auth)
) -> dict[str, Any]:
    """Proefverbinding vóór opslaan: gebruikt de opgegeven gegevens, bewaart niets."""
    provider = database.fetch_one("SELECT type FROM providers WHERE id=?", (provider_id,))
    if not provider:
        raise HTTPException(404, "Provider niet gevonden")
    allowed = {field["key"] for field in PROVIDER_CREDENTIAL_FIELDS.get(provider["type"], [])}
    if not set(payload.credentials).issubset(allowed):
        raise HTTPException(422, "Onbekend credentialveld voor deze provider")
    return await providers.test_one(provider_id, payload.config, payload.credentials)


@app.post("/api/providers/{provider_id}/sync")
async def sync_provider(provider_id: str, _: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    try:
        return await providers.sync_one(provider_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/provider-records/{record_id}/mapping")
def update_provider_mapping(record_id: str, payload: ProviderMappingInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    record = database.fetch_one("SELECT * FROM provider_records WHERE id=?", (record_id,))
    if not record:
        raise HTTPException(404, "Providerrecord niet gevonden")
    if payload.entity_id and not database.fetch_one("SELECT id FROM entities WHERE id=?", (payload.entity_id,)):
        raise HTTPException(404, "Doeldevice niet gevonden")
    with database.transaction() as connection:
        connection.execute("UPDATE provider_records SET entity_id=? WHERE id=?", (payload.entity_id, record_id))
    database.audit(auth.user_id, "provider_record.map", "provider_record", record_id, payload.model_dump())
    return {"ok": True}


@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, payload: ConflictInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        cursor = connection.execute(
            "UPDATE conflicts SET status='resolved',resolved_at=?,resolution=? WHERE id=? AND status='open'",
            (utcnow(), payload.resolution, conflict_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Open conflict niet gevonden")
    database.audit(auth.user_id, "conflict.resolve", "conflict", conflict_id, payload.model_dump())
    return {"ok": True}


def list_backups() -> list[dict[str, Any]]:
    candidates = [*settings.backup_dir.glob("patch-manager-*.pmbackup"), *settings.backup_dir.glob("patch-manager-*.db")]
    return [
        {"name": path.name, "size": path.stat().st_size,
         "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
         "portable": path.suffix == ".pmbackup"}
        for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[:50]
    ]


@app.get("/api/backups")
def backups_list(_: AuthContext = Depends(current_auth)) -> list[dict[str, Any]]:
    return list_backups()


@app.post("/api/backups")
def backup_create(auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    path = create_portable_backup()
    database.prune_backups(settings.backup_dir, settings.backup_retention_daily)
    database.audit(auth.user_id, "backup.create", "backup", path.name)
    return {"name": path.name, "size": path.stat().st_size}


def backup_path(name: str) -> Path:
    if Path(name).name != name or not name.startswith("patch-manager-") or Path(name).suffix not in {".db", ".pmbackup"}:
        raise HTTPException(400, "Ongeldige back-upnaam")
    path = settings.backup_dir / name
    if not path.is_file():
        raise HTTPException(404, "Back-up niet gevonden")
    return path


@app.get("/api/backups/{name}/download")
def backup_download(name: str, _: AuthContext = Depends(current_auth)) -> FileResponse:
    path = backup_path(name)
    media_type = "application/zip" if path.suffix == ".pmbackup" else "application/vnd.sqlite3"
    return FileResponse(path, filename=path.name, media_type=media_type)


@app.post("/api/backups/import")
async def backup_import(file: UploadFile = File(...), auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.backup_dir / f".patch-manager-upload-{uuid.uuid4().hex}.tmp"
    size = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > BACKUP_IMPORT_MAX_BYTES:
                    raise HTTPException(413, "Back-up is groter dan 512 MB")
                handle.write(chunk)
        metadata = database.validate_backup(temporary)
        suffix = ".pmbackup" if metadata["portable"] else ".db"
        path = settings.backup_dir / f"patch-manager-imported-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}{suffix}"
        temporary.replace(path)
        path.chmod(0o600)
        database.prune_backups(settings.backup_dir, settings.backup_retention_daily)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(422, f"Ongeldige Patch Manager-back-up: {exc}") from exc
    database.audit(auth.user_id, "backup.import", "backup", path.name, {"original_name": file.filename, "size": size})
    return {"name": path.name, "size": size, "portable": suffix == ".pmbackup"}


@app.post("/api/backups/{name}/restore")
def backup_restore(name: str, confirm: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    path = backup_path(name)
    if confirm != name:
        raise HTTPException(422, "De bevestigingsnaam komt niet overeen")
    safety = create_portable_backup()
    try:
        key_changed = database.restore_backup(path, SECRET_KEY_PATH)
    except (RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise HTTPException(422, f"Back-up kan niet worden hersteld: {exc}") from exc
    if key_changed:
        provider_secrets.reload()
    actor = auth.user_id if database.fetch_one("SELECT id FROM users WHERE id=?", (auth.user_id,)) else None
    database.audit(actor, "backup.restore", "backup", name, {"safety_backup": safety.name})
    return {"ok": True}


CONFIG_TABLES = (
    "providers", "entities", "physical_devices", "ports", "cables",
    "topology_nodes", "topology_relations", "dns_records", "speedtest_settings",
)


@app.get("/api/config/export")
def config_export(_: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in CONFIG_TABLES:
        where = " WHERE source='manual'" if table == "dns_records" else ""
        tables[table] = database.fetch_all(f"SELECT * FROM {table}{where}")
    return {
        "format": "plugnet-config",
        "version": 1,
        "exported_at": utcnow(),
        "settings": {"title": app_title()},
        "tables": tables,
    }


@app.post("/api/config/import")
def config_import(payload: dict[str, Any], auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    if payload.get("format") != "plugnet-config" or payload.get("version") != 1 or not isinstance(payload.get("tables"), dict):
        raise HTTPException(422, "Dit is geen ondersteund Plugnet-configuratiebestand")
    create_portable_backup()
    database.prune_backups(settings.backup_dir, settings.backup_retention_daily)
    imported = 0
    entity_parents: list[tuple[str, str]] = []
    entity_uplinks: list[tuple[str, str]] = []
    physical_monitors: list[tuple[str, str]] = []
    try:
        with database.transaction() as connection:
            imported_settings = payload.get("settings") or {}
            if isinstance(imported_settings, dict) and isinstance(imported_settings.get("title"), str):
                title = " ".join(imported_settings["title"].split())[:80]
                if len(title) >= 2:
                    connection.execute(
                        """INSERT INTO app_meta(key,value) VALUES('app_title',?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                        (title,),
                    )
            for table in CONFIG_TABLES:
                rows = payload["tables"].get(table, [])
                if not isinstance(rows, list):
                    raise ValueError(f"Tabel {table} is ongeldig")
                allowed = [row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]
                primary = next((row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall() if row[5]), None)
                if not primary:
                    raise ValueError(f"Tabel {table} heeft geen primaire sleutel")
                for raw in rows:
                    if not isinstance(raw, dict) or primary not in raw:
                        raise ValueError(f"Record in {table} mist {primary}")
                    row = {key: raw[key] for key in allowed if key in raw}
                    if table == "entities" and row.get("parent_id"):
                        entity_parents.append((str(row["parent_id"]), str(row[primary])))
                        row["parent_id"] = None
                    if table == "entities" and row.get("uplink_device_id"):
                        entity_uplinks.append((str(row["uplink_device_id"]), str(row[primary])))
                        row["uplink_device_id"] = None
                    if table == "physical_devices" and row.get("monitor_entity_id"):
                        physical_monitors.append((str(row["monitor_entity_id"]), str(row[primary])))
                        row["monitor_entity_id"] = None
                    if table == "cables":
                        row["updated_by"] = None
                    if table == "ports":
                        # Front/rear-paren worden na de tabel opnieuw gelegd.
                        row["peer_port_id"] = None
                    if table == "topology_nodes":
                        row["parent_node_id"] = None
                    columns = list(row)
                    updates = [column for column in columns if column != primary]
                    sql = (
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                        f"ON CONFLICT({primary}) DO UPDATE SET " + ",".join(f"{column}=excluded.{column}" for column in updates)
                    )
                    connection.execute(sql, tuple(row[column] for column in columns))
                    imported += 1
            for parent_id, entity_id in entity_parents:
                connection.execute("UPDATE entities SET parent_id=? WHERE id=?", (parent_id, entity_id))
            for device_id, entity_id in entity_uplinks:
                connection.execute(
                    "UPDATE entities SET uplink_device_id=? WHERE id=?", (device_id, entity_id)
                )
            for entity_id, device_id in physical_monitors:
                connection.execute(
                    "UPDATE physical_devices SET monitor_entity_id=? WHERE id=?", (entity_id, device_id)
                )
            for raw in payload["tables"].get("ports", []):
                if isinstance(raw, dict) and raw.get("peer_port_id"):
                    connection.execute(
                        "UPDATE ports SET peer_port_id=? WHERE id=?", (raw["peer_port_id"], raw["id"])
                    )
            for raw in payload["tables"].get("topology_nodes", []):
                if raw.get("parent_node_id"):
                    connection.execute(
                        "UPDATE topology_nodes SET parent_node_id=? WHERE id=?",
                        (raw["parent_node_id"], raw["id"]),
                    )
    except (sqlite3.Error, ValueError) as exc:
        raise HTTPException(422, f"Configuratie kon niet worden geïmporteerd: {exc}") from exc
    database.audit(auth.user_id, "config.import", "config", None, {"records": imported})
    return {"ok": True, "records": imported}


@app.patch("/api/topology/nodes/{node_id}")
def update_topology_node(node_id: str, payload: TopologyNodeInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    if payload.parent_node_id == node_id:
        raise HTTPException(422, "Een node kan niet zijn eigen parent zijn")
    if payload.parent_node_id and not database.fetch_one("SELECT id FROM topology_nodes WHERE id=?", (payload.parent_node_id,)):
        raise HTTPException(404, "Parentnode niet gevonden")
    if payload.parent_node_id:
        descendants = database.fetch_all(
            """WITH RECURSIVE tree(id) AS (
                 SELECT id FROM topology_nodes WHERE parent_node_id=?
                 UNION ALL SELECT n.id FROM topology_nodes n JOIN tree t ON n.parent_node_id=t.id
               ) SELECT id FROM tree""",
            (node_id,),
        )
        if payload.parent_node_id in {item["id"] for item in descendants}:
            raise HTTPException(422, "Deze parentkeuze zou een cirkel in de topologie maken")
    capture_topology(database, "node bijgewerkt", auth.user_id)
    with database.transaction() as connection:
        cursor = connection.execute(
            """UPDATE topology_nodes SET label=?,subtitle=?,parent_node_id=?,parent_source='manual',lifecycle=?,
               x=COALESCE(?,x),y=COALESCE(?,y),manual_position=CASE WHEN ? IS NULL AND ? IS NULL THEN manual_position ELSE 1 END,
               collapsed=?,hidden=?,metadata_json=json_set(metadata_json,'$.customized',1),updated_at=? WHERE id=?""",
            (payload.label, payload.subtitle, payload.parent_node_id, payload.lifecycle, payload.x, payload.y,
             payload.x, payload.y, int(payload.collapsed), int(payload.hidden), utcnow(), node_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Topologienode niet gevonden")
    database.audit(auth.user_id, "topology.node.update", "topology_node", node_id, payload.model_dump())
    return {"ok": True}


@app.patch("/api/topology/nodes/{node_id}/position")
def move_topology_node(node_id: str, payload: TopologyPositionInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    if not database.fetch_one("SELECT id FROM topology_nodes WHERE id=?", (node_id,)):
        raise HTTPException(404, "Topologienode niet gevonden")
    capture_topology(database, "node verplaatst", auth.user_id)
    with database.transaction() as connection:
        cursor = connection.execute(
            "UPDATE topology_nodes SET x=?,y=?,manual_position=1,updated_at=? WHERE id=?",
            (payload.x, payload.y, utcnow(), node_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Topologienode niet gevonden")
    database.audit(auth.user_id, "topology.node.move", "topology_node", node_id, payload.model_dump())
    return {"ok": True}


@app.patch("/api/topology/positions")
def move_topology_nodes(payload: TopologyPositionsInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    parsed: list[tuple[float, float, str]] = []
    for item in payload.positions:
        try:
            parsed.append((float(item["x"]), float(item["y"]), str(item["id"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "Ongeldige nodepositie") from exc
    capture_topology(database, f"{len(parsed)} nodes verplaatst", auth.user_id)
    with database.transaction() as connection:
        for x, y, node_id in parsed:
            connection.execute(
                "UPDATE topology_nodes SET x=?,y=?,manual_position=1,updated_at=? WHERE id=?",
                (x, y, utcnow(), node_id),
            )
    database.audit(auth.user_id, "topology.nodes.move", "topology", None, {"count": len(parsed)})
    return {"ok": True}


@app.post("/api/topology/groups")
def topology_group_create(payload: TopologyGroupInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    capture_topology(database, "groep toegevoegd", auth.user_id)
    group = create_group(database, payload.label, payload.subtitle)
    if payload.node_ids:
        with database.transaction() as connection:
            for node_id in payload.node_ids:
                if node_id != group["id"]:
                    connection.execute(
                        "UPDATE topology_nodes SET parent_node_id=?,parent_source='manual',updated_at=? WHERE id=?",
                        (group["id"], utcnow(), node_id),
                    )
    database.audit(auth.user_id, "topology.group.create", "topology_node", group["id"], payload.model_dump())
    return group


@app.patch("/api/topology/group-selection")
def topology_group_selection(payload: TopologyGroupSelectionInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    if payload.parent_node_id in payload.node_ids:
        raise HTTPException(422, "Een groep kan niet zichzelf bevatten")
    if not database.fetch_one("SELECT id FROM topology_nodes WHERE id=? AND reference_type='group'", (payload.parent_node_id,)):
        raise HTTPException(404, "Doelgroep niet gevonden")
    placeholders = ",".join("?" for _ in payload.node_ids)
    existing_nodes = {
        row["id"] for row in database.fetch_all(
            f"SELECT id FROM topology_nodes WHERE id IN ({placeholders})", tuple(payload.node_ids)
        )
    }
    if existing_nodes != set(payload.node_ids):
        raise HTTPException(404, "Een of meer geselecteerde nodes bestaan niet")
    for node_id in payload.node_ids:
        descendants = database.fetch_all(
            """WITH RECURSIVE tree(id) AS (
                 SELECT id FROM topology_nodes WHERE parent_node_id=?
                 UNION ALL SELECT n.id FROM topology_nodes n JOIN tree t ON n.parent_node_id=t.id
               ) SELECT id FROM tree""",
            (node_id,),
        )
        if payload.parent_node_id in {row["id"] for row in descendants}:
            raise HTTPException(422, "Deze groepskeuze zou een cirkel in de topologie maken")
    capture_topology(database, f"{len(payload.node_ids)} nodes gegroepeerd", auth.user_id)
    with database.transaction() as connection:
        for node_id in payload.node_ids:
            connection.execute(
                "UPDATE topology_nodes SET parent_node_id=?,parent_source='manual',updated_at=? WHERE id=?",
                (payload.parent_node_id, utcnow(), node_id),
            )
    database.audit(auth.user_id, "topology.nodes.group", "topology_node", payload.parent_node_id, {"node_ids": payload.node_ids})
    return {"ok": True}


@app.post("/api/topology/relations")
def topology_relation_create(payload: TopologyRelationInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    try:
        capture_topology(database, "relatie toegevoegd", auth.user_id)
        relation = create_relation(database, payload.from_node_id, payload.to_node_id, payload.relation_type, payload.label)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    database.audit(auth.user_id, "topology.relation.create", "topology_relation", relation["id"], payload.model_dump())
    return relation


@app.delete("/api/topology/relations/{relation_id}")
def topology_relation_delete(relation_id: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    relation = database.fetch_one("SELECT * FROM topology_relations WHERE id=?", (relation_id,))
    if not relation:
        raise HTTPException(404, "Relatie niet gevonden")
    if relation["source"] != "manual":
        raise HTTPException(409, "Alleen handmatige relaties kunnen hier worden verwijderd")
    capture_topology(database, "relatie verwijderd", auth.user_id)
    with database.transaction() as connection:
        connection.execute("DELETE FROM topology_relations WHERE id=?", (relation_id,))
    database.audit(auth.user_id, "topology.relation.delete", "topology_relation", relation_id)
    return {"ok": True}


@app.post("/api/topology/layout/reset")
def topology_layout_reset(auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    capture_topology(database, "indeling hersteld", auth.user_id)
    with database.transaction() as connection:
        connection.execute("UPDATE topology_nodes SET manual_position=0,x=NULL,y=NULL,updated_at=?", (utcnow(),))
    database.audit(auth.user_id, "topology.layout.reset", "topology", None)
    return {"ok": True}


@app.delete("/api/topology/groups/{group_id}")
def topology_group_delete(group_id: str, confirm: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    group = database.fetch_one("SELECT * FROM topology_nodes WHERE id=? AND reference_type='group'", (group_id,))
    if not group:
        raise HTTPException(404, "Topologiegroep niet gevonden")
    if confirm != group["label"]:
        raise HTTPException(422, "De bevestigingsnaam komt niet overeen")
    capture_topology(database, "groep verwijderd", auth.user_id)
    with database.transaction() as connection:
        connection.execute("DELETE FROM topology_nodes WHERE id=?", (group_id,))
    database.audit(auth.user_id, "topology.group.delete", "topology_node", group_id, {"label": group["label"]})
    return {"ok": True}


@app.post("/api/topology/undo")
def topology_undo(auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    action = undo_topology(database)
    if not action:
        raise HTTPException(409, "Er is niets om ongedaan te maken")
    database.audit(auth.user_id, "topology.undo", "topology", None, {"undone": action})
    return {"ok": True, "undone": action}


@app.get("/api/speedtest")
def speedtest_get(_: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    return speedtests.payload()


@app.post("/api/speedtest/run")
async def speedtest_run(_: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    return await speedtests.run()


@app.patch("/api/speedtest/settings")
def speedtest_settings_update(payload: SpeedtestSettingsInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    with database.transaction() as connection:
        connection.execute(
            """UPDATE speedtest_settings SET enabled=?,interval_seconds=?,server_id=?,interface_name=?,
               duration_seconds=?,telemetry_enabled=0,updated_at=? WHERE id=1""",
            (int(payload.enabled), payload.interval_seconds, payload.server_id or None,
             payload.interface_name or None, payload.duration_seconds, utcnow()),
        )
    database.audit(auth.user_id, "speedtest.settings.update", "speedtest", "1", payload.model_dump())
    return {"ok": True}


def resolve_dns_entity(entity_id: str | None) -> str | None:
    """Een leeg veld is toegestaan; een ingevuld veld moet bestaan.

    Zonder deze controle sloeg de foreign key op dns_records.entity_id pas toe
    tijdens de INSERT en gaf dat een kale 500 op iets wat gewoon een 404 hoort
    te zijn."""
    entity_id = (entity_id or "").strip() or None
    if entity_id and not database.fetch_one("SELECT id FROM entities WHERE id=?", (entity_id,)):
        raise HTTPException(404, "Gekoppeld device niet gevonden")
    return entity_id


@app.post("/api/dns-records")
def dns_record_create(payload: DnsRecordInput, auth: AuthContext = Depends(write_auth)) -> dict[str, Any]:
    entity_id = resolve_dns_entity(payload.entity_id)
    record_id = str(uuid.uuid4())
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO dns_records
               (id,name,record_type,value,ttl,enabled,source,entity_id,manual_locked,created_at,updated_at)
               VALUES(?,?,?,?,?,?,'manual',?,1,?,?)""",
            (record_id, payload.name.rstrip("."), payload.record_type, payload.value, payload.ttl,
             int(payload.enabled), entity_id, now, now),
        )
    database.audit(auth.user_id, "dns.create", "dns_record", record_id, payload.model_dump())
    return database.fetch_one("SELECT * FROM dns_records WHERE id=?", (record_id,))


@app.patch("/api/dns-records/{record_id}")
def dns_record_update(record_id: str, payload: DnsRecordInput, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    record = database.fetch_one("SELECT source FROM dns_records WHERE id=?", (record_id,))
    if not record:
        raise HTTPException(404, "DNS-record niet gevonden")
    if record["source"] != "manual":
        raise HTTPException(409, "Een geïmporteerd record wijzig je in AdGuard Home")
    entity_id = resolve_dns_entity(payload.entity_id)
    with database.transaction() as connection:
        cursor = connection.execute(
            """UPDATE dns_records SET name=?,record_type=?,value=?,ttl=?,enabled=?,entity_id=?,
               manual_locked=1,updated_at=? WHERE id=?""",
            (payload.name.rstrip("."), payload.record_type, payload.value, payload.ttl,
             int(payload.enabled), entity_id, utcnow(), record_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "DNS-record niet gevonden")
    database.audit(auth.user_id, "dns.update", "dns_record", record_id, payload.model_dump())
    return {"ok": True}


@app.delete("/api/dns-records/{record_id}")
def dns_record_delete(record_id: str, auth: AuthContext = Depends(write_auth)) -> dict[str, bool]:
    record = database.fetch_one("SELECT source FROM dns_records WHERE id=?", (record_id,))
    if not record:
        raise HTTPException(404, "DNS-record niet gevonden")
    if record["source"] != "manual":
        raise HTTPException(409, "Een geïmporteerd record verwijder je in AdGuard Home")
    with database.transaction() as connection:
        connection.execute("DELETE FROM dns_records WHERE id=?", (record_id,))
    database.audit(auth.user_id, "dns.delete", "dns_record", record_id)
    return {"ok": True}
