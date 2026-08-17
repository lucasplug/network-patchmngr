from __future__ import annotations

import csv
import os
from functools import lru_cache
from pathlib import Path


def _oui_path() -> Path:
    # In de Docker-image staat het IEEE-bestand op /app/oui.csv (build-time
    # gedownload). Lokaal/tests kunnen een eigen pad meegeven; ontbreekt het
    # bestand, dan is vendorherkenning gewoon uit.
    return Path(os.getenv("PATCH_OUI_FILE", "/app/oui.csv"))


@lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    path = _oui_path()
    if not path.is_file():
        return {}
    table: dict[str, str] = {}
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            for row in reader:
                # IEEE oui.csv: Registry, Assignment (6 hex), Organization Name, Address
                if len(row) >= 3 and len(row[1]) == 6:
                    try:
                        int(row[1], 16)
                    except ValueError:
                        continue
                    table[row[1].upper()] = row[2].strip()
    except OSError:
        return {}
    return table


def vendor_for_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    prefix = mac.replace(":", "").replace("-", "").upper()[:6]
    if len(prefix) != 6:
        return None
    return _table().get(prefix) or None


def reset_cache() -> None:
    """Testhulp: herlaad het OUI-bestand bij de volgende lookup."""
    _table.cache_clear()
