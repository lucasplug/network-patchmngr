from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from patch_manager import categories
from patch_manager.main import app, database, providers

from tests.conftest import CREDENTIALS


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def discovery(external_id: str, name: str, **kwargs) -> str:
    return providers._store_record(
        "dhcp-arp", external_id, "network_device", {"ip": kwargs.get("ip_address", "192.168.1.90")},
        name=name, entity_type="device", status="up", **kwargs,
    )


# --- categorieën ------------------------------------------------------------

def test_categories_cover_every_type_the_providers_produce() -> None:
    """Een provider mag geen rol opleveren die nergens in een keuzelijst staat."""
    produced = {"device", "host", "vm", "lxc", "container", "service"}
    assert produced <= set(categories.ENTITY_KEYS)


def test_physical_and_entity_categories_do_not_overlap() -> None:
    """Poorten horen bij physical_devices; die scheiding moet in de lijst zitten."""
    assert set(categories.ENTITY_KEYS).isdisjoint(categories.PHYSICAL_KEYS)
    assert "switch" in categories.PHYSICAL_KEYS
    assert "container" in categories.ENTITY_KEYS


def test_bootstrap_ships_the_category_list() -> None:
    with TestClient(app) as client:
        login(client)
        payload = client.get("/api/bootstrap").json()["categories"]
        by_key = {item["key"]: item for item in payload}
        assert by_key["container"]["label"] == "Container"
        assert by_key["container"]["physical"] is False
        assert by_key["switch"]["physical"] is True


def test_unknown_category_keeps_something_readable() -> None:
    assert categories.label_for("iets-nieuws") == "iets-nieuws"
    assert categories.normalize("iets-nieuws") == "device"
    assert categories.normalize("iets-nieuws", physical=True) == "switch"
    assert categories.normalize("container", physical=True) == "switch"


# --- poortloze uplink -------------------------------------------------------

