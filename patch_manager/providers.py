from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .db import Database, utcnow
from .oui import vendor_for_mac
from .secret_store import SecretStore


logger = logging.getLogger(__name__)

MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")

# Vlag ATF_COM in /proc/net/arp: het adres is echt opgelost. Staat hij niet aan,
# dan is het een lege buur met MAC 00:00:00:00:00:00.
ARP_FLAG_COMPLETE = 0x2
# Hardgecodeerd met opzet: op een thuisnetwerk hoeft hier geen knop aan te
# zitten. 48 gelijktijdige pings houdt een /22 binnen een minuut.
PING_CONCURRENCY = 48
SCAN_TIMEOUT_SECONDS = 180
EMPTY_MAC = "00:00:00:00:00:00"


def normalize_mac(value: str | None) -> str | None:
    """Een MAC-adres in kleine letters, of None als het er geen is.

    De nul-MAC telt niet: hij is syntactisch geldig maar hoort bij geen enkel
    apparaat. Zou hij wel blijven staan, dan koppelt _store_record elk
    apparaat met die MAC aan dezelfde entity.
    """
    if not value:
        return None
    match = MAC_RE.fullmatch(value.strip().replace("-", ":"))
    if not match:
        return None
    mac = match.group(0).lower()
    return None if mac == EMPTY_MAC else mac


def parse_arp_table(text: str) -> dict[str, dict[str, str]]:
    """De opgeloste buren uit /proc/net/arp, op IP.

    Een ping-sweep laat voor élk adres waar niets op zit een onvolledige buur
    achter: vlaggen 0x0 en MAC 00:00:00:00:00:00. Zonder deze twee controles
    levert een scan over een /22 honderden 'apparaten' op die niet bestaan.
    """
    neighbours: dict[str, dict[str, str]] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        mac = normalize_mac(parts[3])
        if not mac:
            continue
        try:
            flags = int(parts[2], 16)
        except ValueError:
            continue
        if not flags & ARP_FLAG_COMPLETE:
            continue
        neighbours[parts[0]] = {"ip": parts[0], "mac": mac}
    return neighbours


def is_ip_literal(value: Any) -> bool:
    try:
        ipaddress.ip_address(str(value).strip())
        return True
    except ValueError:
        return False


def reverse_hostname(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, TimeoutError):
        return None


