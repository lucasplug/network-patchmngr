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


# --- zoeken, veranderingen, historie en CSV ---------------------------------

def test_search_finds_devices_network_gear_and_apps() -> None:
    with TestClient(app) as client:
        headers = login(client)
        client.post("/api/entities", headers=headers, json={"name": "Zolder-NAS", "type": "nas", "ip_address": "192.168.1.77"})
        client.post("/api/app-links", headers=headers, json={"name": "Zolderpaneel", "url": "http://zolder.local"})

        by_name = client.get("/api/search?q=zolder").json()
        assert {row["kind"] for row in by_name} == {"entity", "app"}

        # Ook op adres, want zo zoek je in de praktijk.
        by_ip = client.get("/api/search?q=192.168.1.77").json()
        assert any(row["label"] == "Zolder-NAS" for row in by_ip)

        # En netwerkapparaten uit de andere tabel.
        assert any(row["kind"] == "physical" for row in client.get("/api/search?q=SG108E").json())


def test_search_ignores_a_single_character() -> None:
    """Anders staat bij de eerste toetsaanslag je halve inventaris in beeld."""
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/search?q=a").json() == []


def test_changes_separates_new_from_gone() -> None:
    from patch_manager.db import utcnow
    fresh = monitor("Net-verschenen")
    old = monitor("Al-lang-weg", status="down")
    with database.transaction() as connection:
        connection.execute("UPDATE entities SET first_seen_at='2000-01-01T00:00:00+00:00',last_seen_at='2000-01-01T00:00:00+00:00' WHERE id=?", (old,))
        connection.execute("UPDATE entities SET first_seen_at=? WHERE id=?", (utcnow(), fresh))
    with TestClient(app) as client:
        login(client)
        data = client.get("/api/changes?days=7").json()
        assert any(item["id"] == fresh for item in data["appeared"])
        assert any(item["id"] == old for item in data["vanished"])
        # Iets dat 'up' is, is niet verdwenen, hoe oud last_seen_at ook is.
        assert not any(item["status"] == "up" for item in data["vanished"])


def test_a_switch_without_a_monitor_has_no_history() -> None:
    with TestClient(app) as client:
        login(client)
        body = client.get("/api/physical-devices/switch-01/history").json()
        assert body == {"monitor_entity_id": None, "days": [], "samples": []}


def test_a_switch_with_a_monitor_reports_that_monitors_history() -> None:
    kuma = monitor("Switch-ping")
    with TestClient(app) as client:
        headers = login(client)
        client.patch(
            "/api/physical-devices/switch-01", headers=headers,
            json={"name": "TP-Link SG108E 01", "type": "switch", "ports": 8, "monitor_entity_id": kuma},
        )
        body = client.get("/api/physical-devices/switch-01/history").json()
        assert body["monitor_entity_id"] == kuma
        assert "days" in body and "samples" in body


def test_csv_import_creates_and_then_updates_without_duplicating() -> None:
    csv_text = (
        "name,type,ip_address,mac_address,notes\n"
        "Werkkamer-pc,host,192.168.1.31,aa:11:22:33:44:55,bureau\n"
        "Printer,printer,192.168.1.32,,\n"
    )
    with TestClient(app) as client:
        headers = login(client)
        first = client.post("/api/entities/import-csv", headers=headers, files={"file": ("d.csv", csv_text, "text/csv")})
        assert first.status_code == 200, first.text
        assert first.json() == {"created": 2, "updated": 0, "problems": []}

        # Hetzelfde bestand nog eens: bijwerken, niet verdubbelen.
        second = client.post("/api/entities/import-csv", headers=headers, files={"file": ("d.csv", csv_text, "text/csv")})
        assert second.json()["created"] == 0 and second.json()["updated"] == 2
        assert len(database.fetch_all("SELECT id FROM entities WHERE name='Werkkamer-pc'")) == 1


def test_csv_import_names_the_rows_it_could_not_read() -> None:
    """Stil overslaan laat je denken dat alles goed ging."""
    with TestClient(app) as client:
        headers = login(client)
        body = client.post(
            "/api/entities/import-csv", headers=headers,
            files={"file": ("d.csv", "name,mac_address\n,geen-naam\nGoed,zz:zz:zz:zz:zz:zz\nPrima,\n", "text/csv")},
        ).json()
        assert body["created"] == 1
        assert any("regel 2" in problem for problem in body["problems"])
        assert any("regel 3" in problem and "MAC" in problem for problem in body["problems"])


def test_csv_import_refuses_a_file_without_a_header() -> None:
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/entities/import-csv", headers=headers,
            files={"file": ("d.csv", "192.168.1.1,iets\n", "text/csv")},
        )
        assert response.status_code == 422
