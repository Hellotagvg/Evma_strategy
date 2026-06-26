"""
Continuous exchange monitoring for positions and open orders.
Polls enabled accounts via ccxt and exposes a cached snapshot for WebSocket broadcast.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core import exchange_ops
from backend.core.logger import log_sync

POLL_INTERVAL_SEC = 5
STALE_AFTER_SEC = 12
WATCHDOG_INTERVAL_SEC = 10


class ExchangeMonitor:
    def __init__(self) -> None:
        self._positions: List[dict] = []
        self._open_orders: List[dict] = []
        self._position_errors: Dict[str, str] = {}
        self._order_errors: Dict[str, str] = {}
        self._last_success_at: Optional[datetime] = None
        self._last_poll_at: Optional[datetime] = None
        self._poll_lock = asyncio.Lock()

    async def poll_once(self, accounts: List[dict]) -> None:
        if self._poll_lock.locked():
            return

        async with self._poll_lock:
            self._last_poll_at = datetime.now(timezone.utc)
            enabled = [acc for acc in accounts if acc.get("enabled", True)]
            if not enabled:
                self._positions = []
                self._open_orders = []
                self._position_errors = {}
                self._order_errors = {}
                self._last_success_at = datetime.now(timezone.utc)
                return

            tasks = [self._fetch_account(acc) for acc in enabled]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            positions: List[dict] = []
            open_orders: List[dict] = []
            position_errors: Dict[str, str] = {}
            order_errors: Dict[str, str] = {}
            any_success = False

            for result in results:
                if isinstance(result, Exception):
                    log_sync(f"Exchange monitor account task failed: {result}", level="ERROR")
                    continue

                account_id, acc_positions, acc_orders, pos_err, ord_err = result
                if pos_err:
                    position_errors[account_id] = pos_err
                else:
                    any_success = True
                    positions.extend(acc_positions)

                if ord_err:
                    order_errors[account_id] = ord_err
                else:
                    any_success = True
                    open_orders.extend(acc_orders)

            self._positions = positions
            self._open_orders = open_orders
            self._position_errors = position_errors
            self._order_errors = order_errors
            if any_success or (not position_errors and not order_errors):
                self._last_success_at = datetime.now(timezone.utc)

    async def _fetch_account(self, account: dict):
        account_id = str(account.get("id") or "unknown")
        pos_err: Optional[str] = None
        ord_err: Optional[str] = None
        positions: List[dict] = []
        orders: List[dict] = []

        try:
            positions = await exchange_ops.fetch_positions(account)
        except Exception as exc:
            pos_err = exchange_ops._format_error(exc)

        try:
            orders = await exchange_ops.fetch_open_orders(account)
        except Exception as exc:
            ord_err = exchange_ops._format_error(exc)

        return account_id, positions, orders, pos_err, ord_err

    def is_stale(self) -> bool:
        if not self._last_success_at:
            return True
        age = (datetime.now(timezone.utc) - self._last_success_at).total_seconds()
        return age > STALE_AFTER_SEC

    def get_snapshot(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        fetched_at = self._last_success_at.isoformat() if self._last_success_at else None
        poll_age_sec = None
        if self._last_poll_at:
            poll_age_sec = int((now - self._last_poll_at).total_seconds())

        return {
            "fetched_at": fetched_at,
            "positions": self._positions,
            "open_orders": self._open_orders,
            "position_errors": self._position_errors,
            "order_errors": self._order_errors,
            "stale": self.is_stale(),
            "poll_age_sec": poll_age_sec,
        }


exchange_monitor = ExchangeMonitor()


async def exchange_monitor_loop(load_accounts_fn) -> None:
    while True:
        try:
            await exchange_monitor.poll_once(load_accounts_fn())
        except Exception as exc:
            log_sync(f"Exchange monitor poll error: {exc}", level="ERROR")
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def exchange_monitor_watchdog_loop(load_accounts_fn) -> None:
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
        if not exchange_monitor.is_stale():
            continue
        try:
            log_sync("Exchange monitor data stale — forcing refresh.", level="WARNING")
            await exchange_monitor.poll_once(load_accounts_fn())
        except Exception as exc:
            log_sync(f"Exchange monitor watchdog refresh failed: {exc}", level="ERROR")
