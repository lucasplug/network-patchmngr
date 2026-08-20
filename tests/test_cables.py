from __future__ import annotations

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from patch_manager import oui
from patch_manager.main import app, database
from patch_manager.topology import trace_from_port

from tests.conftest import CREDENTIALS, TEST_ROOT


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def make_entity(client: TestClient, headers: dict[str, str], name: str) -> str:
    response = client.post(
        "/api/entities", headers=headers,
        json={"name": name, "type": "device", "notes": ""},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def make_panel(client: TestClient, headers: dict[str, str], name: str, ports: int = 4) -> str:
    response = client.post(
        "/api/physical-devices", headers=headers,
        json={"name": name, "type": "patch_panel", "model": "", "location": "", "notes": "", "ports": ports},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_patch_panel_gets_paired_front_and_rear_ports() -> None:
    with TestClient(app) as client:
        headers = login(client)
        panel_id = make_panel(client, headers, "Paneel A", ports=3)
    ports = database.fetch_all("SELECT * FROM ports WHERE physical_device_id=? ORDER BY number,side", (panel_id,))
    assert len(ports) == 6
    fronts = {port["id"]: port for port in ports if port["side"] == "front"}
    rears = {port["id"]: port for port in ports if port["side"] == "rear"}
    assert len(fronts) == len(rears) == 3
    for front in fronts.values():
        assert front["peer_port_id"] in rears
        assert rears[front["peer_port_id"]]["peer_port_id"] == front["id"]


def test_cable_between_two_ports_and_occupancy_rules() -> None:
    with TestClient(app) as client:
        headers = login(client)
        panel_id = make_panel(client, headers, "Paneel B", ports=2)
        created = client.post(
            "/api/cables", headers=headers,
            json={"a_port_id": "switch-01-p1", "b_port_id": f"{panel_id}-f1", "label": "trunk", "color": "geel"},
        )
        assert created.status_code == 200, created.text

        # Beide uiteinden zijn nu bezet, ongeacht welke kolom ze bezetten.
        for payload in (
            {"a_port_id": "switch-01-p1", "b_entity_id": make_entity(client, headers, "X")},
            {"a_port_id": f"{panel_id}-f1", "b_entity_id": make_entity(client, headers, "Y")},
        ):
            blocked = client.post("/api/cables", headers=headers, json=payload)
            assert blocked.status_code == 409, blocked.text

        # Precies één ander uiteinde is verplicht.
        assert client.post(
            "/api/cables", headers=headers,
            json={"a_port_id": "switch-01-p2", "b_port_id": f"{panel_id}-f2", "b_entity_id": make_entity(client, headers, "Z")},
        ).status_code == 422
        assert client.post(
            "/api/cables", headers=headers, json={"a_port_id": "switch-01-p2"},
        ).status_code == 422
        assert client.post(
            "/api/cables", headers=headers, json={"a_port_id": "switch-01-p2", "b_port_id": "switch-01-p2"},
        ).status_code == 422


def test_one_cable_per_entity() -> None:
    with TestClient(app) as client:
        headers = login(client)
        entity_id = make_entity(client, headers, "NAS uniek")
        first = client.post(
            "/api/cables", headers=headers, json={"a_port_id": "switch-01-p1", "b_entity_id": entity_id}
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/cables", headers=headers, json={"a_port_id": "switch-01-p2", "b_entity_id": entity_id}
        )
        assert second.status_code == 409


def test_trace_runs_through_patch_panel_to_entity() -> None:
    with TestClient(app) as client:
        headers = login(client)
        panel_id = make_panel(client, headers, "Paneel C", ports=2)
        entity_id = make_entity(client, headers, "Werkplek-pc")
        assert client.post(
            "/api/cables", headers=headers,
            json={"a_port_id": "switch-01-p3", "b_port_id": f"{panel_id}-f1"},
        ).status_code == 200
        assert client.post(
            "/api/cables", headers=headers,
            json={"a_port_id": f"{panel_id}-r1", "b_entity_id": entity_id},
        ).status_code == 200

        trace = client.get("/api/ports/switch-01-p3/trace").json()
        assert trace["entity_id"] == entity_id
        assert [step["kind"] for step in trace["steps"]] == ["start", "port", "through", "entity"]
        assert trace["steps"][-1]["label"] == "Werkplek-pc"

        # De patchview toont het eind-device op de switchpoort zelf.
        devices = {item["id"]: item for item in client.get("/api/bootstrap").json()["physical_devices"]}
        port = next(item for item in devices["switch-01"]["ports"] if item["id"] == "switch-01-p3")
        assert port["link_kind"] == "port"
        assert port["entity_name"] == "Werkplek-pc"

    # Topologie tekent één patchrelatie: switch → device, panel wordt doorlopen.
    relation = database.fetch_one(
        "SELECT * FROM topology_relations WHERE id=?", ("patch:switch-01-p3",)
    )
    assert relation["to_node_id"] == f"entity:{entity_id}"
    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM topology_relations WHERE source='patch'"
    )["n"] == 1


def test_trace_stops_at_loose_end_and_respects_hop_limit() -> None:
    ports = {"a": {"id": "a", "peer_port_id": "b"}, "b": {"id": "b", "peer_port_id": "a"}}
    # Kabel van a naar b sluit het paneel op zichzelf aan: zonder hoplimiet loopt dit rond.
    cable = {"id": "c1", "a_port_id": "a", "b_port_id": "b", "b_entity_id": None}
    hops, entity_id = trace_from_port("a", ports, {"a": cable, "b": cable})
    assert entity_id is None
    assert len(hops) <= 20

    hops, entity_id = trace_from_port("onbekend", {}, {})
    assert (hops, entity_id) == ([], None)


def test_port_cable_endpoint_replaces_existing_cable() -> None:
    with TestClient(app) as client:
        headers = login(client)
        first_entity = make_entity(client, headers, "Eerste")
        second_entity = make_entity(client, headers, "Tweede")
        assert client.put(
            "/api/ports/switch-01-p4/cable", headers=headers,
            json={"b_entity_id": first_entity, "label": "oud"},
        ).status_code == 200
        replaced = client.put(
            "/api/ports/switch-01-p4/cable", headers=headers,
            json={"b_entity_id": second_entity, "label": "nieuw"},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["label"] == "nieuw"
        assert database.fetch_one(
            "SELECT COUNT(*) AS n FROM cables WHERE a_port_id='switch-01-p4'"
        )["n"] == 1


def test_shrinking_a_device_is_blocked_by_a_cable_on_a_removed_port() -> None:
    with TestClient(app) as client:
        headers = login(client)
        entity_id = make_entity(client, headers, "Laatste poort")
        assert client.put(
            "/api/ports/switch-01-p8/cable", headers=headers, json={"b_entity_id": entity_id}
        ).status_code == 200
        blocked = client.patch(
            "/api/physical-devices/switch-01", headers=headers,
            json={"name": "Switch", "type": "switch", "model": "", "location": "", "notes": "", "ports": 4},
        )
        assert blocked.status_code == 409
        assert "8" in blocked.json()["detail"]


def test_summary_is_lighter_than_bootstrap_but_has_the_live_bits() -> None:
    with TestClient(app) as client:
        headers = login(client)
        entity_id = make_entity(client, headers, "Meetpunt")
        client.put("/api/ports/switch-02-p2/cable", headers=headers, json={"b_entity_id": entity_id})
        summary = client.get("/api/summary")
        assert summary.status_code == 200, summary.text
        payload = summary.json()
        # app_links hoort er wél bij: het appdashboard moet zijn statusbolletjes
        # kunnen bijwerken zonder een volledige bootstrap op te halen.
        assert set(payload) == {"counts", "entities", "metrics", "providers", "speedtest", "app_links"}
        assert payload["counts"]["patched"] == 1
        assert any(item["id"] == entity_id for item in payload["entities"])
        # Geen inventaris, audit of topologie in de poll-payload.
        assert "physical_devices" not in payload and "audit_log" not in payload


def test_vendor_lookup_reads_local_ieee_file(monkeypatch: pytest.MonkeyPatch) -> None:
    oui_file = TEST_ROOT / "oui.csv"
    oui_file.write_text(
        "Registry,Assignment,Organization Name,Organization Address\n"
        "MA-L,B0BE76,TP-LINK TECHNOLOGIES CO.LTD.,Shenzhen\n"
        "MA-L,001132,Synology Incorporated,Taipei\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATCH_OUI_FILE", str(oui_file))
    oui.reset_cache()
    try:
        assert oui.vendor_for_mac("b0:be:76:11:22:33") == "TP-LINK TECHNOLOGIES CO.LTD."
        assert oui.vendor_for_mac("00-11-32-aa-bb-cc") == "Synology Incorporated"
        assert oui.vendor_for_mac("ff:ff:ff:00:00:01") is None
        assert oui.vendor_for_mac(None) is None
        assert oui.vendor_for_mac("kort") is None
    finally:
        oui.reset_cache()


def test_discovery_gets_vendor_from_oui(monkeypatch: pytest.MonkeyPatch) -> None:
    oui_file = TEST_ROOT / "oui-discovery.csv"
    oui_file.write_text("MA-L,B0BE76,TP-LINK TECHNOLOGIES CO.LTD.,Shenzhen\n", encoding="utf-8")
    monkeypatch.setenv("PATCH_OUI_FILE", str(oui_file))
    oui.reset_cache()
    try:
        from patch_manager.main import providers

        entity_id = providers._store_record(
            "dhcp-arp", f"vendor-{uuid.uuid4()}", "network_device", {"ip": "192.168.1.77"},
            name="192.168.1.77", entity_type="device", status="up",
            ip_address="192.168.1.77", mac_address="b0:be:76:aa:bb:cc",
        )
        entity = database.fetch_one("SELECT vendor FROM entities WHERE id=?", (entity_id,))
        assert entity["vendor"] == "TP-LINK TECHNOLOGIES CO.LTD."
    finally:
        oui.reset_cache()


def test_a_cable_between_two_network_devices_is_drawn() -> None:
    """ONT naar Deco: beide uiteinden zijn poorten, geen device."""
    with TestClient(app) as client:
        headers = login(client)
        cabled = client.put(
            "/api/ports/ont-01-p1/cable", headers=headers,
            json={"b_port_id": "deco-01-p1", "label": "glasvezel-uplink"},
        )
        assert cabled.status_code == 200, cabled.text
        relations = client.get("/api/bootstrap").json()["topology"]["relations"]
        trunks = [r for r in relations if r["id"].startswith("trunk:")]
        assert len(trunks) == 1, trunks
        assert {trunks[0]["from_node_id"], trunks[0]["to_node_id"]} == {"physical:ont-01", "physical:deco-01"}
        # Het kabellabel, niet "Poort 1": de kabel is hier het onderwerp.
        assert trunks[0]["label"] == "glasvezel-uplink"


def test_the_internet_arrives_at_the_ont() -> None:
    with TestClient(app) as client:
        login(client)
        topology = client.get("/api/bootstrap").json()["topology"]
        assert any(node["id"] == "physical:ont-01" for node in topology["nodes"])
        link = next(r for r in topology["relations"] if r["id"] == "manual:internet-ont")
        assert link["from_node_id"] == "special:internet"
        assert link["to_node_id"] == "physical:ont-01"
        # De oude spookknoop die een Deco nabootste is weg.
        assert not any(node["id"] == "special:router" for node in topology["nodes"])


def test_port_cable_replace_rolls_back_when_insert_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAT-PAT-017/SYS-020: delete en insert vormen één transactie."""
    import patch_manager.main as main_module

    with TestClient(app) as client:
        headers = login(client)
        original_entity = make_entity(client, headers, "Blijft aangesloten")
        replacement_entity = make_entity(client, headers, "Nieuwe aansluiting")
        original = client.put(
            "/api/ports/switch-01-p4/cable", headers=headers,
            json={"b_entity_id": original_entity, "label": "oude kabel"},
        ).json()

        def fail_insert(*_args, **_kwargs):
            raise sqlite3.OperationalError("geïnjecteerde insertfout")

        monkeypatch.setattr(main_module, "insert_cable", fail_insert)
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            login_headers = login(failing_client)
            response = failing_client.put(
                "/api/ports/switch-01-p4/cable", headers=login_headers,
                json={"b_entity_id": replacement_entity, "label": "nieuwe kabel"},
            )
        assert response.status_code == 500

    preserved = database.fetch_one("SELECT * FROM cables WHERE id=?", (original["id"],))
    assert preserved is not None
    assert preserved["b_entity_id"] == original_entity
    assert preserved["label"] == "oude kabel"


def test_moving_a_cable_is_one_server_side_operation() -> None:
    with TestClient(app) as client:
        headers = login(client)
        entity_id = make_entity(client, headers, "Verplaatsbaar")
        original = client.put(
            "/api/ports/switch-01-p1/cable", headers=headers,
            json={"b_entity_id": entity_id, "label": "P-01"},
        ).json()
        moved = client.put(
            "/api/ports/switch-01-p2/cable", headers=headers,
            json={"b_entity_id": entity_id, "label": "P-01", "move_cable_id": original["id"]},
        )
        assert moved.status_code == 200, moved.text
    cables = database.fetch_all("SELECT a_port_id,b_entity_id,label FROM cables")
    assert cables == [{"a_port_id": "switch-01-p2", "b_entity_id": entity_id, "label": "P-01"}]


def test_a_patch_panel_is_traversed_not_drawn() -> None:
    """Anders krijgt elke verbinding via een paneel er een lijn naar het paneel bij."""
    with TestClient(app) as client:
        headers = login(client)
        panel = client.post(
            "/api/physical-devices", headers=headers,
            json={"name": "Paneel zolder", "type": "patch_panel", "ports": 4},
        ).json()["id"]
        entity = client.post("/api/entities", headers=headers, json={"name": "Werkplek", "type": "host"}).json()
        client.put(f"/api/ports/switch-01-p2/cable", headers=headers, json={"b_port_id": f"{panel}-f1"})
        client.put(f"/api/ports/{panel}-r1/cable", headers=headers, json={"b_entity_id": entity["id"]})

        relations = client.get("/api/bootstrap").json()["topology"]["relations"]
        assert not [r for r in relations if r["id"].startswith("trunk:")], "paneel wordt als eindpunt getekend"
        patched = [r for r in relations if r["id"].startswith("patch:")]
        assert len(patched) == 1
        assert patched[0]["from_node_id"] == "physical:switch-01"
        assert patched[0]["to_node_id"] == f"entity:{entity['id']}"


def test_ont_is_a_network_device_category() -> None:
    with TestClient(app) as client:
        login(client)
        payload = client.get("/api/bootstrap").json()
        ont = next(d for d in payload["physical_devices"] if d["id"] == "ont-01")
        assert ont["type"] == "ont"
        assert len(ont["ports"]) == 1
        category = next(c for c in payload["categories"] if c["key"] == "ont")
        assert category["physical"] is True and category["attachable"] is False
