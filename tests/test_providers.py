from __future__ import annotations

from fastapi.testclient import TestClient

from patch_manager.main import app, database, provider_secrets

from tests.conftest import CREDENTIALS


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_a_second_environment_of_the_same_type_is_allowed() -> None:
    """Twee Portainers of twee AdGuards: het schema stond er maar één toe."""
    with TestClient(app) as client:
        headers = login(client)
        created = client.post(
            "/api/providers", headers=headers,
            json={"type": "portainer", "name": "Portainer zolder"},
        )
        assert created.status_code == 200, created.text
        second = created.json()
        assert second["type"] == "portainer"
        assert second["id"] != "portainer"
        # Uit staat hij, en zonder voorbeeldadres uit het sjabloon.
        assert second["enabled"] is False
        assert second["config"]["base_url"] == ""

        rows = client.get("/api/bootstrap").json()["providers"]
        assert len([row for row in rows if row["type"] == "portainer"]) == 2


def test_the_two_environments_keep_separate_settings_and_secrets() -> None:
    with TestClient(app) as client:
        headers = login(client)
        second = client.post(
            "/api/providers", headers=headers, json={"type": "adguard", "name": "AdGuard zolder"},
        ).json()
        client.patch(
            f"/api/providers/{second['id']}", headers=headers,
            json={"enabled": True, "poll_interval_seconds": 300,
                  "config": {"base_url": "http://192.168.1.20:3000"},
                  "credentials": {"username": "zolder", "password": "geheim-zolder"}},
        )
        client.patch(
            "/api/providers/adguard", headers=headers,
            json={"enabled": True, "poll_interval_seconds": 300,
                  "config": {"base_url": "http://192.168.1.12:3000"},
                  "credentials": {"username": "meterkast", "password": "geheim-meterkast"}},
        )
    assert provider_secrets.get(second["id"])["password"] == "geheim-zolder"
    assert provider_secrets.get("adguard")["password"] == "geheim-meterkast"
    urls = {
        row["id"]: row["config_json"]
        for row in database.fetch_all("SELECT id,config_json FROM providers WHERE type='adguard'")
    }
    assert "192.168.1.20" in urls[second["id"]] and "192.168.1.12" in urls["adguard"]


def test_a_provider_can_be_renamed() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.patch(
            "/api/providers/portainer", headers=headers,
            json={"name": "Portainer meterkast", "enabled": False,
                  "poll_interval_seconds": 60, "config": {}, "credentials": {}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Portainer meterkast"


def test_the_last_environment_of_a_type_cannot_be_deleted() -> None:
    """Weggooien laat de app zonder adapter achter; uitzetten bewaart de instellingen."""
    with TestClient(app) as client:
        headers = login(client)
        response = client.delete("/api/providers/proxmox?confirm=Proxmox VE", headers=headers)
        assert response.status_code == 409
        assert "enige bron" in response.json()["detail"]


def test_a_second_environment_can_be_deleted_with_its_name() -> None:
    with TestClient(app) as client:
        headers = login(client)
        second = client.post(
            "/api/providers", headers=headers, json={"type": "glances", "name": "Glances tweede"},
        ).json()
        wrong = client.delete(f"/api/providers/{second['id']}?confirm=fout", headers=headers)
        assert wrong.status_code == 422

        ok = client.delete(f"/api/providers/{second['id']}?confirm=Glances tweede", headers=headers)
        assert ok.status_code == 200, ok.text
        assert database.fetch_one("SELECT id FROM providers WHERE id=?", (second["id"],)) is None


def test_an_unknown_type_is_refused() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/providers", headers=headers, json={"type": "verzonnen", "name": "Iets"},
        )
        assert response.status_code == 422
