from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .db import Database, utcnow


class SecretStore:
    """Encrypt provider credentials with a machine key kept outside SQLite."""

    def __init__(self, database: Database, key_path: Path):
        self.database = database
        self.key_path = key_path
        self._fernet = Fernet(self._load_or_create_key())

    def reload(self) -> None:
        """Reload the key after a portable backup has been restored."""
        self._fernet = Fernet(self.key_path.read_bytes().strip())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            Fernet(key)
            return key
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.key_path.read_bytes().strip()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key + b"\n")
        return key

    def get(self, provider_id: str) -> dict[str, str]:
        row = self.database.fetch_one(
            "SELECT encrypted_json FROM provider_secrets WHERE provider_id=?", (provider_id,)
        )
        if not row:
            return {}
        try:
            payload: Any = json.loads(self._fernet.decrypt(row["encrypted_json"].encode()).decode())
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Opgeslagen providergegevens kunnen niet worden ontsleuteld") from exc
        return {str(key): str(value) for key, value in payload.items() if value is not None}

    def update(
        self,
        provider_id: str,
        values: dict[str, str | None],
        clear: list[str] | None = None,
    ) -> dict[str, bool]:
        current = self.get(provider_id)
        for key in clear or []:
            current.pop(key, None)
        for key, value in values.items():
            if value is not None and value.strip():
                current[key] = value.strip()
        with self.database.transaction() as connection:
            if current:
                encrypted = self._fernet.encrypt(
                    json.dumps(current, separators=(",", ":"), sort_keys=True).encode()
                ).decode()
                connection.execute(
                    """INSERT INTO provider_secrets(provider_id,encrypted_json,updated_at)
                       VALUES(?,?,?) ON CONFLICT(provider_id) DO UPDATE SET
                       encrypted_json=excluded.encrypted_json,updated_at=excluded.updated_at""",
                    (provider_id, encrypted, utcnow()),
                )
            else:
                connection.execute("DELETE FROM provider_secrets WHERE provider_id=?", (provider_id,))
        return {key: True for key in current}
