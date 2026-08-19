from __future__ import annotations

from fastapi.testclient import TestClient

from patch_manager.main import app, database, providers

from tests.conftest import CREDENTIALS


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def monitor(name: str, status: str = "up") -> str:
    """Zoals Uptime Kuma een monitor aanlevert."""
    return providers._store_record(
        "uptime-kuma", f"monitor:{name}", "service", {"monitor": {"name": name}},
        name=name, entity_type="service", status=status,
    )


def test_an_app_borrows_its_status_from_a_monitor() -> None:
    kuma = monitor("Portainer")
    with TestClient(app) as client:
        headers = login(client)
        created = client.post(
            "/api/app-links", headers=headers,
            json={"name": "Portainer", "url": "https://192.168.1.12:9443", "icon": "🐳",
                  "group_name": "Beheer", "monitor_entity_id": kuma},
        )
        assert created.status_code == 200, created.text

        tile = client.get("/api/bootstrap").json()["app_links"][0]
        assert tile["name"] == "Portainer"
        assert tile["status"] == "up"
        assert tile["monitor_name"] == "Portainer"

    with database.transaction() as connection:
        connection.execute("UPDATE entities SET status='down' WHERE id=?", (kuma,))
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/bootstrap").json()["app_links"][0]["status"] == "down"


def test_an_app_without_a_monitor_stays_unknown() -> None:
    with TestClient(app) as client:
        headers = login(client)
        client.post("/api/app-links", headers=headers, json={"name": "Wiki", "url": "http://wiki.local"})
        tile = next(item for item in client.get("/api/bootstrap").json()["app_links"] if item["name"] == "Wiki")
        assert tile["status"] == "unknown"
        assert tile["monitor_name"] is None


def test_only_http_links_are_accepted() -> None:
    """Een tegel is een <a href>; javascript: daarin draait met je sessie erbij."""
    with TestClient(app) as client:
        headers = login(client)
        for bad in ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd", "ftp://host/x"]:
            response = client.post("/api/app-links", headers=headers, json={"name": "Stout", "url": bad})
            assert response.status_code == 422, f"{bad} werd geaccepteerd"
        ok = client.post("/api/app-links", headers=headers, json={"name": "Goed", "url": "https://host:8080/pad"})
        assert ok.status_code == 200, ok.text


def test_apps_are_grouped_and_ordered() -> None:
    with TestClient(app) as client:
        headers = login(client)
        for name, group, position in [("Zeta", "Beheer", 2), ("Alfa", "Beheer", 1), ("Media", "Thuis", 0)]:
            client.post(
                "/api/app-links", headers=headers,
                json={"name": name, "url": f"http://{name.lower()}.local", "group_name": group, "position": position},
            )
        tiles = client.get("/api/bootstrap").json()["app_links"]
        assert [(t["group_name"], t["name"]) for t in tiles] == [
            ("Beheer", "Alfa"), ("Beheer", "Zeta"), ("Thuis", "Media"),
        ]


def test_an_app_can_be_edited_and_removed() -> None:
    with TestClient(app) as client:
        headers = login(client)
        created = client.post(
            "/api/app-links", headers=headers, json={"name": "Tijdelijk", "url": "http://x.local"},
        ).json()
        edited = client.patch(
            f"/api/app-links/{created['id']}", headers=headers,
            json={"name": "Blijvend", "url": "https://x.local/dashboard"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["name"] == "Blijvend"

        assert client.delete(f"/api/app-links/{created['id']}", headers=headers).status_code == 200
        assert database.fetch_one("SELECT id FROM app_links WHERE id=?", (created["id"],)) is None


def test_deleting_the_monitor_leaves_the_app_alone() -> None:
    """De tegel is handmatige invoer; een verdwenen observatie mag hem niet meenemen."""
    kuma = monitor("Vergankelijk")
    with TestClient(app) as client:
        headers = login(client)
        created = client.post(
            "/api/app-links", headers=headers,
            json={"name": "Blijft", "url": "http://blijft.local", "monitor_entity_id": kuma},
        ).json()
    with database.transaction() as connection:
        connection.execute("DELETE FROM entities WHERE id=?", (kuma,))
    row = database.fetch_one("SELECT monitor_entity_id FROM app_links WHERE id=?", (created["id"],))
    assert row is not None and row["monitor_entity_id"] is None
