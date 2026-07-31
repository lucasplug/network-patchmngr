from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="plugnet-tests-"))
os.environ["PATCH_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["PATCH_BACKUP_DIR"] = str(TEST_ROOT / "backups")

from fastapi.testclient import TestClient  # noqa: E402

from patch_manager.main import app, database, provider_secrets, providers, speedtests  # noqa: E402
from patch_manager.db import utcnow  # noqa: E402
from patch_manager.speedtest import parse_result  # noqa: E402


def setup_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/setup",
        json={"username": "lucas", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.fixture(autouse=True)
def isolated_installation(request: pytest.FixtureRequest):
    for candidate in (database.path, database.path.with_name(database.path.name + "-wal"), database.path.with_name(database.path.name + "-shm")):
        candidate.unlink(missing_ok=True)
    for candidate in (TEST_ROOT / "backups").glob("*"):
        candidate.unlink(missing_ok=True)
    database.initialize()
    if request.node.name != "test_initializes_expected_physical_inventory":
        with TestClient(app) as client:
            setup_admin(client)
    yield


def test_initializes_expected_physical_inventory() -> None:
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is True
        csrf = setup_admin(client)
        response = client.get("/api/bootstrap")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["physical_devices"]) == 5
        assert payload["counts"]["ports"] == 25
        assert payload["counts"]["patched"] == 0
        assert len(payload["providers"]) == 7
        assert {node["id"] for node in payload["topology"]["nodes"]} >= {"special:internet", "special:router"}
        assert payload["speedtest"]["settings"]["telemetry_enabled"] is False
        assert csrf


def test_admin_manages_title_and_encrypted_provider_credentials() -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "lucas", "password": "correct horse battery staple"},
        )
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        title = client.patch("/api/settings", headers=headers, json={"title": "Mijn Thuisnetwerk"})
        assert title.status_code == 200, title.text
        assert client.get("/api/public/settings").json()["title"] == "Mijn Thuisnetwerk"
        assert client.get("/api/bootstrap").json()["site"]["title"] == "Mijn Thuisnetwerk"

        response = client.patch(
            "/api/providers/proxmox",
            headers=headers,
            json={
                "enabled": False,
                "poll_interval_seconds": 60,
                "config": {"base_url": "https://proxmox.local:8006", "user": "reader@pve", "token_name": "patch-manager"},
                "credentials": {"token_secret": "highly-secret-token"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["credentials_configured"]["token_secret"] is True
        assert "highly-secret-token" not in response.text
        encrypted = database.fetch_one(
            "SELECT encrypted_json FROM provider_secrets WHERE provider_id='proxmox'"
        )["encrypted_json"]
        assert "highly-secret-token" not in encrypted
        assert provider_secrets.get("proxmox")["token_secret"] == "highly-secret-token"

        cleared = client.patch(
            "/api/providers/proxmox",
            headers=headers,
            json={
                "enabled": False,
                "poll_interval_seconds": 60,
                "config": {},
                "clear_credentials": ["token_secret"],
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["credentials_configured"]["token_secret"] is False


def test_manual_device_can_be_assigned_and_unassigned() -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "lucas", "password": "correct horse battery staple"},
        )
        csrf = login.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        entity = client.post(
            "/api/entities",
            headers=headers,
            json={"name": "NAS", "type": "nas", "ip_address": "192.168.1.20", "notes": ""},
        ).json()
        response = client.put(
            "/api/ports/switch-01-p1/assignment",
            headers=headers,
            json={"entity_id": entity["id"], "cable_label": "C-001", "cable_color": "blauw", "notes": ""},
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/bootstrap").json()["counts"]["patched"] == 1
        response = client.delete("/api/ports/switch-01-p1/assignment", headers=headers)
        assert response.status_code == 200
        assert client.get("/api/bootstrap").json()["counts"]["patched"] == 0


def test_provider_never_overwrites_manual_identity() -> None:
    now = utcnow()
    entity_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO entities
               (id,name,type,origin,status,ip_address,mac_address,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (entity_id, "NAS", "nas", "manual", "unknown", "192.168.1.20", "aa:bb:cc:dd:ee:ff", "", now, now),
        )
    manual = database.fetch_one("SELECT * FROM entities WHERE id=?", (entity_id,))
    providers._store_record(
        "dhcp-arp",
        "aa:bb:cc:dd:ee:ff",
        "network_device",
        {"ip": "192.168.1.99", "mac": "aa:bb:cc:dd:ee:ff"},
        name="router-supplied-name",
        entity_type="device",
        status="up",
        ip_address="192.168.1.99",
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    current = database.fetch_one("SELECT * FROM entities WHERE id=?", (manual["id"],))
    assert current["name"] == "NAS"
    assert current["ip_address"] == "192.168.1.20"
    conflict = database.fetch_one("SELECT * FROM conflicts WHERE entity_id=? AND status='open'", (manual["id"],))
    assert conflict is not None


def test_discovered_entity_cannot_be_updated_locally() -> None:
    discovery_id = providers._store_record(
        "dhcp-arp", "update-guard-test", "network_device", {"ip": "192.168.1.239"},
        name="update-guard", entity_type="device", ip_address="192.168.1.239",
    )
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        response = client.patch(
            f"/api/entities/{discovery_id}", headers={"X-CSRF-Token": csrf},
            json={"name": "hernoemd", "type": "device", "notes": ""},
        )
        assert response.status_code == 409


def test_backup_is_valid_sqlite_database() -> None:
    target = database.create_backup_bundle(TEST_ROOT / "backups", provider_secrets.key_path)
    assert target.exists()
    assert target.suffix == ".pmbackup"
    assert database.validate_backup(target)["portable"] is True


def test_topology_and_dns_are_interactively_configurable() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        group = client.post("/api/topology/groups", headers=headers, json={"label": "Clients", "subtitle": "LAN/WiFi"})
        assert group.status_code == 200, group.text
        group_id = group.json()["id"]
        moved = client.patch(
            f"/api/topology/nodes/{group_id}/position", headers=headers, json={"x": 321, "y": 654}
        )
        assert moved.status_code == 200, moved.text
        relation = client.post(
            "/api/topology/relations", headers=headers,
            json={"from_node_id": "special:router", "to_node_id": group_id, "relation_type": "network", "label": "LAN"},
        )
        assert relation.status_code == 200, relation.text
        dns = client.post(
            "/api/dns-records", headers=headers,
            json={"name": "nas.home.arpa", "record_type": "A", "value": "192.168.1.20", "ttl": 300, "enabled": True},
        )
        assert dns.status_code == 200, dns.text
        payload = client.get("/api/bootstrap").json()
        assert any(item["name"] == "nas.home.arpa" for item in payload["dns_records"])
        assert any(item["id"] == group_id and item["manual_position"] for item in payload["topology"]["nodes"])


def test_speedtest_result_parser_accepts_librespeed_json() -> None:
    result = parse_result({
        "download": 812_500_000, "upload": 97_100_000, "ping": 5.2, "jitter": 0.8,
        "server": {"id": 4, "name": "Amsterdam"}, "isp": "Example ISP", "ip": "203.0.113.4",
    })
    assert result["download_mbps"] == 812.5
    assert result["upload_mbps"] == 97.1
    assert result["server_name"] == "Amsterdam"
    official = parse_result({
        "download": 812.5, "upload": 97.1, "ping": 5.2, "jitter": 0.8,
        "server": {"name": "Amsterdam", "url": "https://example.test"},
        "client": {"ip": "203.0.113.4", "org": "Example ISP"},
    })
    assert official["download_mbps"] == 812.5
    assert official["public_ip"] == "203.0.113.4"
    assert official["isp"] == "Example ISP"


def test_adguard_and_nginx_import_storage() -> None:
    providers._upsert_dns_record("adguard", {"domain": "proxy.home.arpa", "answer": "192.168.1.12"})
    target = database.fetch_one("SELECT id FROM entities WHERE name='NAS'")
    service_id = providers._store_record(
        "nginx-proxy-manager", "proxy:test", "proxy_host", {"id": 99},
        name="proxy.home.arpa", entity_type="service", parent_id=target["id"] if target else None,
    )
    providers._upsert_proxy_host(
        "nginx-proxy-manager",
        {"id": 99, "domain_names": ["proxy.home.arpa"], "forward_scheme": "http", "forward_host": "192.168.1.20", "forward_port": 8080, "enabled": 1},
        target,
        service_id,
    )
    assert database.fetch_one("SELECT id FROM dns_records WHERE name='proxy.home.arpa'")
    assert database.fetch_one("SELECT service_entity_id FROM proxy_hosts WHERE external_id='99'")["service_entity_id"] == service_id


def test_safe_entity_and_physical_device_deletion() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        entity = client.post(
            "/api/entities", headers=headers,
            json={"name": "Tijdelijk device", "type": "device", "ip_address": "192.168.1.240", "notes": ""},
        ).json()
        assert client.put(
            "/api/ports/switch-02-p1/assignment", headers=headers,
            json={"entity_id": entity["id"], "cable_label": "TMP", "cable_color": "", "notes": ""},
        ).status_code == 200
        dns = client.post(
            "/api/dns-records", headers=headers,
            json={"name": "tijdelijk.home.arpa", "record_type": "A", "value": "192.168.1.240", "enabled": True, "entity_id": entity["id"]},
        ).json()
        impact = client.get(f"/api/entities/{entity['id']}/deletion-impact").json()
        assert impact["counts"]["port_assignments"] == 1
        assert impact["counts"]["dns_records"] == 1
        assert client.delete(f"/api/entities/{entity['id']}?confirm=verkeerd", headers=headers).status_code == 422
        deleted = client.delete(
            f"/api/entities/{entity['id']}?confirm=Tijdelijk%20device", headers=headers
        )
        assert deleted.status_code == 200, deleted.text
        assert database.fetch_one("SELECT id FROM entities WHERE id=?", (entity["id"],)) is None
        assert database.fetch_one("SELECT entity_id FROM dns_records WHERE id=?", (dns["id"],))["entity_id"] is None
        assert database.fetch_one("SELECT port_id FROM port_assignments WHERE port_id='switch-02-p1'") is None

        # Starter hardware is deliberately removable and must not be reseeded.
        impact = client.get("/api/physical-devices/deco-03/deletion-impact").json()
        assert impact["counts"]["ports"] == 3
        removed = client.delete(
            "/api/physical-devices/deco-03?confirm=Deco%20XE75%20Pro%2003", headers=headers
        )
        assert removed.status_code == 200, removed.text
        database.initialize()
        assert database.fetch_one("SELECT id FROM physical_devices WHERE id='deco-03'") is None


def test_discovered_entity_cannot_be_deleted_locally() -> None:
    discovery_id = providers._store_record(
        "dhcp-arp", "delete-protected", "network_device", {"ip": "192.168.1.222"},
        name="gevonden-device", entity_type="device", ip_address="192.168.1.222",
    )
    discovered = database.fetch_one("SELECT * FROM entities WHERE id=?", (discovery_id,))
    assert discovered is not None
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        response = client.delete(
            f"/api/entities/{discovered['id']}?confirm={discovered['name']}", headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 409


def test_inventory_edit_discovery_mapping_merge_and_audit() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        physical = client.patch(
            "/api/physical-devices/switch-01", headers=headers,
            json={"name": "Switch kantoor", "type": "switch", "model": "SG108E", "location": "Kast", "notes": "", "ports": 9},
        )
        assert physical.status_code == 200, physical.text
        assert database.fetch_one("SELECT COUNT(*) AS n FROM ports WHERE physical_device_id='switch-01'")["n"] == 9

        target = client.post(
            "/api/entities", headers=headers,
            json={"name": "Vaste host", "type": "host", "ip_address": "192.168.1.210", "notes": ""},
        ).json()
        discovery_id = providers._store_record(
            "dhcp-arp", "merge-test", "network_device", {"ip": "192.168.1.211"},
            name="gevonden-host", entity_type="device", ip_address="192.168.1.211",
        )
        state_response = client.patch(
            f"/api/entities/{discovery_id}/discovery-state", headers=headers,
            json={"ignored": True, "archived": False},
        )
        assert state_response.status_code == 200
        assert database.fetch_one("SELECT ignored FROM entities WHERE id=?", (discovery_id,))["ignored"] == 1
        record = database.fetch_one("SELECT id FROM provider_records WHERE provider_id='dhcp-arp' AND external_id='merge-test'")
        mapped = client.patch(
            f"/api/provider-records/{record['id']}/mapping", headers=headers, json={"entity_id": target["id"]}
        )
        assert mapped.status_code == 200, mapped.text
        # Return the record to the discovery before exercising the full merge.
        client.patch(f"/api/provider-records/{record['id']}/mapping", headers=headers, json={"entity_id": discovery_id})
        merged = client.post(
            f"/api/entities/{discovery_id}/merge", headers=headers, json={"target_entity_id": target["id"]}
        )
        assert merged.status_code == 200, merged.text
        assert database.fetch_one("SELECT id FROM entities WHERE id=?", (discovery_id,)) is None
        assert database.fetch_one("SELECT entity_id FROM provider_records WHERE id=?", (record["id"],))["entity_id"] == target["id"]
        assert client.get("/api/bootstrap").json()["audit_log"]


def test_topology_grouping_delete_and_undo() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        group = client.post(
            "/api/topology/groups", headers=headers,
            json={"label": "Selectiegroep", "subtitle": "test", "node_ids": ["special:router"]},
        )
        assert group.status_code == 200, group.text
        group_id = group.json()["id"]
        assert database.fetch_one("SELECT parent_node_id FROM topology_nodes WHERE id='special:router'")["parent_node_id"] == group_id
        undo = client.post("/api/topology/undo", headers=headers)
        assert undo.status_code == 200, undo.text
        assert database.fetch_one("SELECT id FROM topology_nodes WHERE id=?", (group_id,)) is None

        group = client.post(
            "/api/topology/groups", headers=headers, json={"label": "Wisgroep", "subtitle": "", "node_ids": []}
        ).json()
        removed = client.delete(
            f"/api/topology/groups/{group['id']}?confirm=Wisgroep", headers=headers
        )
        assert removed.status_code == 200, removed.text
        assert client.post("/api/topology/undo", headers=headers).status_code == 200
        assert database.fetch_one("SELECT id FROM topology_nodes WHERE id=?", (group["id"],)) is not None


def test_configuration_and_backup_roundtrip() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        config = client.get("/api/config/export")
        assert config.status_code == 200
        imported = client.post("/api/config/import", headers=headers, json=config.json())
        assert imported.status_code == 200, imported.text
        assert imported.json()["records"] > 0

        configured = client.patch(
            "/api/providers/proxmox", headers=headers,
            json={"enabled": False, "poll_interval_seconds": 60, "config": {},
                  "credentials": {"token_secret": "secret-from-backup"}},
        )
        assert configured.status_code == 200

        backup = client.post("/api/backups", headers=headers).json()
        assert backup["name"].endswith(".pmbackup")
        downloaded = client.get(f"/api/backups/{backup['name']}/download")
        assert downloaded.status_code == 200
        client.patch(
            "/api/providers/proxmox", headers=headers,
            json={"enabled": False, "poll_interval_seconds": 60, "config": {},
                  "credentials": {"token_secret": "changed-after-backup"}},
        )
        uploaded = client.post(
            "/api/backups/import", headers=headers,
            files={"file": ("roundtrip.pmbackup", downloaded.content, "application/zip")},
        )
        assert uploaded.status_code == 200, uploaded.text
        imported_name = uploaded.json()["name"]
        restored = client.post(
            f"/api/backups/{imported_name}/restore?confirm={imported_name}", headers=headers
        )
        assert restored.status_code == 200, restored.text
        assert provider_secrets.get("proxmox")["token_secret"] == "secret-from-backup"


def test_observations_are_bounded_and_resolved_conflict_stays_resolved() -> None:
    now = utcnow()
    entity_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO entities
               (id,name,type,origin,status,mac_address,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (entity_id, "Handmatige NAS", "nas", "manual", "unknown", "aa:bb:cc:dd:ee:11", "", now, now),
        )
    for _ in range(3):
        providers._store_record(
            "dhcp-arp", "aa:bb:cc:dd:ee:11", "network_device", {"ip": "192.168.1.50"},
            name="provider-naam", entity_type="device", status="up",
            ip_address="192.168.1.50", mac_address="aa:bb:cc:dd:ee:11", hostname="nas.local",
        )
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM observations WHERE entity_id=?", (entity_id,)
    )["n"] == 3
    conflict = database.fetch_one("SELECT id FROM conflicts WHERE entity_id=? AND status='open'", (entity_id,))
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        response = client.post(
            f"/api/conflicts/{conflict['id']}/resolve",
            headers={"X-CSRF-Token": csrf}, json={"resolution": "manual_kept"},
        )
        assert response.status_code == 200
    providers._store_record(
        "dhcp-arp", "aa:bb:cc:dd:ee:11", "network_device", {"ip": "192.168.1.50"},
        name="provider-naam", entity_type="device", status="up",
        ip_address="192.168.1.50", mac_address="aa:bb:cc:dd:ee:11", hostname="nas.local",
    )
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM conflicts WHERE entity_id=? AND status='open'", (entity_id,)
    )["n"] == 0


def test_scan_rejects_subnets_outside_trusted_ranges() -> None:
    provider = database.fetch_one("SELECT * FROM providers WHERE id='dhcp-arp'")
    with pytest.raises(ValueError, match="PATCH_TRUSTED_SUBNETS"):
        asyncio.run(providers._sync_dhcp_arp(provider, {"scan": True, "subnets": ["10.20.30.0/30"]}))


def test_topology_grouping_rejects_cycles() -> None:
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        parent = client.post("/api/topology/groups", headers=headers, json={"label": "Parent", "subtitle": ""}).json()
        child = client.post(
            "/api/topology/groups", headers=headers,
            json={"label": "Child", "subtitle": "", "node_ids": [parent["id"]]},
        ).json()
        response = client.patch(
            "/api/topology/group-selection", headers=headers,
            json={"parent_node_id": parent["id"], "node_ids": [child["id"]]},
        )
        assert response.status_code == 422


def test_config_import_handles_child_before_parent() -> None:
    now = utcnow()
    parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [
        {"id": child_id, "name": "Child", "type": "service", "origin": "manual", "status": "unknown",
         "parent_id": parent_id, "ignored": 0, "archived": 0, "notes": "", "created_at": now, "updated_at": now},
        {"id": parent_id, "name": "Parent", "type": "host", "origin": "manual", "status": "unknown",
         "parent_id": None, "ignored": 0, "archived": 0, "notes": "", "created_at": now, "updated_at": now},
    ]
    with TestClient(app) as client:
        csrf = client.post(
            "/api/auth/login", json={"username": "lucas", "password": "correct horse battery staple"}
        ).json()["csrf_token"]
        response = client.post(
            "/api/config/import", headers={"X-CSRF-Token": csrf},
            json={"format": "plugnet-config", "version": 1, "tables": {"entities": rows}},
        )
        assert response.status_code == 200, response.text
    assert database.fetch_one("SELECT parent_id FROM entities WHERE id=?", (child_id,))["parent_id"] == parent_id


def test_stale_speedtest_is_closed_on_startup() -> None:
    run_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO speedtest_runs(id,status,started_at) VALUES(?,'running',?)", (run_id, utcnow())
        )
    speedtests.recover_stale_runs()
    run = database.fetch_one("SELECT status,completed_at FROM speedtest_runs WHERE id=?", (run_id,))
    assert run["status"] == "failed"
    assert run["completed_at"]
