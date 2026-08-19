from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from patch_manager import providers as providers_module
from patch_manager.main import app, database, providers
from patch_manager.providers import EMPTY_MAC

from tests.conftest import CREDENTIALS


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def make_discovery(external_id: str, name: str, **kwargs) -> str:
    return providers._store_record(
        "dhcp-arp", external_id, "network_device", {"ip": kwargs.get("ip_address", "192.168.1.60")},
        name=name, entity_type="device", status="up", **kwargs,
    )


def test_wizard_info_reports_state_and_trusted_subnets() -> None:
    with TestClient(app) as client:
        headers = login(client)
        info = client.get("/api/wizard/info").json()
        assert info["dismissed"] is False
        assert info["trusted_subnets"] == ["127.0.0.0/8"]
        # Voorstel komt uit de eigen route en valt binnen de trusted subnets, of is leeg.
        assert info["suggested_subnet"] in (None, "127.0.0.0/8")

        assert client.patch("/api/wizard/info", headers=headers, json={"dismissed": True}).status_code == 200
        assert client.get("/api/wizard/info").json()["dismissed"] is True


def test_discoveries_endpoint_lists_open_items_with_vendor_and_link_state() -> None:
    linked_id = make_discovery("bulk-1", "gevonden-a", ip_address="192.168.1.61")
    make_discovery("bulk-2", "gevonden-b", ip_address="192.168.1.62")
    ignored_id = make_discovery("bulk-3", "genegeerd", ip_address="192.168.1.63")
    with TestClient(app) as client:
        headers = login(client)
        assert client.patch(
            f"/api/entities/{ignored_id}/discovery-state", headers=headers,
            json={"ignored": True, "archived": False},
        ).status_code == 200
        assert client.put(
            "/api/ports/switch-01-p1/cable", headers=headers, json={"b_entity_id": linked_id}
        ).status_code == 200

        rows = client.get("/api/discoveries").json()
        by_id = {row["id"]: row for row in rows}
        assert ignored_id not in by_id, "genegeerde discoveries horen niet in de bulklijst"
        assert by_id[linked_id]["linked"] is True
        assert by_id[linked_id]["sources"] == "DHCP / ARP discovery"
        assert all(key in rows[0] for key in ("mac_address", "vendor", "first_seen_at"))


def test_promote_makes_discovery_manual_without_losing_provider_link() -> None:
    discovery_id = make_discovery("promote-1", "192.168.1.64", ip_address="192.168.1.64")
    with TestClient(app) as client:
        headers = login(client)
        promoted = client.post(
            f"/api/entities/{discovery_id}/promote", headers=headers,
            json={"name": "Werkkamer-pc", "type": "host"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["origin"] == "manual"
        assert promoted.json()["name"] == "Werkkamer-pc"
        # Tweede keer is geen no-op maar een duidelijke fout.
        assert client.post(
            f"/api/entities/{discovery_id}/promote", headers=headers, json={},
        ).status_code == 409

    # De providerkoppeling blijft bestaan, dus status blijft binnenkomen.
    record = database.fetch_one(
        "SELECT entity_id FROM provider_records WHERE provider_id='dhcp-arp' AND external_id='promote-1'"
    )
    assert record["entity_id"] == discovery_id

    # En de provider overschrijft de handmatige naam nu niet meer.
    providers._store_record(
        "dhcp-arp", "promote-1", "network_device", {"ip": "192.168.1.64"},
        name="192.168.1.64", entity_type="device", status="up", ip_address="192.168.1.64",
    )
    assert database.fetch_one("SELECT name FROM entities WHERE id=?", (discovery_id,))["name"] == "Werkkamer-pc"


def test_promote_after_bulk_choice_allows_direct_port_assignment() -> None:
    discovery_id = make_discovery("promote-2", "192.168.1.65", ip_address="192.168.1.65")
    with TestClient(app) as client:
        headers = login(client)
        client.post(f"/api/entities/{discovery_id}/promote", headers=headers, json={"name": "Printer"})
        cabled = client.post(
            "/api/cables", headers=headers,
            json={"a_port_id": "switch-02-p3", "b_entity_id": discovery_id, "label": "P-01"},
        )
        assert cabled.status_code == 200, cabled.text
        assert client.get("/api/discoveries").json() == [] or all(
            row["id"] != discovery_id for row in client.get("/api/discoveries").json()
        )


def test_provider_test_endpoint_summarizes_without_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api2/json/cluster/resources"
        assert request.headers["authorization"] == "PVEAPIToken=reader@pve!wizard=proef-geheim"
        return httpx.Response(200, json={"data": [
            {"type": "node", "node": "pve", "status": "online"},
            {"type": "qemu", "node": "pve", "vmid": 100, "name": "vm1", "status": "running"},
            {"type": "qemu", "node": "pve", "vmid": 101, "name": "vm2", "status": "stopped"},
            {"type": "lxc", "node": "pve", "vmid": 200, "name": "ct1", "status": "running"},
        ]})

    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/providers/proxmox/test", headers=headers,
            json={"config": {"base_url": "https://pve.local:8006", "user": "reader@pve", "token_name": "wizard"},
                  "credentials": {"token_secret": "proef-geheim"}},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "summary": "1 node(s), 2 VM's, 1 LXC's"}

    # Niets opgeslagen: geen secret, provider blijft uit.
    assert database.fetch_one("SELECT provider_id FROM provider_secrets WHERE provider_id='proxmox'") is None
    assert database.fetch_one("SELECT enabled FROM providers WHERE id='proxmox'")["enabled"] == 0


def test_provider_test_reports_failure_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *args, **kwargs: original(*args, **{**kwargs, "transport": httpx.MockTransport(handler)}),
    )
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/providers/portainer/test", headers=headers,
            json={"config": {"base_url": "https://docker.local:9443"}, "credentials": {"api_key": "fout"}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is False and "401" in body["summary"]


def test_provider_test_rejects_unknown_credential_field() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/providers/portainer/test", headers=headers,
            json={"config": {}, "credentials": {"wachtwoord": "x"}},
        )
        assert response.status_code == 422


