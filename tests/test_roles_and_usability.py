from __future__ import annotations

from fastapi.testclient import TestClient

from patch_manager.db import utcnow
from patch_manager.main import app, database
from tests.conftest import CREDENTIALS


VIEWER = {"username": "sanne", "password": "veilig kijkwachtwoord"}


def login(client: TestClient, credentials: dict[str, str] = CREDENTIALS) -> dict[str, str]:
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_admin_can_create_a_read_only_viewer() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/users",
            headers=headers,
            json={**VIEWER, "role": "viewer"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "viewer"
        assert VIEWER["password"] not in response.text
        assert any(user["username"] == "sanne" for user in client.get("/api/bootstrap").json()["users"])


def test_viewer_gets_safe_data_cannot_mutate_and_can_logout() -> None:
    with TestClient(app) as client:
        admin_headers = login(client)
        client.post("/api/users", headers=admin_headers, json={**VIEWER, "role": "viewer"})
        client.post("/api/auth/logout", headers=admin_headers)

        viewer_headers = login(client, VIEWER)
        status = client.get("/api/auth/status").json()
        assert status["role"] == "viewer"
        payload = client.get("/api/bootstrap").json()
        assert payload["auth"] == {"username": "sanne", "role": "viewer", "can_write": False}
        assert payload["providers"] == []
        assert payload["backups"] == []
        assert payload["audit_log"] == []
        assert payload["users"] == []
        for path in ("/api/backups", "/api/config/export", "/api/wizard/info", "/api/discoveries"):
            assert client.get(path).status_code == 403, path

        denied = client.post(
            "/api/entities",
            headers=viewer_headers,
            json={"name": "Mag niet", "type": "device"},
        )
        assert denied.status_code == 403
        assert "Alleen beheerders" in denied.json()["detail"]
        assert client.post("/api/auth/logout", headers=viewer_headers).status_code == 200


def test_last_admin_and_active_account_are_protected() -> None:
    with TestClient(app) as client:
        headers = login(client)
        current = next(user for user in client.get("/api/bootstrap").json()["users"] if user["username"] == "lucas")
        response = client.delete(
            f"/api/users/{current['id']}?confirm=lucas",
            headers=headers,
        )
        assert response.status_code == 409


def test_enabled_provider_is_not_saved_when_connection_test_fails() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.patch(
            "/api/providers/portainer",
            headers=headers,
            json={
                "enabled": True,
                "poll_interval_seconds": 60,
                "config": {"base_url": "https://portainer.local", "verify_tls": True},
                "credentials": {},
            },
        )
        assert response.status_code == 422
        assert "API-key" in response.json()["detail"]
        assert database.fetch_one("SELECT enabled FROM providers WHERE id='portainer'")["enabled"] == 0


def test_internet_monitor_is_distinct_from_speedtest_failure() -> None:
    with TestClient(app) as client:
        headers = login(client)
        now = utcnow()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO entities(id,name,type,origin,status,status_updated_at,created_at,updated_at)
                   VALUES('internet-monitor','Externe ping','service','manual','up',?,?,?)""",
                (now, now, now),
            )
            connection.execute(
                "UPDATE speedtest_settings SET last_error='librespeed-cli is niet geïnstalleerd' WHERE id=1"
            )
        response = client.patch(
            "/api/speedtest/settings",
            headers=headers,
            json={
                "enabled": False,
                "interval_seconds": 21600,
                "server_id": None,
                "interface_name": None,
                "duration_seconds": 10,
                "monitor_entity_id": "internet-monitor",
            },
        )
        assert response.status_code == 200, response.text
        speed = client.get("/api/speedtest").json()
        assert speed["internet"]["status"] == "up"
        assert speed["settings"]["last_error"].startswith("librespeed-cli")
        internet_node = next(
            node for node in client.get("/api/bootstrap").json()["topology"]["nodes"]
            if node["id"] == "special:internet"
        )
        assert internet_node["status"] == "up"