class ProviderManager:
    """Read-only provider adapters; only observations may update discovered fields."""

    def __init__(self, database: Database, secrets: SecretStore, trusted_subnets: tuple[str, ...] = ("127.0.0.0/8",)):
        self.database = database
        self.secrets = secrets
        self.trusted_networks = tuple(ipaddress.ip_network(value, strict=False) for value in trusted_subnets)
        self._locks: dict[str, asyncio.Lock] = {}

    def _network_is_trusted(self, network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
        return any(network.version == trusted.version and network.subnet_of(trusted) for trusted in self.trusted_networks)

    def _address_is_trusted(self, value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return any(address.version == trusted.version and address in trusted for trusted in self.trusted_networks)

    async def sync_enabled(self) -> None:
        providers = self.database.fetch_all("SELECT id FROM providers WHERE enabled=1")
        for provider in providers:
            try:
                await self.sync_one(provider["id"])
            except Exception:
                # Per-provider failures are persisted and must not stop other collectors.
                logger.exception("Synchronisatie van provider %s is mislukt", provider["id"])
                continue

    async def sync_one(self, provider_id: str) -> dict[str, Any]:
        provider = self.database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not provider:
            raise ValueError("Onbekende provider")
        lock = self._locks.setdefault(provider_id, asyncio.Lock())
        if lock.locked():
            return {"status": "busy", "message": "Synchronisatie loopt al"}
        async with lock:
            started = utcnow()
            with self.database.transaction() as connection:
                connection.execute("UPDATE providers SET last_run_at=?, last_error=NULL WHERE id=?", (started, provider_id))
            try:
                config = json.loads(provider["config_json"] or "{}")
                handler = getattr(self, f"_sync_{provider['type']}")
                records = await handler(provider, config)
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE providers SET last_success_at=?,last_error=NULL WHERE id=?",
                        (utcnow(), provider_id),
                    )
                return {"status": "ok", "records": records}
            except Exception as exc:
                message = str(exc)[:500]
                with self.database.transaction() as connection:
                    connection.execute("UPDATE providers SET last_error=? WHERE id=?", (message, provider_id))
                raise RuntimeError(message) from exc

    def _store_record(
        self,
        provider_id: str,
        external_id: str,
        kind: str,
        raw: dict[str, Any],
        *,
        name: str,
        entity_type: str,
        status: str = "unknown",
        ip_address: str | None = None,
        mac_address: str | None = None,
        hostname: str | None = None,
        parent_id: str | None = None,
        bind_entity_id: str | None = None,
    ) -> str:
        now = utcnow()
        mac_address = normalize_mac(mac_address)
        vendor = vendor_for_mac(mac_address)
        existing_record = self.database.fetch_one(
            "SELECT entity_id FROM provider_records WHERE provider_id=? AND external_id=?",
            (provider_id, external_id),
        )
        entity = None
        # Een expliciete koppeling uit de configuratie (Glances-endpoint → device)
        # gaat vóór alle raadwerk hieronder. Bestaat het device niet meer, dan
        # valt hij terug op de normale matching in plaats van te crashen.
        if bind_entity_id:
            entity = self.database.fetch_one("SELECT * FROM entities WHERE id=?", (bind_entity_id,))
        if not entity and existing_record and existing_record["entity_id"]:
            entity = self.database.fetch_one("SELECT * FROM entities WHERE id=?", (existing_record["entity_id"],))
        if not entity and mac_address:
            entity = self.database.fetch_one("SELECT * FROM entities WHERE lower(mac_address)=?", (mac_address,))
        # Docker IDs are stable across Glances and Portainer. Merge only on a
        # sufficiently long native ID, never on a display name or IP alone.
        native_container_id = raw.get("id") or raw.get("Id")
        if not entity and kind == "container" and native_container_id and len(str(native_container_id)) >= 12:
            matching_record = self.database.fetch_one(
                """SELECT entity_id FROM provider_records
                   WHERE kind='container' AND external_id LIKE ? AND entity_id IS NOT NULL LIMIT 1""",
                (f"%:{native_container_id}",),
            )
            if matching_record:
                entity = self.database.fetch_one("SELECT * FROM entities WHERE id=?", (matching_record["entity_id"],))
        # Hostnames are considered strong only for already-discovered hosts.
        # Manual hosts still require MAC or an explicit user mapping.
        if not entity and entity_type == "host" and name:
            entity = self.database.fetch_one(
                "SELECT * FROM entities WHERE origin='discovered' AND type='host' AND lower(name)=lower(?) LIMIT 1",
                (name,),
            )

        if entity:
            entity_id = entity["id"]
            if entity["origin"] == "discovered":
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE entities SET name=?,type=?,status=?,status_updated_at=?,
                           ip_address=COALESCE(?,ip_address),mac_address=COALESCE(?,mac_address),
                           hostname=COALESCE(?,hostname),parent_id=COALESCE(?,parent_id),
                           vendor=COALESCE(?,vendor),last_seen_at=?,updated_at=? WHERE id=?""",
                        (name, entity_type, status, now, ip_address, mac_address, hostname, parent_id, vendor, now, now, entity_id),
                    )
            else:
                # Bij een expliciete koppeling is een afwijkende naam geen
                # conflict maar de bedoeling: je hebt zelf gezegd dat dit
                # hetzelfde apparaat is.
                if not bind_entity_id:
                    self._record_manual_conflicts(entity, provider_id, {"name": name, "ip_address": ip_address, "mac_address": mac_address})
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE entities SET status=?,status_updated_at=?,last_seen_at=? WHERE id=?",
                        (status, now, now, entity_id),
                    )
        else:
            entity_id = str(uuid.uuid4())
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO entities
                       (id,name,type,origin,status,status_updated_at,ip_address,mac_address,hostname,parent_id,
                        vendor,first_seen_at,last_seen_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (entity_id, name, entity_type, "discovered", status, now, ip_address, mac_address, hostname, parent_id, vendor, now, now, now, now),
                )

        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO provider_records
                   (id,provider_id,external_id,entity_id,kind,raw_json,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider_id,external_id) DO UPDATE SET
                   entity_id=excluded.entity_id,kind=excluded.kind,raw_json=excluded.raw_json,last_seen_at=excluded.last_seen_at""",
                (str(uuid.uuid4()), provider_id, external_id, entity_id, kind, json.dumps(raw), now, now),
            )
            provider_row = connection.execute(
                "SELECT poll_interval_seconds FROM providers WHERE id=?", (provider_id,)
            ).fetchone()
            ttl_seconds = max(300, int(provider_row[0]) * 3 if provider_row else 600)
            expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
            for field, value in {"status": status, "ip_address": ip_address, "hostname": hostname}.items():
                if value is not None:
                    connection.execute(
                        "DELETE FROM observations WHERE entity_id=? AND provider_id=? AND field=?",
                        (entity_id, provider_id, field),
                    )
                    connection.execute(
                        """INSERT INTO observations(id,entity_id,provider_id,field,value_json,observed_at,expires_at,confidence)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), entity_id, provider_id, field, json.dumps(value), now, expires, 1.0),
                    )
        return entity_id

    def _record_manual_conflicts(self, entity: dict[str, Any], provider_id: str, values: dict[str, Any]) -> None:
        for field in ("name", "mac_address"):
            observed = values.get(field)
            manual = entity.get(field)
            if observed and manual and str(observed).lower() != str(manual).lower():
                existing = self.database.fetch_one(
                    """SELECT id FROM conflicts
                       WHERE entity_id=? AND provider_id=? AND field=?
                         AND lower(COALESCE(manual_value,''))=lower(?)
                         AND lower(COALESCE(observed_value,''))=lower(?)
                       LIMIT 1""",
                    (entity["id"], provider_id, field, str(manual), str(observed)),
                )
                if not existing:
                    with self.database.transaction() as connection:
                        connection.execute(
                            """INSERT INTO conflicts(id,entity_id,provider_id,field,manual_value,observed_value,status,created_at,resolved_at,resolution)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (str(uuid.uuid4()), entity["id"], provider_id, field, str(manual), str(observed), "open", utcnow(), None, None),
                        )

    async def _sync_dhcp_arp(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        found: dict[str, dict[str, str]] = {}
        arp_path = Path("/proc/net/arp")

        def read_arp_table() -> None:
            if arp_path.exists():
                found.update(parse_arp_table(arp_path.read_text()))

        read_arp_table()

        scanned: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        if config.get("scan"):
            semaphore = asyncio.Semaphore(PING_CONCURRENCY)

            async def ping(ip: str) -> None:
                async with semaphore:
                    try:
                        # Twee pakketten, niet één: op wifi raakt er geregeld
                        # eentje kwijt, en met één probe zou dat apparaat als
                        # down in de historie belanden. Alleen vlaggen die
                        # overal bestaan; -i is niet op elke ping beschikbaar.
                        process = await asyncio.create_subprocess_exec(
                            "ping", "-c", "2", "-W", "1", ip,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        if await process.wait() == 0:
                            found.setdefault(ip, {"ip": ip, "mac": ""})
                    except FileNotFoundError:
                        return

            for subnet_value in config.get("subnets", [])[:8]:
                network = ipaddress.ip_network(subnet_value, strict=False)
                if not self._network_is_trusted(network):
                    raise ValueError(f"Subnet {network} valt buiten PATCH_TRUSTED_SUBNETS")
                if network.num_addresses > 1024:
                    raise ValueError(f"Subnet {network} bevat meer dan 1024 adressen")
                # Een hangende ping mag de hele synchronisatie niet ophouden;
                # wat binnen is, is binnen.
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(ping(str(ip)) for ip in network.hosts())),
                        timeout=SCAN_TIMEOUT_SECONDS,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    logger.warning("Scan van %s afgekapt na %ss", network, SCAN_TIMEOUT_SECONDS)
                scanned.append(network)
            read_arp_table()

        present = [item for item in found.values() if self._address_is_trusted(item["ip"])]

        # Reverse lookups parallel: serieel is dit bij tweehonderd apparaten
        # met een timeout van twee seconden al langer dan het pollinterval.
        async def hostname_for(ip: str) -> str | None:
            try:
                return await asyncio.wait_for(asyncio.to_thread(reverse_hostname, ip), timeout=2)
            except (TimeoutError, asyncio.TimeoutError):
                return None

        hostnames = dict(zip(
            [item["ip"] for item in present],
            await asyncio.gather(*(hostname_for(item["ip"]) for item in present)),
        ))

        count = 0
        for item in present:
            hostname = hostnames.get(item["ip"])
            await asyncio.to_thread(
                self._store_record,
                provider["id"],
                item["ip"],
                "network_device",
                item,
                name=hostname or item["ip"],
                entity_type="device",
                status="up",
                ip_address=item["ip"],
                mac_address=item["mac"] or None,
                hostname=hostname,
            )
            count += 1

        if scanned:
            count += await asyncio.to_thread(
                self._mark_absent, provider["id"], scanned, {item["ip"] for item in present}
            )
        return count

    def _mark_absent(
        self,
        provider_id: str,
        scanned: list[Any],
        present: set[str],
    ) -> int:
        """Zet apparaten die we actief zochten maar niet vonden op 'down'.

        Zonder dit blijft alles voor altijd 'up': de adapter meldde alleen wat
        hij vond. Een uitgezet apparaat viel daarna hooguit terug op 'unknown'
        als de observatie verliep, en dat is te slap — we hebben gekeken en
        niets gekregen, dat is bewijs. Alleen adressen binnen de gescande
        subnetten tellen mee; buiten dat bereik weten we niets.
        """
        rows = self.database.fetch_all(
            """SELECT e.id,e.ip_address FROM entities e
               JOIN provider_records pr ON pr.entity_id=e.id
               WHERE pr.provider_id=? AND e.ip_address IS NOT NULL
                 AND e.status!='down' AND e.archived=0""",
            (provider_id,),
        )
        absent = []
        for row in rows:
            if row["ip_address"] in present:
                continue
            try:
                address = ipaddress.ip_address(row["ip_address"])
            except ValueError:
                continue
            if any(address in network for network in scanned):
                absent.append(row["id"])
        if not absent:
            return 0
        now = utcnow()
        provider_row = self.database.fetch_one(
            "SELECT poll_interval_seconds FROM providers WHERE id=?", (provider_id,)
        )
        ttl = max(300, int(provider_row["poll_interval_seconds"]) * 3 if provider_row else 600)
        expires = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(timespec="seconds")
        with self.database.transaction() as connection:
            for entity_id in absent:
                # last_seen_at blijft staan: dat is wanneer het ding er nog wél
                # was, en die betekenis wil je niet kwijt.
                connection.execute(
                    "UPDATE entities SET status='down',status_updated_at=?,updated_at=? WHERE id=?",
                    (now, now, entity_id),
                )
                connection.execute(
                    "DELETE FROM observations WHERE entity_id=? AND provider_id=? AND field='status'",
                    (entity_id, provider_id),
                )
                connection.execute(
                    """INSERT INTO observations(id,entity_id,provider_id,field,value_json,observed_at,expires_at,confidence)
                       VALUES(?,?,?,'status',?,?,?,1.0)""",
                    (str(uuid.uuid4()), entity_id, provider_id, json.dumps("down"), now, expires),
                )
        return len(absent)

    async def _sync_uptime_kuma(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        base_url = str(config.get("base_url", "")).rstrip("/")
        slug = config.get("status_page_slug")
        if not base_url or not slug:
            raise ValueError("Base URL en statuspagina-slug zijn verplicht")
        async with httpx.AsyncClient(timeout=15) as client:
            page_response = await client.get(f"{base_url}/api/status-page/{slug}")
            page_response.raise_for_status()
            heartbeat_response = await client.get(f"{base_url}/api/status-page/heartbeat/{slug}")
            heartbeat_response.raise_for_status()
        page = page_response.json()
        heartbeats = heartbeat_response.json().get("heartbeatList", {})
        count = 0
        for group in page.get("publicGroupList", []):
            for monitor in group.get("monitorList", []):
                monitor_id = str(monitor.get("id"))
                latest = (heartbeats.get(monitor_id) or [{}])[-1]
                state = {1: "up", 0: "down", 2: "degraded"}.get(latest.get("status"), "unknown")
                await asyncio.to_thread(
                    self._store_record,
                    provider["id"], monitor_id, "service", {"monitor": monitor, "heartbeat": latest},
                    name=monitor.get("name") or f"Monitor {monitor_id}", entity_type="service", status=state,
                )
                count += 1
        return count

    async def _sync_glances(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        count = 0
        auth = None
        credentials = self.secrets.get(provider["id"])
        username = credentials.get("username")
        password = credentials.get("password")
        if username and password:
            auth = (username, password)
        endpoints = config.get("endpoints", []) or []
        missing = [
            endpoint.get("name") or endpoint.get("url") or "naamloos"
            for endpoint in endpoints
            if str(endpoint.get("url", "")).strip() and not endpoint.get("entity_id")
        ]
        if missing:
            raise ValueError(f"Geen device gekozen voor endpoint: {', '.join(missing)}")
        async with httpx.AsyncClient(timeout=15, auth=auth) as client:
            for endpoint in endpoints:
                base_url = str(endpoint.get("url", "")).rstrip("/")
                if not base_url:
                    continue
                bind_entity_id = endpoint.get("entity_id")
                responses = await asyncio.gather(
                    client.get(f"{base_url}/system"), client.get(f"{base_url}/ip"),
                    client.get(f"{base_url}/containers"), client.get(f"{base_url}/quicklook"),
                    client.get(f"{base_url}/mem"), return_exceptions=True,
                )
                if isinstance(responses[0], Exception):
                    raise responses[0]
                system = responses[0].json() if responses[0].status_code == 200 else {}
                ip_data = responses[1].json() if not isinstance(responses[1], Exception) and responses[1].status_code == 200 else {}
                host_name = system.get("hostname") or endpoint.get("name") or base_url
                quicklook = responses[3].json() if not isinstance(responses[3], Exception) and responses[3].status_code == 200 else {}
                memory = responses[4].json() if not isinstance(responses[4], Exception) and responses[4].status_code == 200 else {}
                host_id = await asyncio.to_thread(
                    self._store_record,
                    # Het endpoint is de sleutel, niet de hostnaam: verandert de
                    # hostnaam van de machine, dan blijft dit dezelfde rij.
                    provider["id"], f"host:{bind_entity_id}", "host",
                    {"system": system, "ip": ip_data, "quicklook": quicklook, "memory": memory},
                    name=host_name, entity_type="host", status="up", ip_address=ip_data.get("address"), hostname=host_name,
                    bind_entity_id=bind_entity_id,
                )
                count += 1
                if not isinstance(responses[2], Exception) and responses[2].status_code == 200:
                    for container in responses[2].json():
                        await asyncio.to_thread(
                            self._store_record,
                            provider["id"], f"container:{bind_entity_id}:{container.get('id') or container.get('name')}",
                            "container", container, name=container.get("name") or "container", entity_type="container",
                            status="up" if str(container.get("status", "")).lower().startswith("up") else "down",
                            parent_id=host_id,
                        )
                        count += 1
        return count

    async def _sync_adguard(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        base_url = str(config.get("base_url", "")).rstrip("/")
        credentials = self.secrets.get(provider["id"])
        username = credentials.get("username")
        password = credentials.get("password")
        if not base_url or not username or not password:
            raise ValueError("AdGuard URL, gebruikersnaam of wachtwoord ontbreekt")
        verify = bool(config.get("verify_tls", True))
        count = 0
        async with httpx.AsyncClient(timeout=20, verify=verify, auth=(username, password)) as client:
            if config.get("import_clients", True):
                response = await client.get(f"{base_url}/control/clients")
                response.raise_for_status()
                payload = response.json()
                clients = payload.get("clients", []) + payload.get("auto_clients", [])
                for item in clients:
                    identifiers = item.get("ids") or item.get("ip_addrs") or [item.get("ip")]
                    identifiers = [value for value in identifiers if value]
                    mac_address = next((value for value in identifiers if normalize_mac(value)), None)
                    ip_address = next((value for value in identifiers if is_ip_literal(value)), None)
                    external_id = mac_address or ip_address or item.get("name")
                    await asyncio.to_thread(
                        self._store_record,
                        provider["id"], f"client:{external_id}", "dns_client", item,
                        name=item.get("name") or str(external_id), entity_type="device", status="unknown",
                        ip_address=ip_address, mac_address=mac_address, hostname=item.get("name"),
                    )
                    count += 1
            if config.get("import_rewrites", True):
                response = await client.get(f"{base_url}/control/rewrite/list")
                response.raise_for_status()
                for item in response.json():
                    await asyncio.to_thread(self._upsert_dns_record, provider["id"], item)
                    count += 1
        return count

    def _upsert_dns_record(self, provider_id: str, item: dict[str, Any]) -> None:
        domain = str(item.get("domain", "")).rstrip(".")
        answer = str(item.get("answer", ""))
        if not domain or not answer:
            return
        try:
            ipaddress.ip_address(answer)
            record_type = "AAAA" if ":" in answer else "A"
        except ValueError:
            record_type = "CNAME"
        external_id = f"{domain}|{answer}"
        entity = self.database.fetch_one(
            "SELECT id FROM entities WHERE ip_address=? OR lower(hostname)=lower(?) LIMIT 1",
            (answer, answer.rstrip(".")),
        )
        now = utcnow()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO dns_records
                   (id,name,record_type,value,enabled,source,provider_id,external_id,entity_id,last_seen_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,'adguard',?,?,?,?,?,?)
                   ON CONFLICT(provider_id,external_id) DO UPDATE SET
                   name=excluded.name,record_type=excluded.record_type,value=excluded.value,
                   entity_id=COALESCE(dns_records.entity_id,excluded.entity_id),last_seen_at=excluded.last_seen_at,
                   updated_at=excluded.updated_at""",
                (str(uuid.uuid4()), domain, record_type, answer, int(item.get("enabled", True)), provider_id,
                 external_id, entity["id"] if entity else None, now, now, now),
            )

    async def _sync_nginx_proxy_manager(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Nginx Proxy Manager URL ontbreekt")
        verify = bool(config.get("verify_tls", True))
        credentials = self.secrets.get(provider["id"])
        token = credentials.get("token")
        async with httpx.AsyncClient(timeout=20, verify=verify) as client:
            if not token:
                identity = credentials.get("identity")
                secret = credentials.get("secret")
                if not identity or not secret:
                    raise ValueError("NPM-token of gebruikersnaam/wachtwoord ontbreekt")
                login = await client.post(f"{base_url}/api/tokens", json={"identity": identity, "secret": secret})
                login.raise_for_status()
                token = login.json().get("token")
            response = await client.get(
                f"{base_url}/api/nginx/proxy-hosts?expand=owner,access_list,certificate",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        count = 0
        for host in response.json():
            service_name = (host.get("domain_names") or [f"proxy-{host.get('id')}"])[0]
            target = str(host.get("forward_host", ""))
            target_entity = await asyncio.to_thread(
                self.database.fetch_one,
                "SELECT id FROM entities WHERE ip_address=? OR lower(hostname)=lower(?) OR lower(name)=lower(?) LIMIT 1",
                (target, target, target),
            )
            service_entity_id = await asyncio.to_thread(
                self._store_record,
                provider["id"], f"proxy:{host.get('id')}", "proxy_host", host,
                name=service_name, entity_type="service", status="up" if host.get("enabled", 1) else "down",
                parent_id=target_entity["id"] if target_entity else None,
            )
            await asyncio.to_thread(self._upsert_proxy_host, provider["id"], host, target_entity, service_entity_id)
            count += 1
        return count

    def _upsert_proxy_host(
        self, provider_id: str, host: dict[str, Any], target_entity: dict[str, Any] | None, service_entity_id: str
    ) -> None:
        now = utcnow()
        external_id = str(host.get("id"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO proxy_hosts
                   (id,domains_json,forward_scheme,forward_host,forward_port,enabled,source,provider_id,external_id,
                    entity_id,service_entity_id,last_seen_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,'nginx_proxy_manager',?,?,?,?,?,?,?)
                   ON CONFLICT(provider_id,external_id) DO UPDATE SET
                   domains_json=excluded.domains_json,forward_scheme=excluded.forward_scheme,
                   forward_host=excluded.forward_host,forward_port=excluded.forward_port,enabled=excluded.enabled,
                   entity_id=COALESCE(proxy_hosts.entity_id,excluded.entity_id),service_entity_id=excluded.service_entity_id,
                   last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                (str(uuid.uuid4()), json.dumps(host.get("domain_names") or []), host.get("forward_scheme") or "http",
                 str(host.get("forward_host", "")), int(host.get("forward_port") or 80), int(host.get("enabled", 1)),
                 provider_id, external_id, target_entity["id"] if target_entity else None, service_entity_id, now, now, now),
            )

    async def _sync_portainer(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        base_url = str(config.get("base_url", "")).rstrip("/")
        token = self.secrets.get(provider["id"]).get("api_key")
        if not base_url or not token:
            raise ValueError("Portainer URL of API-key ontbreekt")
        verify = bool(config.get("verify_tls", True))
        headers = {"X-API-Key": token}
        count = 0
        async with httpx.AsyncClient(timeout=20, verify=verify, headers=headers) as client:
            response = await client.get(f"{base_url}/api/endpoints")
            response.raise_for_status()
            for endpoint in response.json():
                endpoint_id = str(endpoint["Id"])
                host_id = await asyncio.to_thread(
                    self._store_record,
                    provider["id"], f"endpoint:{endpoint_id}", "docker_host", endpoint,
                    name=endpoint.get("Name") or f"Docker {endpoint_id}", entity_type="host", status="up",
                )
                count += 1
                containers = await client.get(f"{base_url}/api/endpoints/{endpoint_id}/docker/containers/json?all=1")
                containers.raise_for_status()
                for container in containers.json():
                    names = container.get("Names") or []
                    name = (names[0].lstrip("/") if names else container.get("Id", "")[:12])
                    await asyncio.to_thread(
                        self._store_record,
                        provider["id"], f"container:{endpoint_id}:{container.get('Id')}", "container", container,
                        name=name, entity_type="container",
                        status="up" if container.get("State") == "running" else "down", parent_id=host_id,
                    )
                    count += 1
        return count

    async def _sync_proxmox(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        base_url = str(config.get("base_url", "")).rstrip("/")
        user = config.get("user")
        token_name = config.get("token_name")
        secret = self.secrets.get(provider["id"]).get("token_secret")
        if not all((base_url, user, token_name, secret)):
            raise ValueError("Proxmox URL, gebruiker, tokennaam of tokengeheim ontbreekt")
        headers = {"Authorization": f"PVEAPIToken={user}!{token_name}={secret}"}
        verify = bool(config.get("verify_tls", True))
        async with httpx.AsyncClient(timeout=20, verify=verify, headers=headers) as client:
            response = await client.get(f"{base_url}/api2/json/cluster/resources")
            response.raise_for_status()
        count = 0
        nodes: dict[str, str] = {}
        resources = response.json().get("data", [])
        for resource in resources:
            if resource.get("type") == "node":
                node = resource.get("node") or resource.get("id")
                nodes[node] = await asyncio.to_thread(
                    self._store_record,
                    provider["id"], f"node:{node}", "proxmox_node", resource,
                    name=node, entity_type="host", status="up" if resource.get("status") == "online" else "down",
                )
                count += 1
        for resource in resources:
            kind = resource.get("type")
            if kind not in {"qemu", "lxc"}:
                continue
            node = resource.get("node")
            await asyncio.to_thread(
                self._store_record,
                provider["id"], f"{kind}:{node}:{resource.get('vmid')}", kind, resource,
                name=resource.get("name") or f"{kind}-{resource.get('vmid')}",
                entity_type="vm" if kind == "qemu" else "lxc",
                status="up" if resource.get("status") == "running" else "down",
                parent_id=nodes.get(node),
            )
            count += 1
        return count

    # --- Testverbinding ------------------------------------------------
    # Read-only proefcall per provider: zegt vóór opslaan of de gegevens
    # kloppen en wat er gevonden zou worden. Slaat niets op.

    async def test_one(
        self, provider_id: str, config: dict[str, Any], credentials: dict[str, str | None]
    ) -> dict[str, Any]:
        provider = self.database.fetch_one("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not provider:
            raise ValueError("Onbekende provider")
        merged = dict(self.secrets.get(provider_id))
        merged.update({key: value.strip() for key, value in credentials.items() if value and value.strip()})
        handler = getattr(self, f"_test_{provider['type']}", None)
        if handler is None:
            return {"ok": False, "summary": "Deze provider heeft geen testverbinding"}
        try:
            return {"ok": True, "summary": await handler(config, merged)}
        except httpx.HTTPStatusError as exc:
            hint = " (controleer inloggegevens)" if exc.response.status_code in (401, 403) else ""
            return {"ok": False, "summary": f"HTTP {exc.response.status_code}{hint}"}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "summary": str(exc)[:200] or exc.__class__.__name__}

    async def _test_dhcp_arp(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        arp_path = Path("/proc/net/arp")
        entries = len(parse_arp_table(arp_path.read_text())) if arp_path.exists() else 0
        subnets = [str(value) for value in config.get("subnets", [])]
        for value in subnets:
            network = ipaddress.ip_network(value, strict=False)
            if not self._network_is_trusted(network):
                raise ValueError(f"Subnet {network} valt buiten PATCH_TRUSTED_SUBNETS")
            if network.num_addresses > 1024:
                raise ValueError(f"Subnet {network} bevat meer dan 1024 adressen")
        scope = f", scan over {', '.join(subnets)}" if subnets and config.get("scan") else ""
        return f"ARP-tabel: {entries} buren{scope}"

    async def _test_uptime_kuma(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        base_url = str(config.get("base_url", "")).rstrip("/")
        slug = config.get("status_page_slug")
        if not base_url or not slug:
            raise ValueError("Base URL en statuspagina-slug zijn verplicht")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{base_url}/api/status-page/{slug}")
            response.raise_for_status()
            page = response.json()
        monitors = sum(len(group.get("monitorList", []) or []) for group in page.get("publicGroupList", []))
        return f"{monitors} monitor(s) op statuspagina '{slug}'"

    async def _test_glances(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        endpoints = config.get("endpoints", []) or []
        if not endpoints:
            raise ValueError("Geen endpoints geconfigureerd")
        auth = None
        if credentials.get("username") and credentials.get("password"):
            auth = (credentials["username"], credentials["password"])
        # Test alle endpoints en meld ze per stuk: met meerdere machines wil je
        # weten wélke er stuk is, niet dat er "iets" niet werkt.
        found: list[str] = []
        working = 0
        async with httpx.AsyncClient(timeout=15, auth=auth) as client:
            for endpoint in endpoints:
                base_url = str(endpoint.get("url", "")).rstrip("/")
                if not base_url:
                    continue
                title = endpoint.get("name") or base_url
                if not endpoint.get("entity_id"):
                    found.append(f"{title}: geen device gekozen")
                    continue
                try:
                    response = await client.get(f"{base_url}/system")
                    response.raise_for_status()
                    containers = await client.get(f"{base_url}/containers")
                    count = len(containers.json()) if containers.status_code == 200 else 0
                except httpx.HTTPStatusError as exc:
                    found.append(f"{title}: HTTP {exc.response.status_code}")
                except httpx.HTTPError as exc:
                    found.append(f"{title}: {exc.__class__.__name__}")
                else:
                    working += 1
                    found.append(f"{title} → {response.json().get('hostname') or base_url} ({count} container(s))")
        if not found:
            raise ValueError("Geen bruikbare endpoints")
        # Eén kapotte machine tussen vijf werkende blokkeert het opslaan niet;
        # niets dat werkt wél.
        if not working:
            raise ValueError(" · ".join(found))
        return " · ".join(found)

    async def _test_adguard(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url or not credentials.get("username") or not credentials.get("password"):
            raise ValueError("URL, gebruikersnaam of wachtwoord ontbreekt")
        auth = (credentials["username"], credentials["password"])
        async with httpx.AsyncClient(timeout=20, verify=bool(config.get("verify_tls", True)), auth=auth) as client:
            clients = await client.get(f"{base_url}/control/clients")
            clients.raise_for_status()
            payload = clients.json()
            rewrites = await client.get(f"{base_url}/control/rewrite/list")
            rewrites.raise_for_status()
        total = len(payload.get("clients", []) or []) + len(payload.get("auto_clients", []) or [])
        return f"{total} client(s), {len(rewrites.json())} DNS-rewrite(s)"

    async def _test_nginx_proxy_manager(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("Nginx Proxy Manager URL ontbreekt")
        async with httpx.AsyncClient(timeout=20, verify=bool(config.get("verify_tls", True))) as client:
            token = credentials.get("token")
            if not token:
                identity, secret = credentials.get("identity"), credentials.get("secret")
                if not identity or not secret:
                    raise ValueError("Token of gebruikersnaam/wachtwoord ontbreekt")
                login = await client.post(f"{base_url}/api/tokens", json={"identity": identity, "secret": secret})
                login.raise_for_status()
                token = login.json().get("token")
            response = await client.get(
                f"{base_url}/api/nginx/proxy-hosts", headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
        return f"{len(response.json())} proxyhost(s)"

    async def _test_portainer(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        base_url = str(config.get("base_url", "")).rstrip("/")
        token = credentials.get("api_key")
        if not base_url or not token:
            raise ValueError("Portainer URL of API-key ontbreekt")
        async with httpx.AsyncClient(
            timeout=20, verify=bool(config.get("verify_tls", True)), headers={"X-API-Key": token}
        ) as client:
            response = await client.get(f"{base_url}/api/endpoints")
            response.raise_for_status()
            endpoints = response.json()
            containers = 0
            for endpoint in endpoints:
                listing = await client.get(
                    f"{base_url}/api/endpoints/{endpoint['Id']}/docker/containers/json?all=1"
                )
                if listing.status_code == 200:
                    containers += len(listing.json())
        return f"{len(endpoints)} endpoint(s), {containers} container(s)"

    async def _test_proxmox(self, config: dict[str, Any], credentials: dict[str, str]) -> str:
        base_url = str(config.get("base_url", "")).rstrip("/")
        user, token_name = config.get("user"), config.get("token_name")
        secret = credentials.get("token_secret")
        if not all((base_url, user, token_name, secret)):
            raise ValueError("URL, gebruiker, tokennaam of tokengeheim ontbreekt")
        headers = {"Authorization": f"PVEAPIToken={user}!{token_name}={secret}"}
        async with httpx.AsyncClient(
            timeout=20, verify=bool(config.get("verify_tls", True)), headers=headers
        ) as client:
            response = await client.get(f"{base_url}/api2/json/cluster/resources")
            # Een kale "401" helpt hier niet: het token-ID wordt uit twee velden
            # samengesteld, dus laat zien wat er daadwerkelijk is verstuurd.
            if response.status_code == 401:
                raise ValueError(
                    f"HTTP 401: Proxmox weigert token '{user}!{token_name}'. Controleer de gebruiker, "
                    "het token-ID en het geheim, en of het token leesrechten heeft."
                )
            response.raise_for_status()
        resources = response.json().get("data", [])
        kinds = {"node": 0, "qemu": 0, "lxc": 0}
        for resource in resources:
            if resource.get("type") in kinds:
                kinds[resource["type"]] += 1
        return f"{kinds['node']} node(s), {kinds['qemu']} VM's, {kinds['lxc']} LXC's"