ARP_AFTER_SWEEP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.100.1    0x1         0x2         58:04:4f:9c:ff:3b     *        eth0
192.168.100.50   0x1         0x2         f4:65:0b:aa:a1:cb     *        eth0
192.168.100.51   0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.100.52   0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.100.53   0x1         0x6         00:00:00:00:00:00     *        eth0
192.168.100.54   0x1         0xnope      aa:bb:cc:dd:ee:ff     *        eth0
kapotte regel
"""


def test_arp_table_skips_neighbours_the_sweep_never_resolved() -> None:
    """Een ping-sweep laat lege buren achter; die zijn geen apparaten."""
    neighbours = providers_module.parse_arp_table(ARP_AFTER_SWEEP)
    assert sorted(neighbours) == ["192.168.100.1", "192.168.100.50"]
    assert neighbours["192.168.100.1"]["mac"] == "58:04:4f:9c:ff:3b"


def test_arp_table_ignores_empty_mac_even_when_flagged_complete() -> None:
    """De nul-MAC wordt los van de vlaggen geweerd, niet alleen via 0x0."""
    assert "192.168.100.53" not in providers_module.parse_arp_table(ARP_AFTER_SWEEP)


def test_arp_table_survives_a_malformed_flags_column() -> None:
    assert "192.168.100.54" not in providers_module.parse_arp_table(ARP_AFTER_SWEEP)


def test_arp_table_normalises_case() -> None:
    table = "kop\n10.0.0.9  0x1  0x2  AA:BB:CC:DD:EE:FF  *  eth0\n"
    assert providers_module.parse_arp_table(table)["10.0.0.9"]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_zero_mac_is_not_an_address() -> None:
    """Anders koppelt _store_record alles met die MAC aan één entity."""
    assert providers_module.normalize_mac("00:00:00:00:00:00") is None
    assert providers_module.normalize_mac("00-00-00-00-00-00") is None
    assert providers_module.normalize_mac("58:04:4f:9c:ff:3b") == "58:04:4f:9c:ff:3b"


def test_two_devices_without_a_mac_stay_separate() -> None:
    """Twee ping-vondsten zonder MAC mogen niet in elkaar schuiven."""
    first = make_discovery("192.168.1.71", "een", ip_address="192.168.1.71", mac_address=EMPTY_MAC)
    second = make_discovery("192.168.1.72", "twee", ip_address="192.168.1.72", mac_address=EMPTY_MAC)
    assert first != second


def test_dhcp_arp_test_refuses_untrusted_subnet() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/providers/dhcp-arp/test", headers=headers,
            json={"config": {"scan": True, "subnets": ["10.20.30.0/30"]}, "credentials": {}},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "PATCH_TRUSTED_SUBNETS" in response.json()["summary"]