def test_uplink_attaches_a_device_without_using_a_port() -> None:
    entity_id = discovery("wifi-1", "telefoon", ip_address="192.168.1.91")
    with TestClient(app) as client:
        headers = login(client)
        response = client.put(
            f"/api/entities/{entity_id}/uplink", headers=headers,
            json={"physical_device_id": "deco-01"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["uplink_device_id"] == "deco-01"

        row = next(item for item in client.get("/api/discoveries").json() if item["id"] == entity_id)
        assert row["uplink_device_name"] == "Deco XE75 Pro 01"
        # Geplaatst is geplaatst, ook zonder kabel.
        assert row["linked"] is True
        assert client.get("/api/summary").json()["counts"]["unlinked"] == 0


def test_uplink_can_be_cleared() -> None:
    entity_id = discovery("wifi-2", "tablet", ip_address="192.168.1.92")
    with TestClient(app) as client:
        headers = login(client)
        client.put(f"/api/entities/{entity_id}/uplink", headers=headers, json={"physical_device_id": "deco-02"})
        response = client.put(f"/api/entities/{entity_id}/uplink", headers=headers, json={"physical_device_id": None})
        assert response.status_code == 200, response.text
        assert response.json()["uplink_device_id"] is None


def test_uplink_refuses_an_unknown_network_device() -> None:
    entity_id = discovery("wifi-3", "lamp", ip_address="192.168.1.93")
    with TestClient(app) as client:
        headers = login(client)
        response = client.put(
            f"/api/entities/{entity_id}/uplink", headers=headers,
            json={"physical_device_id": "bestaat-niet"},
        )
        assert response.status_code == 404


def test_a_cable_and_an_uplink_never_coexist() -> None:
    """Twee antwoorden op 'waar zit dit ding' is erger dan één onvolledig antwoord."""
    entity_id = discovery("wired-1", "printer", ip_address="192.168.1.94")
    with TestClient(app) as client:
        headers = login(client)
        client.put(f"/api/entities/{entity_id}/uplink", headers=headers, json={"physical_device_id": "deco-03"})

        # De kabel wint en ruimt de uplink op.
        cabled = client.put(
            "/api/ports/switch-01-p5/cable", headers=headers, json={"b_entity_id": entity_id}
        )
        assert cabled.status_code == 200, cabled.text
        assert database.fetch_one("SELECT uplink_device_id FROM entities WHERE id=?", (entity_id,))["uplink_device_id"] is None

        # En andersom mag het niet stilletjes gebeuren.
        blocked = client.put(
            f"/api/entities/{entity_id}/uplink", headers=headers, json={"physical_device_id": "deco-03"}
        )
        assert blocked.status_code == 409


def test_uplink_shows_up_in_the_topology_as_its_own_relation() -> None:
    wifi = discovery("wifi-4", "camera-schuur", ip_address="192.168.1.95")
    wired = discovery("wired-2", "onbekende-poort", ip_address="192.168.1.96")
    with TestClient(app) as client:
        headers = login(client)
        client.put(f"/api/entities/{wifi}/uplink", headers=headers, json={"physical_device_id": "deco-01"})
        client.put(f"/api/entities/{wired}/uplink", headers=headers, json={"physical_device_id": "switch-02"})
        relations = client.get("/api/bootstrap").json()["topology"]["relations"]
        by_id = {relation["id"]: relation for relation in relations}
        # Een Deco is draadloos, een switch niet: dat verschil moet zichtbaar zijn.
        assert by_id[f"uplink:{wifi}"]["relation_type"] == "wireless"
        assert by_id[f"uplink:{wired}"]["relation_type"] == "portless"


# --- Glances met meerdere endpoints ----------------------------------------

def glances_transport(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/system"):
            return httpx.Response(200, json={"hostname": f"host-{request.url.port}", "uptime": 42})
        if request.url.path.endswith("/containers"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: original(*a, **{**kw, "transport": httpx.MockTransport(handler)}))


def test_glances_binds_each_endpoint_to_the_chosen_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twee machines, twee devices — en de hostnaam van Glances beslist niets."""
    seen: list[str] = []
    glances_transport(monkeypatch, seen)
    with TestClient(app) as client:
        headers = login(client)
        first = client.post("/api/entities", headers=headers, json={"name": "Docker VM", "type": "host"}).json()
        second = client.post("/api/entities", headers=headers, json={"name": "Proxmox host", "type": "host"}).json()
        saved = client.patch(
            "/api/providers/glances", headers=headers,
            json={"enabled": True, "poll_interval_seconds": 60, "credentials": {}, "config": {"endpoints": [
                {"name": "docker", "url": "http://host-a:61208/api/4", "entity_id": first["id"]},
                {"name": "pve", "url": "http://host-b:61209/api/4", "entity_id": second["id"]},
            ]}},
        )
        assert saved.status_code == 200, saved.text
        result = client.post("/api/providers/glances/sync", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["records"] == 2

    # Beide endpoints zijn bezocht en elk record hangt aan het gekozen device.
    assert any(":61208" in url for url in seen) and any(":61209" in url for url in seen)
    bound = {
        row["external_id"]: row["entity_id"]
        for row in database.fetch_all("SELECT external_id,entity_id FROM provider_records WHERE provider_id='glances'")
    }
    assert bound[f"host:{first['id']}"] == first["id"]
    assert bound[f"host:{second['id']}"] == second["id"]

    # Handmatige namen blijven staan; Glances observeert alleen.
    assert database.fetch_one("SELECT name FROM entities WHERE id=?", (first["id"],))["name"] == "Docker VM"


def test_glances_refuses_an_endpoint_without_a_device(monkeypatch: pytest.MonkeyPatch) -> None:
    glances_transport(monkeypatch, [])
    with TestClient(app) as client:
        headers = login(client)
        client.patch(
            "/api/providers/glances", headers=headers,
            json={"enabled": True, "poll_interval_seconds": 60, "credentials": {}, "config": {"endpoints": [
                {"name": "vergeten", "url": "http://host-c:61208/api/4"},
            ]}},
        )
        response = client.post("/api/providers/glances/sync", headers=headers)
        assert response.status_code == 400, response.text
        assert "vergeten" in response.json()["detail"]


def test_glances_test_reports_each_endpoint_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Met vijf machines wil je weten wélke stuk is."""
    glances_transport(monkeypatch, [])
    with TestClient(app) as client:
        headers = login(client)
        entity = client.post("/api/entities", headers=headers, json={"name": "Host A", "type": "host"}).json()
        response = client.post(
            "/api/providers/glances/test", headers=headers,
            json={"config": {"endpoints": [
                {"name": "werkt", "url": "http://host-a:61208/api/4", "entity_id": entity["id"]},
                {"name": "geen-device", "url": "http://host-b:61208/api/4"},
            ]}, "credentials": {}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert "werkt →" in body["summary"]
        assert "geen-device: geen device gekozen" in body["summary"]
