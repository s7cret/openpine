"""AccountManager for OpenPine accounts schema."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from openpine.accounts.models import Account, AccountType, generate_id

if TYPE_CHECKING:
    from openpine.storage.sqlite_storage import SQLiteStorage


class AccountManager:
    """CRUD operations for TZ 30.4 accounts.

    The public schema uses account_id/provider/market_type/mode and stores
    secret references only. Legacy id/account_type/api_key_hash fields remain
    readable for current execution/risk callers.
    """

    def __init__(self, storage: "SQLiteStorage") -> None:
        self.storage = storage

    def create_account(
        self,
        name: str,
        account_type: AccountType | None = None,
        exchange: str = "",
        provider: str = "unknown",
        market_type: str = "spot",
        mode: AccountType | None = None,
        api_key_ref: str | None = None,
        api_secret_ref: str | None = None,
        permissions: str | None = None,
        api_key_hash: str | None = None,
        live_enabled: bool = False,
        config: dict | None = None,
    ) -> Account:
        """Create an account. live_enabled defaults to False."""
        now = int(time.time() * 1000)
        account_id = generate_id("acct")
        resolved_mode = mode or account_type or AccountType.PAPER
        config_json = json.dumps(config or {}, sort_keys=True)

        self.storage.execute(
            """
            INSERT INTO accounts
              (account_id, id, name, provider, exchange, market_type, mode, account_type,
               api_key_ref, api_secret_ref, permissions, api_key_hash, live_enabled,
               config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                account_id,
                name,
                provider,
                exchange,
                market_type,
                resolved_mode.value,
                resolved_mode.value,
                api_key_ref,
                api_secret_ref,
                permissions,
                api_key_hash,
                int(live_enabled),
                config_json,
                now,
                now,
            ),
        )
        self.storage.commit()

        return Account(
            account_id=account_id,
            name=name,
            provider=provider,
            exchange=exchange,
            market_type=market_type,
            mode=resolved_mode,
            live_enabled=live_enabled,
            api_key_ref=api_key_ref,
            api_secret_ref=api_secret_ref,
            permissions=permissions,
            api_key_hash=api_key_hash,
            config=config or {},
            created_at=now,
            updated_at=now,
        )

    def get_account(self, account_id: str) -> Account | None:
        """Get account by account_id or legacy id."""
        cursor = self.storage.execute(
            "SELECT * FROM accounts WHERE account_id = ? OR id = ?",
            (account_id, account_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_account(row)

    def list_accounts(self, account_type: AccountType | None = None) -> list[Account]:
        """List accounts, optionally filtered by execution mode."""
        if account_type is not None:
            cursor = self.storage.execute(
                "SELECT * FROM accounts WHERE mode = ? OR account_type = ? ORDER BY created_at DESC",
                (account_type.value, account_type.value),
            )
        else:
            cursor = self.storage.execute("SELECT * FROM accounts ORDER BY created_at DESC")
        return [self._row_to_account(row) for row in cursor.fetchall()]

    def list_accounts_by_provider(self, provider: str, exchange: str | None = None) -> list[Account]:
        """List accounts by provider and optional exchange."""
        if exchange is None:
            cursor = self.storage.execute(
                "SELECT * FROM accounts WHERE provider = ? ORDER BY created_at DESC",
                (provider,),
            )
        else:
            cursor = self.storage.execute(
                "SELECT * FROM accounts WHERE provider = ? AND exchange = ? ORDER BY created_at DESC",
                (provider, exchange),
            )
        return [self._row_to_account(row) for row in cursor.fetchall()]

    def set_live_enabled(self, account_id: str, enabled: bool) -> None:
        """Enable or disable live trading for account."""
        now = int(time.time() * 1000)
        self.storage.execute(
            "UPDATE accounts SET live_enabled = ?, updated_at = ? WHERE account_id = ? OR id = ?",
            (int(enabled), now, account_id, account_id),
        )
        self.storage.commit()

    def delete_account(self, account_id: str) -> None:
        """Delete an account."""
        self.storage.execute(
            "DELETE FROM accounts WHERE account_id = ? OR id = ?",
            (account_id, account_id),
        )
        self.storage.commit()

    def _row_to_account(self, row: tuple[Any, ...]) -> Account:
        """Convert database row to Account object using current column names."""
        cursor = self.storage.execute("PRAGMA table_info(accounts)")
        columns = [col[1] for col in cursor.fetchall()]
        values = dict(zip(columns, row, strict=False))
        mode = values.get("mode") or values.get("account_type") or AccountType.PAPER.value

        return Account(
            account_id=values.get("account_id") or values.get("id"),
            name=values.get("name") or "",
            provider=values.get("provider") or "unknown",
            exchange=values.get("exchange") or "",
            market_type=values.get("market_type") or "spot",
            mode=mode,
            live_enabled=bool(values.get("live_enabled")),
            api_key_ref=values.get("api_key_ref"),
            api_secret_ref=values.get("api_secret_ref"),
            permissions=values.get("permissions"),
            api_key_hash=values.get("api_key_hash"),
            config=json.loads(values.get("config") or "{}"),
            created_at=values.get("created_at") or 0,
            updated_at=values.get("updated_at") or 0,
        )
