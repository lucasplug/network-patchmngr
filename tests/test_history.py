from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from patch_manager import db as db_module
from patch_manager.db import utcnow
from patch_manager.main import app, database

from tests.conftest import CREDENTIALS


def make_entity(name: str, status: str = "up") -> str:
    entity_id = str(uuid.uuid4())
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO entities(id,name,type,origin,status,notes,created_at,updated_at)
               VALUES(?,?,?,?,?,'',?,?)""",
            (entity_id, name, "device", "manual", status, now, now),
        )
    return entity_id


def set_status(entity_id: str, status: str) -> None:
    with database.transaction() as connection:
        connection.execute("UPDATE entities SET status=? WHERE id=?", (status, entity_id))


def test_sampling_writes_one_row_per_slot_and_counts_uptime() -> None:
    entity_id = make_entity("Sampler")
    assert database.record_samples() >= 1
    # Binnen hetzelfde 5-minutenvak komt er niets bij.
    assert database.record_samples() == 0
    samples = database.fetch_all("SELECT * FROM entity_samples WHERE entity_id=?", (entity_id,))
    assert len(samples) == 1
    assert samples[0]["status"] == "up"

    day = database.fetch_one("SELECT * FROM entity_days WHERE entity_id=?", (entity_id,))
    assert day["samples_total"] == 1 and day["samples_up"] == 1 and day["flips"] == 0


def test_sampling_records_metrics_and_derives_memory_percent() -> None:
    entity_id = make_entity("Metriek")
    database.record_samples({entity_id: {"cpu_percent": 31.456, "memory_used": 512, "memory_total": 2048, "latency_ms": 7.2}})
    sample = database.fetch_one("SELECT * FROM entity_samples WHERE entity_id=?", (entity_id,))
    assert sample["cpu_percent"] == 31.46
    assert sample["memory_percent"] == 25.0
    assert sample["latency_ms"] == 7.2


def test_status_flip_is_counted_once_per_change() -> None:
    entity_id = make_entity("Wisselaar", status="up")
    database.record_samples()
    # Simuleer een eerder meetpunt zodat de volgende vergelijking een wissel ziet.
    with database.transaction() as connection:
        connection.execute("UPDATE entity_samples SET sampled_at=? WHERE entity_id=?",
                           ((datetime.now(UTC) - timedelta(minutes=30)).isoformat(timespec="seconds"), entity_id))
    set_status(entity_id, "down")
    database.record_samples()
    day = database.fetch_one("SELECT * FROM entity_days WHERE entity_id=?", (entity_id,))
    assert day["flips"] == 1
    assert day["last_change_at"]
    assert day["samples_up"] == 1 and day["samples_total"] == 2


def test_retention_drops_old_samples_days_and_audit_rows() -> None:
    entity_id = make_entity("Oud")
    old_sample = (datetime.now(UTC) - timedelta(hours=db_module.SAMPLE_RETENTION_HOURS + 2)).isoformat(timespec="seconds")
    old_day = (datetime.now(UTC) - timedelta(days=db_module.DAY_RETENTION_DAYS + 5)).date().isoformat()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO entity_samples(entity_id,sampled_at,status) VALUES(?,?,'up')", (entity_id, old_sample)
        )
        connection.execute(
            "INSERT INTO entity_days(entity_id,day,samples_total,samples_up) VALUES(?,?,10,10)", (entity_id, old_day)
        )
    for index in range(db_module.AUDIT_RETENTION_ROWS + 25):
        pass
    with database.transaction() as connection:
        connection.executemany(
            "INSERT INTO audit_log(id,actor_user_id,action,target_type,target_id,details_json,created_at) VALUES(?,NULL,'test','x',NULL,'{}',?)",
            [(str(uuid.uuid4()), utcnow()) for _ in range(db_module.AUDIT_RETENTION_ROWS + 25)],
        )

    database.prune_history()

    assert database.fetch_one(
        "SELECT COUNT(*) AS n FROM entity_samples WHERE sampled_at=?", (old_sample,)
    )["n"] == 0
    assert database.fetch_one("SELECT COUNT(*) AS n FROM entity_days WHERE day=?", (old_day,))["n"] == 0
    assert database.fetch_one("SELECT COUNT(*) AS n FROM audit_log")["n"] == db_module.AUDIT_RETENTION_ROWS


def test_history_endpoint_reports_uptime_percentage() -> None:
    entity_id = make_entity("Rapportage")
    with database.transaction() as connection:
        connection.executemany(
            "INSERT INTO entity_days(entity_id,day,samples_total,samples_up,flips) VALUES(?,?,?,?,?)",
            [(entity_id, f"2026-07-{day:02d}", 288, 288 if day % 2 else 216, 0 if day % 2 else 2) for day in range(1, 11)],
        )
    with TestClient(app) as client:
        client.post("/api/auth/login", json=CREDENTIALS)
        history = client.get(f"/api/entities/{entity_id}/history")
        assert history.status_code == 200, history.text
        payload = history.json()
        # De onderhoudslus kan intussen ook een meetpunt van vandaag hebben gezet.
        july = [row for row in payload["days"] if row["day"].startswith("2026-07")]
        assert len(july) == 10
        assert payload["days"] == sorted(payload["days"], key=lambda row: row["day"]), "oplopend op datum"
        total = sum(row["samples_total"] for row in payload["days"])
        up = sum(row["samples_up"] for row in payload["days"])
        assert payload["uptime_percent"] == round(up / total * 100, 1)
        assert payload["flips"] >= 10
        assert client.get(f"/api/entities/{uuid.uuid4()}/history").status_code == 404


def test_history_stays_within_the_documented_budget() -> None:
    # Het plafond uit het ontwerp: 40 entities x 12 samples/uur x 48 uur.
    expected_max = 40 * 12 * db_module.SAMPLE_RETENTION_HOURS
    assert expected_max == 23040
    assert db_module.DAY_RETENTION_DAYS * 40 == 29200
