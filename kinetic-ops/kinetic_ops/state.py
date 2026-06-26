"""Persist non-secret session state in the working directory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

STATE_FILE = Path.cwd() / ".kinetic_ops_state.json"


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(updates: dict[str, Any]) -> dict[str, Any]:
    state = load_state()
    state.update(updates)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def touch_log_sync(summary: dict[str, Any]) -> None:
    save_state({
        "last_log_sync_at": datetime.now(timezone.utc).isoformat(),
        "log_sync_summary": summary,
    })


def touch_accounts_update(accounts_count: int, config_hash: str) -> None:
    save_state({
        "last_accounts_update_at": datetime.now(timezone.utc).isoformat(),
        "last_accounts_count": accounts_count,
        "last_accounts_config_hash": config_hash,
    })


def get_last_sync_at() -> Optional[str]:
    return load_state().get("last_log_sync_at")


def get_last_accounts_update_at() -> Optional[str]:
    return load_state().get("last_accounts_update_at")
