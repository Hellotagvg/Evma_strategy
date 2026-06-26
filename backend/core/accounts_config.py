"""
Validate, persist, and hot-reload accounts.json for the execution engine.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Set

from backend.core.logger import log_sync

VALID_EXCHANGES = frozenset({"bybit", "hyperliquid"})
VALID_RISK_MODES = frozenset({"fixed_pct", "fixed_usd", "adjustable_pct", "adjustable_usd"})


def sync_risk_config(acc: dict) -> None:
    risk = acc.get("risk")
    if not isinstance(risk, dict):
        return
    acc["risk_config"] = {
        "type": risk.get("mode", risk.get("type")),
        "value": risk.get("value"),
        "min": risk.get("min"),
        "max": risk.get("max"),
        "step": risk.get("step"),
        "default": risk.get("default"),
    }


def normalize_accounts(accounts: List[dict]) -> List[dict]:
    for acc in accounts:
        sync_risk_config(acc)
    return accounts


def _validate_risk(acc: dict, account_id: str) -> List[str]:
    errors: List[str] = []
    risk = acc.get("risk")
    if not isinstance(risk, dict):
        errors.append(f"{account_id}: missing or invalid 'risk' object")
        return errors

    mode = risk.get("mode", risk.get("type"))
    if mode not in VALID_RISK_MODES:
        errors.append(f"{account_id}: risk.mode must be one of {sorted(VALID_RISK_MODES)}")
        return errors

    if mode in ("fixed_pct", "fixed_usd"):
        if risk.get("value") is None:
            errors.append(f"{account_id}: risk.value is required for {mode}")
    elif mode in ("adjustable_pct", "adjustable_usd"):
        for field in ("min", "max", "step"):
            if risk.get(field) is None:
                errors.append(f"{account_id}: risk.{field} is required for {mode}")

    return errors


def validate_account(acc: dict) -> List[str]:
    errors: List[str] = []
    account_id = str(acc.get("id") or "").strip()
    if not account_id:
        return ["Each account must have a non-empty 'id'"]

    exchange = str(acc.get("exchange") or "").strip().lower()
    if exchange not in VALID_EXCHANGES:
        errors.append(f"{account_id}: exchange must be one of {sorted(VALID_EXCHANGES)}")

    credentials = acc.get("credentials")
    if not isinstance(credentials, dict):
        errors.append(f"{account_id}: missing or invalid 'credentials' object")
    elif exchange == "bybit":
        if not str(credentials.get("api_key") or "").strip():
            errors.append(f"{account_id}: credentials.api_key is required for Bybit")
        if not str(credentials.get("api_secret") or credentials.get("secret") or "").strip():
            errors.append(f"{account_id}: credentials.api_secret is required for Bybit")
    elif exchange == "hyperliquid":
        wallet = credentials.get("wallet_address") or credentials.get("api_key")
        secret = credentials.get("api_secret") or credentials.get("secret")
        if not str(wallet or "").strip():
            errors.append(f"{account_id}: credentials.wallet_address is required for Hyperliquid")
        if not str(secret or "").strip():
            errors.append(f"{account_id}: credentials.api_secret is required for Hyperliquid")

    errors.extend(_validate_risk(acc, account_id))

    max_positions = acc.get("max_positions", 3)
    try:
        if int(max_positions) < 1:
            errors.append(f"{account_id}: max_positions must be >= 1")
    except (TypeError, ValueError):
        errors.append(f"{account_id}: max_positions must be an integer")

    return errors


def validate_accounts(accounts: List[dict]) -> List[str]:
    if not isinstance(accounts, list):
        return ["'accounts' must be a list"]

    errors: List[str] = []
    seen_ids: Set[str] = set()
    for acc in accounts:
        if not isinstance(acc, dict):
            errors.append("Each account entry must be an object")
            continue
        account_id = str(acc.get("id") or "").strip()
        errors.extend(validate_account(acc))
        if account_id:
            if account_id in seen_ids:
                errors.append(f"Duplicate account id: {account_id}")
            seen_ids.add(account_id)
    return errors


def merge_account(existing: dict, patch: dict) -> dict:
    merged = deepcopy(existing)
    for key, value in patch.items():
        if value is None:
            continue
        if key == "credentials" and isinstance(value, dict):
            creds = dict(merged.get("credentials") or {})
            creds.update(value)
            merged["credentials"] = creds
        elif key == "risk" and isinstance(value, dict):
            risk = dict(merged.get("risk") or {})
            risk.update(value)
            merged["risk"] = risk
        elif key == "fixed_tp_rr_by_coin" and isinstance(value, dict):
            merged["fixed_tp_rr_by_coin"] = dict(value)
        else:
            merged[key] = value
    sync_risk_config(merged)
    return merged


def accounts_for_persist(accounts: List[dict]) -> List[dict]:
    formatted: List[dict] = []
    for acc in accounts:
        cleaned = deepcopy(acc)
        if "risk_config" in cleaned:
            risk_cfg = cleaned.pop("risk_config")
            cleaned["risk"] = {
                "mode": risk_cfg.get("type"),
                "value": risk_cfg.get("value"),
                "min": risk_cfg.get("min"),
                "max": risk_cfg.get("max"),
                "step": risk_cfg.get("step"),
                "default": risk_cfg.get("default"),
            }
        formatted.append(cleaned)
    return formatted


def persist_accounts(accounts: List[dict], path: str) -> None:
    payload = {"accounts": accounts_for_persist(accounts)}
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, path)


def check_removals_safe(removed_ids: Set[str], active_account_ids: Set[str]) -> List[str]:
    blocked = sorted(removed_ids & active_account_ids)
    if not blocked:
        return []
    return [
        "Cannot remove or rename accounts with active managed orders: "
        + ", ".join(blocked)
    ]


def apply_accounts_runtime(accounts: List[dict], execution_engine) -> None:
    normalize_accounts(accounts)
    execution_engine.set_accounts_config(accounts)


def update_accounts_config(
    accounts: List[dict],
    *,
    path: str,
    execution_engine,
    previous_accounts: Optional[List[dict]] = None,
) -> List[str]:
    errors = validate_accounts(accounts)
    if errors:
        return errors

    previous = previous_accounts if previous_accounts is not None else []
    old_ids = {str(a.get("id")) for a in previous if a.get("id")}
    new_ids = {str(a.get("id")) for a in accounts if a.get("id")}
    active_ids = {o.account_id for o in execution_engine.active_orders}
    errors.extend(check_removals_safe(old_ids - new_ids, active_ids))
    if errors:
        return errors

    try:
        persist_accounts(accounts, path)
    except OSError as e:
        log_sync(f"Failed writing accounts config at {path}: {e}", level="ERROR")
        return [f"Failed to persist accounts configuration: {e}"]

    apply_accounts_runtime(accounts, execution_engine)
    return []
