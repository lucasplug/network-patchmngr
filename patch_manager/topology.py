from __future__ import annotations

import json
import uuid
from typing import Any

from .db import Database, utcnow


MAX_TRACE_HOPS = 10


def trace_from_port(
    port_id: str,
    ports: dict[str, dict[str, Any]],
    by_port: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Volg kabels en front/rear-paren tot een device, een los eind of de hoplimiet.

    Elke hop is óf een kabel naar een volgende poort, óf de doorsteek binnen een
    patchpanel (front↔rear). Eindigt bij een entity-uiteinde of bij niets.
    """
    hops: list[dict[str, Any]] = []
    current = port_id
    for _ in range(MAX_TRACE_HOPS):
        cable = by_port.get(current)
        if not cable:
            return hops, None
        if cable["b_entity_id"]:
            hops.append({"kind": "cable", "cable_id": cable["id"], "entity_id": cable["b_entity_id"]})
            return hops, cable["b_entity_id"]
        other = cable["b_port_id"] if cable["a_port_id"] == current else cable["a_port_id"]
        if not other:
            return hops, None
        hops.append({"kind": "cable", "cable_id": cable["id"], "from_port_id": current, "port_id": other})
        peer = (ports.get(other) or {}).get("peer_port_id")
        if not peer:
            return hops, None
        hops.append({"kind": "through", "port_id": other, "peer_port_id": peer})
        current = peer
    return hops, None


def trace_entity(port_id: str, ports: dict[str, dict[str, Any]], by_port: dict[str, dict[str, Any]]) -> str | None:
    return trace_from_port(port_id, ports, by_port)[1]


def sync_topology_catalog(database: Database) -> None:
    """Project inventory into topology nodes without touching manual layout."""
    now = utcnow()
    with database.transaction() as connection:
        devices = connection.execute("SELECT * FROM physical_devices ORDER BY position").fetchall()
        for index, device in enumerate(devices):
            node_id = f"physical:{device['id']}"
            connection.execute(
                """INSERT INTO topology_nodes
                   (id,reference_type,reference_id,label,subtitle,node_type,x,y,width,height,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(reference_type,reference_id) DO UPDATE SET
                   label=CASE WHEN topology_nodes.metadata_json='{}' THEN excluded.label ELSE topology_nodes.label END,
                   subtitle=excluded.subtitle,node_type=excluded.node_type,updated_at=excluded.updated_at""",
                (node_id, "physical", device["id"], device["name"], device["model"], device["type"],
                 40 + index * 220, 230, 190, 64, now, now),
            )

        # Een monitor die de status van een netwerkapparaat levert, hoort niet
        # ook nog als losse knoop in beeld: dan staat hetzelfde ding er twee
        # keer. Zijn status zit al op het apparaat.
        entities = connection.execute(
            """SELECT * FROM entities WHERE ignored=0 AND archived=0
                 AND id NOT IN (SELECT monitor_entity_id FROM physical_devices
                                WHERE monitor_entity_id IS NOT NULL)"""
        ).fetchall()
        connection.execute(
            """DELETE FROM topology_nodes WHERE reference_type='entity' AND reference_id IN
                 (SELECT monitor_entity_id FROM physical_devices WHERE monitor_entity_id IS NOT NULL)"""
        )
        for index, entity in enumerate(entities):
            node_id = f"entity:{entity['id']}"
            parent_node_id = f"entity:{entity['parent_id']}" if entity["parent_id"] else None
            connection.execute(
                """INSERT INTO topology_nodes
                   (id,reference_type,reference_id,label,subtitle,node_type,parent_node_id,parent_source,x,y,width,height,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(reference_type,reference_id) DO UPDATE SET
                   label=CASE WHEN topology_nodes.metadata_json='{}' THEN excluded.label ELSE topology_nodes.label END,
                   subtitle=excluded.subtitle,node_type=excluded.node_type,
                   parent_node_id=CASE WHEN topology_nodes.parent_source='auto' THEN excluded.parent_node_id ELSE topology_nodes.parent_node_id END,
                   updated_at=excluded.updated_at""",
                (node_id, "entity", entity["id"], entity["name"],
                 entity["ip_address"] or entity["hostname"] or entity["type"], entity["type"],
                 parent_node_id, "auto", 40 + (index % 5) * 220, 390 + (index // 5) * 90, 180, 58, now, now),
            )

        # Patch relations are fully derived from the cable graph. A device port
        # whose trace ends at an entity becomes one relation; intermediate patch
        # panels are traversed, not drawn. Manual relations stay untouched.
        connection.execute("DELETE FROM topology_relations WHERE source='patch'")
        ports = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM ports").fetchall()}
        cables = [dict(row) for row in connection.execute("SELECT * FROM cables").fetchall()]
        by_port: dict[str, dict[str, Any]] = {}
        for cable in cables:
            by_port[cable["a_port_id"]] = cable
            if cable["b_port_id"]:
                by_port[cable["b_port_id"]] = cable
        for port_id, port in ports.items():
            if port["side"] != "front" or port_id not in by_port:
                continue
            entity_id = trace_entity(port_id, ports, by_port)
            if not entity_id:
                continue
            connection.execute(
                """INSERT OR REPLACE INTO topology_relations
                   (id,from_node_id,to_node_id,relation_type,label,source,locked,created_at,updated_at)
                   VALUES(?,?,?,?,?,'patch',1,?,?)""",
                (f"patch:{port_id}", f"physical:{port['physical_device_id']}",
                 f"entity:{entity_id}", "physical", port["label"], now, now),
            )

        # Poortloze uplinks: wifi-clients op een Deco, of een switchpoort die
        # nog niet bekend is. Zelfde herkomst 'patch' zodat ze mee opgeruimd
        # worden, maar een eigen relation_type zodat de tekening ze anders
        # weergeeft dan een echte kabel.
        uplinks = connection.execute(
            """SELECT e.id,e.uplink_device_id,d.type AS device_type FROM entities e
               JOIN physical_devices d ON d.id=e.uplink_device_id
               WHERE e.uplink_device_id IS NOT NULL AND e.ignored=0 AND e.archived=0"""
        ).fetchall()
        for entity in uplinks:
            wireless = entity["device_type"] in ("mesh_ap", "access_point")
            connection.execute(
                """INSERT OR REPLACE INTO topology_relations
                   (id,from_node_id,to_node_id,relation_type,label,source,locked,created_at,updated_at)
                   VALUES(?,?,?,?,?,'patch',1,?,?)""",
                # Niet "uplink": dat type bestond al voor de internet-routerlijn.
                (f"uplink:{entity['id']}", f"physical:{entity['uplink_device_id']}",
                 f"entity:{entity['id']}", "wireless" if wireless else "portless",
                 "wifi" if wireless else "poort onbekend", now, now),
            )


def topology_payload(database: Database) -> dict[str, Any]:
    sync_topology_catalog(database)
    # Een netwerkapparaat heeft geen eigen status; het leent die van de
    # observatie die erover gaat (monitor_entity_id). Vandaar de tweede join.
    nodes = database.fetch_all(
        """SELECT n.*,
                  COALESCE(e.status, m.status) AS status,
                  COALESCE(e.ip_address, m.ip_address) AS ip_address,
                  COALESCE(e.hostname, m.hostname) AS hostname,
                  COALESCE(e.last_seen_at, m.last_seen_at) AS last_seen_at
           FROM topology_nodes n
           LEFT JOIN entities e
             ON n.reference_type='entity' AND e.id=n.reference_id
           LEFT JOIN physical_devices d
             ON n.reference_type='physical' AND d.id=n.reference_id
           LEFT JOIN entities m ON m.id=d.monitor_entity_id
           WHERE n.hidden=0 ORDER BY n.created_at"""
    )
    metrics = entity_metrics(database)
    for node in nodes:
        node["manual_position"] = bool(node["manual_position"])
        node["collapsed"] = bool(node["collapsed"])
        node["metadata"] = json.loads(node.pop("metadata_json") or "{}")
        node["metrics"] = metrics.get(node["reference_id"], {}) if node["reference_type"] == "entity" else {}
    relations = database.fetch_all("SELECT * FROM topology_relations ORDER BY source,created_at")
    for relation in relations:
        relation["locked"] = bool(relation["locked"])
        relation["metadata"] = json.loads(relation.pop("metadata_json") or "{}")
    return {"nodes": nodes, "relations": relations}


def entity_metrics(database: Database) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    records = database.fetch_all(
        """SELECT pr.entity_id,pr.raw_json,p.name AS provider_name,p.type AS provider_type
           FROM provider_records pr JOIN providers p ON p.id=pr.provider_id
           WHERE pr.entity_id IS NOT NULL ORDER BY pr.last_seen_at"""
    )
    for record in records:
        entity_id = record["entity_id"]
        raw = json.loads(record["raw_json"] or "{}")
        metrics = result.setdefault(entity_id, {"sources": []})
        if record["provider_name"] not in metrics["sources"]:
            metrics["sources"].append(record["provider_name"])
        if record["provider_type"] == "proxmox":
            metrics.update(
                cpu_percent=round(float(raw.get("cpu", 0)) * 100, 1) if raw.get("cpu") is not None else None,
                memory_used=raw.get("mem"), memory_total=raw.get("maxmem"),
                disk_used=raw.get("disk"), disk_total=raw.get("maxdisk"), uptime_seconds=raw.get("uptime"),
            )
        elif record["provider_type"] == "glances":
            quick = raw.get("quicklook") or {}
            memory = raw.get("memory") or {}
            metrics.update(
                cpu_percent=quick.get("cpu"), memory_used=memory.get("used"), memory_total=memory.get("total"),
                uptime_seconds=(raw.get("system") or {}).get("uptime"),
            )
        elif record["provider_type"] == "uptime_kuma":
            heartbeat = raw.get("heartbeat") or {}
            metrics.update(latency_ms=heartbeat.get("ping"), message=heartbeat.get("msg"))
    return result


def create_group(database: Database, label: str, subtitle: str = "") -> dict[str, Any]:
    node_id = f"group:{uuid.uuid4()}"
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO topology_nodes
               (id,reference_type,reference_id,label,subtitle,node_type,x,y,width,height,manual_position,metadata_json,created_at,updated_at)
               VALUES(?,?,?,?,?,'group',80,460,420,220,1,'{}',?,?)""",
            (node_id, "group", node_id.split(":", 1)[1], label, subtitle, now, now),
        )
    return database.fetch_one("SELECT * FROM topology_nodes WHERE id=?", (node_id,))


def create_relation(database: Database, from_node_id: str, to_node_id: str, relation_type: str, label: str) -> dict[str, Any]:
    if from_node_id == to_node_id:
        raise ValueError("Een node kan niet met zichzelf worden verbonden")
    relation_id = f"manual:{uuid.uuid4()}"
    now = utcnow()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO topology_relations
               (id,from_node_id,to_node_id,relation_type,label,source,locked,created_at,updated_at)
               VALUES(?,?,?,?,?,'manual',1,?,?)""",
            (relation_id, from_node_id, to_node_id, relation_type, label, now, now),
        )
    return database.fetch_one("SELECT * FROM topology_relations WHERE id=?", (relation_id,))


def capture_topology(database: Database, action: str, actor_user_id: str | None) -> None:
    snapshot = {
        "nodes": database.fetch_all("SELECT * FROM topology_nodes"),
        "relations": database.fetch_all("SELECT * FROM topology_relations WHERE source='manual'"),
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO topology_history(id,action,snapshot_json,actor_user_id,created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), action, json.dumps(snapshot), actor_user_id, utcnow()),
        )
        connection.execute(
            """DELETE FROM topology_history WHERE id IN (
                 SELECT id FROM topology_history ORDER BY rowid DESC LIMIT -1 OFFSET 50
               )"""
        )


def undo_topology(database: Database) -> str | None:
    history = database.fetch_one("SELECT * FROM topology_history ORDER BY rowid DESC LIMIT 1")
    if not history:
        return None
    snapshot = json.loads(history["snapshot_json"])
    with database.transaction() as connection:
        connection.execute("DELETE FROM topology_relations WHERE source='manual'")
        connection.execute("DELETE FROM topology_nodes WHERE reference_type='group'")
        for node in snapshot.get("nodes", []):
            if node["reference_type"] == "group":
                columns = [key for key in node if key != "parent_node_id"]
                connection.execute(
                    f"INSERT OR REPLACE INTO topology_nodes ({','.join(columns)},parent_node_id) VALUES ({','.join('?' for _ in columns)},NULL)",
                    tuple(node[column] for column in columns),
                )
            else:
                connection.execute(
                    """UPDATE topology_nodes SET label=?,subtitle=?,parent_node_id=NULL,parent_source=?,lifecycle=?,
                       x=?,y=?,width=?,height=?,manual_position=?,collapsed=?,hidden=?,metadata_json=?,updated_at=? WHERE id=?""",
                    (node["label"], node["subtitle"], node["parent_source"], node["lifecycle"], node["x"], node["y"],
                     node["width"], node["height"], node["manual_position"], node["collapsed"], node["hidden"],
                     node["metadata_json"], utcnow(), node["id"]),
                )
        for node in snapshot.get("nodes", []):
            if node.get("parent_node_id"):
                connection.execute("UPDATE topology_nodes SET parent_node_id=? WHERE id=?", (node["parent_node_id"], node["id"]))
        for relation in snapshot.get("relations", []):
            columns = list(relation)
            connection.execute(
                f"INSERT OR IGNORE INTO topology_relations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(relation[column] for column in columns),
            )
        connection.execute("DELETE FROM topology_history WHERE id=?", (history["id"],))
    return history["action"]
