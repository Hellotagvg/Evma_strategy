"""Validate and push accounts.json to the backend."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from kinetic_ops.client import KineticClient
from kinetic_ops.state import touch_accounts_update

VALID_EXCHANGES = frozenset({"bybit", "hyperliquid"})
VALID_RISK_MODES = frozenset({"fixed_pct", "fixed_usd", "adjustable_pct", "adjustable_usd"})
EXIT_BLOCKED = 2


def load_accounts_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "accounts" in data:
        return data["accounts"]
    if isinstance(data, list):
        return data
    raise ValueError("File must contain {\"accounts\": [...]} or a top-level array")


def validate_accounts(accounts: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()

    for acc in accounts:
        account_id = str(acc.get("id") or "").strip()
        if not account_id:
            errors.append("Each account must have a non-empty 'id'")
            continue
        if account_id in seen_ids:
            errors.append(f"Duplicate account id: {account_id}")
        seen_ids.add(account_id)

        exchange = str(acc.get("exchange") or "").strip().lower()
        if exchange not in VALID_EXCHANGES:
            errors.append(f"{account_id}: exchange must be one of {sorted(VALID_EXCHANGES)}")

        credentials = acc.get("credentials")
        if not isinstance(credentials, dict):
            errors.append(f"{account_id}: missing or invalid 'credentials' object")
        elif exchange == "bybit":
            if not str(credentials.get("api_key") or "").strip():
                errors.append(f"{account_id}: credentials.api_key is required for Bybit")
            secret = credentials.get("api_secret") or credentials.get("secret")
            if not str(secret or "").strip():
                errors.append(f"{account_id}: credentials.api_secret is required for Bybit")

        risk = acc.get("risk")
        if not isinstance(risk, dict):
            errors.append(f"{account_id}: missing or invalid 'risk' object")
        else:
            mode = risk.get("mode", risk.get("type"))
            if mode not in VALID_RISK_MODES:
                errors.append(f"{account_id}: risk.mode must be one of {sorted(VALID_RISK_MODES)}")
            elif mode in ("fixed_pct", "fixed_usd") and risk.get("value") is None:
                errors.append(f"{account_id}: risk.value is required for {mode}")

    return errors


def config_hash(accounts: list[dict[str, Any]]) -> str:
    payload = json.dumps(accounts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def mask_secret(value: str) -> str:
    if not value:
        return "—"
    if len(value) <= 6:
        return "****"
    return f"****…{value[-3:]}"


def summarize_diff(local: list[dict], remote: list[dict]) -> dict[str, Any]:
    local_ids = {a.get("id") for a in local if a.get("id")}
    remote_ids = {a.get("id") for a in remote if a.get("id")}
    added = sorted(local_ids - remote_ids)
    removed = sorted(remote_ids - local_ids)
    changed: list[str] = []
    remote_by_id = {a["id"]: a for a in remote if a.get("id")}
    for acc in local:
        aid = acc.get("id")
        if not aid or aid not in remote_by_id:
            continue
        if json.dumps(acc, sort_keys=True) != json.dumps(remote_by_id[aid], sort_keys=True):
            changed.append(aid)
    return {"added": added, "removed": removed, "changed": sorted(changed)}


def check_active_trades(client: KineticClient) -> tuple[int, list[dict]]:
    status = client.get_execution_status()
    count = int(status.get("active_managed_orders", 0))
    orders = status.get("active_orders") or []
    return count, orders


def format_active_orders(orders: list[dict]) -> str:
    if not orders:
        return ""
    parts = []
    for o in orders:
        parts.append(
            f"{o.get('account_id', '?')} ({o.get('coin', '?')}) order {o.get('order_id', '?')}"
        )
    return "; ".join(parts)


def apply_accounts_config(
    client: KineticClient,
    accounts: list[dict[str, Any]],
    *,
    skip_safety_check: bool = False,
) -> dict[str, Any]:
    errors = validate_accounts(accounts)
    if errors:
        raise ValueError({"errors": errors})

    count, orders = check_active_trades(client)
    if count > 0 and not skip_safety_check:
        raise ActiveTradesBlocked(count, orders)

    result = client.put_accounts_config(accounts)
    touch_accounts_update(int(result.get("accounts_count", len(accounts))), config_hash(accounts))
    return result


class ActiveTradesBlocked(Exception):
    def __init__(self, count: int, orders: list[dict]) -> None:
        self.count = count
        self.orders = orders
        super().__init__(f"{count} active managed trade(s)")
