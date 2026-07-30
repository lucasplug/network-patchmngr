from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from contextlib import suppress
from typing import Any

from .db import Database, utcnow


logger = logging.getLogger(__name__)


class SpeedtestManager:
    def __init__(self, database: Database):
        self.database = database
        self.lock = asyncio.Lock()

    def payload(self) -> dict[str, Any]:
        settings = self.database.fetch_one("SELECT * FROM speedtest_settings WHERE id=1") or {}
        if settings:
            settings["enabled"] = bool(settings["enabled"])
            settings["telemetry_enabled"] = bool(settings["telemetry_enabled"])
        latest = self.database.fetch_one(
            "SELECT * FROM speedtest_runs WHERE status='success' ORDER BY completed_at DESC LIMIT 1"
        )
        running = self.database.fetch_one(
            "SELECT id,started_at FROM speedtest_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1"
        )
        history = self.database.fetch_all(
            """SELECT id,download_mbps,upload_mbps,ping_ms,jitter_ms,server_name,started_at,completed_at
               FROM speedtest_runs WHERE status='success' ORDER BY completed_at DESC LIMIT 120"""
        )
        return {"settings": settings, "latest": latest, "running": running, "history": history}

    async def run(self) -> dict[str, Any]:
        if self.lock.locked():
            return {"status": "busy"}
        async with self.lock:
            settings = self.database.fetch_one("SELECT * FROM speedtest_settings WHERE id=1") or {}
            run_id = str(uuid.uuid4())
            started = utcnow()
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO speedtest_runs(id,status,started_at) VALUES(?,'running',?)",
                    (run_id, started),
                )
                connection.execute("UPDATE speedtest_settings SET last_run_at=?,last_error=NULL WHERE id=1", (started,))
            binary = os.getenv("LIBRESPEED_CLI", "librespeed-cli")
            executable = shutil.which(binary) if "/" not in binary else binary
            if not executable:
                return self._fail(run_id, "librespeed-cli is niet geïnstalleerd; bouw en start de Docker-container")
            command = [
                executable, "--json", "--secure", "--no-icmp", "--telemetry-level", "disabled",
                "--duration", str(settings.get("duration_seconds") or 10), "--timeout", "30",
            ]
            if settings.get("server_id"):
                command += ["--server", str(settings["server_id"])]
            if settings.get("interface_name"):
                command += ["--interface", str(settings["interface_name"])]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=150)
                if process.returncode != 0:
                    return self._fail(run_id, stderr.decode(errors="replace").strip() or "Speedtest mislukt")
                raw_output = json.loads(stdout.decode())
                if isinstance(raw_output, list):
                    if not raw_output:
                        raise ValueError("LibreSpeed gaf geen serverresultaat terug")
                    raw = raw_output[0]
                elif isinstance(raw_output, dict):
                    raw = raw_output
                else:
                    raise ValueError("LibreSpeed gaf geen JSON-object terug")
                parsed = parse_result(raw)
                completed = utcnow()
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE speedtest_runs SET status='success',download_mbps=?,upload_mbps=?,ping_ms=?,
                           jitter_ms=?,server_name=?,server_id=?,isp=?,public_ip=?,raw_json=?,completed_at=? WHERE id=?""",
                        (parsed["download_mbps"], parsed["upload_mbps"], parsed["ping_ms"], parsed["jitter_ms"],
                         parsed["server_name"], parsed["server_id"], parsed["isp"], parsed["public_ip"],
                         json.dumps(raw_output), completed, run_id),
                    )
                    connection.execute("UPDATE speedtest_settings SET last_error=NULL,updated_at=? WHERE id=1", (completed,))
                return {"status": "success", "id": run_id, **parsed}
            except (TimeoutError, asyncio.TimeoutError):
                # wait_for annuleert alleen het wachten; het CLI-proces zelf
                # moet expliciet worden gestopt om lekken te voorkomen.
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                return self._fail(run_id, "Speedtest heeft de tijdslimiet overschreden")
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                return self._fail(run_id, f"Onverwacht LibreSpeed-resultaat: {exc}")
            except (OSError, TypeError) as exc:
                return self._fail(run_id, f"Speedtest kon niet worden uitgevoerd: {exc}")

    def _fail(self, run_id: str, message: str) -> dict[str, Any]:
        message = message[:1000]
        logger.warning("Speedtest %s mislukt: %s", run_id, message)
        completed = utcnow()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE speedtest_runs SET status='failed',error=?,completed_at=? WHERE id=?",
                (message, completed, run_id),
            )
            connection.execute("UPDATE speedtest_settings SET last_error=?,updated_at=? WHERE id=1", (message, completed))
        return {"status": "failed", "error": message}


def parse_result(raw: dict[str, Any]) -> dict[str, Any]:
    server = raw.get("server") or raw.get("serverInfo") or {}
    client = raw.get("client") or {}
    isp = raw.get("isp") or raw.get("ispinfo") or raw.get("ispInfo") or ""
    if not isp and isinstance(client, dict):
        isp = client.get("org") or client.get("organization") or ""
    if isinstance(isp, dict):
        isp = isp.get("name") or isp.get("isp") or ""

    def number(*keys: str) -> float | None:
        for key in keys:
            value = raw.get(key)
            if value is not None:
                return float(value)
        return None

    download = number("download", "downloadSpeed", "download_speed")
    upload = number("upload", "uploadSpeed", "upload_speed")
    # LibreSpeed JSON documents speeds in bit/s. Some compatible runners emit Mbps.
    def mbps(value: float | None) -> float | None:
        if value is None:
            return None
        return round(value / 1_000_000 if value > 100_000 else value, 2)

    return {
        "download_mbps": mbps(download),
        "upload_mbps": mbps(upload),
        "ping_ms": number("ping", "ping_ms"),
        "jitter_ms": number("jitter", "jitter_ms"),
        "server_name": server.get("name") if isinstance(server, dict) else str(server),
        "server_id": str(server.get("id") or server.get("url") or "") if isinstance(server, dict) else "",
        "isp": str(isp),
        "public_ip": str(raw.get("ip") or raw.get("clientIp") or (client.get("ip") if isinstance(client, dict) else "") or ""),
    }
