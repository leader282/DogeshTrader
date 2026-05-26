"""Persistence for Kite access tokens.

Two locations are written:
* ``data/kite_token.json`` - convenient for shell scripts and the kite SDK.
* SQLite ``kite_tokens`` table - for audit and discovery.

The JSON file is chmod 0600 if the filesystem supports it. Both stores are in
the project's ignored ``data/`` directory by default.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from dogeshtrader.constants import TOKEN_STORE_FILE
from dogeshtrader.core.errors import AuthError
from dogeshtrader.storage import Database, TokenRepository


class TokenStore:
    def __init__(self, *, data_dir: Path | str, db: Database) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.data_dir / TOKEN_STORE_FILE
        self.repo = TokenRepository(db)

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, payload: dict[str, Any], *, api_key: str) -> None:
        access_token = payload.get("access_token")
        if not access_token:
            raise AuthError("payload has no access_token")
        merged = {
            "api_key": api_key,
            "access_token": access_token,
            "user_id": payload.get("user_id"),
            "user_name": payload.get("user_name"),
            "login_time": payload.get("login_time") or datetime.now().isoformat(),
            "raw": payload,
        }
        with self.json_path.open("w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, default=str)
        try:
            os.chmod(self.json_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        self.repo.save(
            api_key,
            access_token,
            user_id=merged["user_id"],
            user_name=merged["user_name"],
            login_time=merged["login_time"],
            raw=payload,
        )

    def load(self, api_key: str | None = None) -> dict[str, Any] | None:
        if api_key:
            row = self.repo.get(api_key)
            if row:
                return row
        if self.json_path.exists():
            try:
                return json.loads(self.json_path.read_text())
            except json.JSONDecodeError:
                return None
        return None

    def access_token(self, api_key: str) -> str | None:
        row = self.repo.get(api_key)
        if row and row.get("access_token"):
            return row["access_token"]
        if self.json_path.exists():
            data = json.loads(self.json_path.read_text())
            if data.get("api_key") == api_key:
                return data.get("access_token")
        return None

    def clear(self, api_key: str) -> None:
        self.repo.clear(api_key)
        try:
            self.json_path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["TokenStore"]
