from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .db import Database, utcnow
from .secret_store import SecretStore


MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    match = MAC_RE.search(value.replace("-", ":"))
    return match.group(0).lower() if match else None


class ProviderManager:
    """Read-only provider adapters; only observations may update discovered fields."""

    def __init__(self, database: Database, secrets: SecretStore):
        self.database = database
        self.secrets = secrets
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync_enabled(self) -> None:
        providers = self.database.fetch_all("SELECT id FROM providers WHERE enabled=1")
        for provider in providers:
            try:
                await self.sync_one(provider["id"])
            except Exception:
                # Per-provider failures are persisted and must not stop other collectors.
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
    ) -> str:
        now = utcnow()
        mac_address = normalize_mac(mac_address)
        existing_record = self.database.fetch_one(
            "SELECT entity_id FROM provider_records WHERE provider_id=? AND external_id=?",
            (provider_id, external_id),
        )
        entity = None
        if existing_record and existing_record["entity_id"]:
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
                           last_seen_at=?,updated_at=? WHERE id=?""",
                        (name, entity_type, status, now, ip_address, mac_address, hostname, parent_id, now, now, entity_id),
                    )
            else:
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
                        first_seen_at,last_seen_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (entity_id, name, entity_type, "discovered", status, now, ip_address, mac_address, hostname, parent_id, now, now, now, now),
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
                        "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), entity_id, provider_id, field, json.dumps(value), now, expires, 1.0),
                    )
        return entity_id

    def _record_manual_conflicts(self, entity: dict[str, Any], provider_id: str, values: dict[str, Any]) -> None:
        for field in ("name", "mac_address"):
            observed = values.get(field)
            manual = entity.get(field)
            if observed and manual and str(observed).lower() != str(manual).lower():
                existing = self.database.fetch_one(
                    "SELECT id FROM conflicts WHERE entity_id=? AND provider_id=? AND field=? AND status='open'",
                    (entity["id"], provider_id, field),
                )
                if not existing:
                    with self.database.transaction() as connection:
                        connection.execute(
                            "INSERT INTO conflicts VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), entity["id"], provider_id, field, str(manual), str(observed), "open", utcnow(), None, None),
                        )

    async def _sync_dhcp_arp(self, provider: dict[str, Any], config: dict[str, Any]) -> int:
        found: dict[str, dict[str, str]] = {}
        arp_path = Path("/proc/net/arp")

        def read_arp_table() -> None:
            if arp_path.exists():
                for line in arp_path.read_text().splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4 and normalize_mac(parts[3]):
                        found[parts[0]] = {"ip": parts[0], "mac": parts[3]}

        read_arp_table()

        if config.get("scan"):
            for subnet_value in config.get("subnets", [])[:8]:
                network = ipaddress.ip_network(subnet_value, strict=False)
                if network.num_addresses > 1024:
                    continue
                semaphore = asyncio.Semaphore(48)

                async def ping(ip: str) -> None:
                    async with semaphore:
                        try:
                            process = await asyncio.create_subprocess_exec(
                                "ping", "-c", "1", "-W", "1", ip,
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            if await process.wait() == 0:
                                found.setdefault(ip, {"ip": ip, "mac": ""})
                        except FileNotFoundError:
                            return

                await asyncio.gather(*(ping(str(ip)) for ip in network.hosts()))
            read_arp_table()

        count = 0
        for item in found.values():
            try:
                hostname = socket.gethostbyaddr(item["ip"])[0]
            except (socket.herror, socket.gaierror, TimeoutError):
                hostname = None
            self._store_record(
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
        return count

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
                self._store_record(
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
        async with httpx.AsyncClient(timeout=15, auth=auth) as client:
            for endpoint in config.get("endpoints", []):
                base_url = str(endpoint.get("url", "")).rstrip("/")
                if not base_url:
                    continue
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
                host_id = self._store_record(
                    provider["id"], f"host:{host_name}", "host",
                    {"system": system, "ip": ip_data, "quicklook": quicklook, "memory": memory},
                    name=host_name, entity_type="host", status="up", ip_address=ip_data.get("address"), hostname=host_name,
                )
                count += 1
                if not isinstance(responses[2], Exception) and responses[2].status_code == 200:
                    for container in responses[2].json():
                        self._store_record(
                            provider["id"], f"container:{host_name}:{container.get('id') or container.get('name')}",
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
                    ip_address = next((value for value in identifiers if "." in value or ":" in value and not normalize_mac(value)), None)
                    mac_address = next((value for value in identifiers if normalize_mac(value)), None)
                    external_id = mac_address or ip_address or item.get("name")
                    self._store_record(
                        provider["id"], f"client:{external_id}", "dns_client", item,
                        name=item.get("name") or str(external_id), entity_type="device", status="unknown",
                        ip_address=ip_address, mac_address=mac_address, hostname=item.get("name"),
                    )
                    count += 1
            if config.get("import_rewrites", True):
                response = await client.get(f"{base_url}/control/rewrite/list")
                response.raise_for_status()
                for item in response.json():
                    self._upsert_dns_record(provider["id"], item)
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
            target_entity = self.database.fetch_one(
                "SELECT id FROM entities WHERE ip_address=? OR lower(hostname)=lower(?) OR lower(name)=lower(?) LIMIT 1",
                (target, target, target),
            )
            service_entity_id = self._store_record(
                provider["id"], f"proxy:{host.get('id')}", "proxy_host", host,
                name=service_name, entity_type="service", status="up" if host.get("enabled", 1) else "down",
                parent_id=target_entity["id"] if target_entity else None,
            )
            self._upsert_proxy_host(provider["id"], host, target_entity, service_entity_id)
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
                host_id = self._store_record(
                    provider["id"], f"endpoint:{endpoint_id}", "docker_host", endpoint,
                    name=endpoint.get("Name") or f"Docker {endpoint_id}", entity_type="host", status="up",
                )
                count += 1
                containers = await client.get(f"{base_url}/api/endpoints/{endpoint_id}/docker/containers/json?all=1")
                containers.raise_for_status()
                for container in containers.json():
                    names = container.get("Names") or []
                    name = (names[0].lstrip("/") if names else container.get("Id", "")[:12])
                    self._store_record(
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
                nodes[node] = self._store_record(
                    provider["id"], f"node:{node}", "proxmox_node", resource,
                    name=node, entity_type="host", status="up" if resource.get("status") == "online" else "down",
                )
                count += 1
        for resource in resources:
            kind = resource.get("type")
            if kind not in {"qemu", "lxc"}:
                continue
            node = resource.get("node")
            self._store_record(
                provider["id"], f"{kind}:{node}:{resource.get('vmid')}", kind, resource,
                name=resource.get("name") or f"{kind}-{resource.get('vmid')}",
                entity_type="vm" if kind == "qemu" else "lxc",
                status="up" if resource.get("status") == "running" else "down",
                parent_id=nodes.get(node),
            )
            count += 1
        return count
