"""
Multi-Account Trading Execution Engine.
Manages limit order dragging on EVMA line, fill detection, take profit / stop loss execution,
and logs all actions to the SQLite logging and journaling databases.
Includes support for both simulated mock trading and live ccxt-based Bybit Demo/Bybit Live/Hyperliquid.
"""

import asyncio
import math
import time
import os
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import ccxt.pro as ccxt  # Using ccxt for exchange communication
from backend.core.logger import log, log_sync
from backend.core.exchange_factory import ccxt_symbol
from backend.core.risk import RiskEngine, OrderParams
from backend.core.journal import open_trade, close_trade, update_trade


class ManagedOrderCancelError(Exception):
    """Raised when an exchange rejects or fails a managed order cancellation."""

@dataclass
class Intent:
    account_id: str
    coin: str
    direction: str
    rr: float
    risk_value: float
    session_id: str
    session_type: str

@dataclass
class ManagedOrder:
    order_id: str  # Generated unique engine ID
    exchange_order_id: Optional[str]  # ID on the exchange
    account_id: str
    coin: str
    direction: str
    current_entry: float
    current_sl: float
    current_tp: float
    current_qty: float
    status: str  # pending, dragging, filled, tp_hit, sl_hit, cancelled
    fill_price: Optional[float] = None
    fill_time: Optional[str] = None
    session_id: str = ""
    trade_journal_id: str = ""
    risk_value: float = 1.0
    rr: float = 2.0
    exchange_name: str = "bybit"
    is_simulated: bool = True
    tp_exchange_order_id: Optional[str] = None
    sl_exchange_order_id: Optional[str] = None
    entry_qty: float = 0.0
    filled_qty: float = 0.0
    protected_qty: float = 0.0
    partial_fill_detected: bool = False
    entry_order_active: bool = True
    exchange_missing_count: int = 0

class SimulatedExchange:
    def __init__(self, account_config: dict):
        self.account_id = account_config.get("id")
        self.exchange_name = account_config.get("exchange", "bybit")
        self.balance = float(account_config.get("risk_config", {}).get("default_balance", 10000.0))
        self.orders = {}
        self.positions = {}
        
    async def fetch_balance(self):
        return {"total": {"USDT": self.balance}, "free": {"USDT": self.balance}}

    async def create_order(self, symbol: str, type: str, side: str, amount: float, price: float = None, params=None):
        order_id = f"sim_ord_{int(time.time() * 1000)}"
        order = {
            "id": order_id,
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "timestamp": int(time.time() * 1000)
        }
        self.orders[order_id] = order
        return order

    async def cancel_order(self, order_id: str, symbol: str = None):
        if order_id in self.orders:
            self.orders[order_id]["status"] = "canceled"
            return self.orders[order_id]
        raise Exception(f"Order {order_id} not found on simulated exchange")

    async def edit_order(self, order_id: str, symbol: str, type: str, side: str, amount: float, price: float = None, params=None):
        if order_id in self.orders:
            self.orders[order_id]["price"] = price
            self.orders[order_id]["amount"] = amount
            return self.orders[order_id]
        raise Exception(f"Order {order_id} not found on simulated exchange to edit")

class ExecutionEngine:
    def __init__(self, session_manager, data_feed_manager):
        self.session_manager = session_manager
        self.data_feed_manager = data_feed_manager
        self.active_orders: List[ManagedOrder] = []
        self.exchanges = {}
        self.accounts_config = []
        self.exchange_call_timeout_sec = 15
        self.exchange_call_semaphore = asyncio.Semaphore(16)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(base_dir, "data")
        self.snapshot_path = os.path.join(self.data_dir, "active_orders_snapshot.json")
        self.snapshot_tmp_path = self.snapshot_path + ".tmp"
        self.snapshot_schema_version = 1
        self.snapshot_lock = asyncio.Lock()

    async def _exchange_call(self, coro):
        async with self.exchange_call_semaphore:
            return await asyncio.wait_for(coro, timeout=self.exchange_call_timeout_sec)

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _resolve_usdt_balance(self, balance_data: dict, default: float = 0.0) -> float:
        """
        Normalize different exchange balance payload shapes into a single USDT value.
        """
        candidates = []

        if isinstance(balance_data, dict):
            for key in ("free", "available", "total", "wallet", "info"):
                value = balance_data.get(key)
                if isinstance(value, dict):
                    for inner_key in ("USDT", "usdt", "USDC", "usdc"):
                        if value.get(inner_key) is not None:
                            candidates.append(self._safe_float(value.get(inner_key)))

            info = balance_data.get("info")
            if isinstance(info, dict):
                result = info.get("result")
                if isinstance(result, dict):
                    rows = result.get("list")
                    if isinstance(rows, list):
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            for key in (
                                "availableBalance",
                                "availableToWithdraw",
                                "walletBalance",
                                "totalAvailableBalance",
                                "equity",
                                "balance",
                            ):
                                if row.get(key) is not None:
                                    candidates.append(self._safe_float(row.get(key)))
                                    break

        candidates = [value for value in candidates if value > 0]
        if candidates:
            return max(candidates)
        return max(self._safe_float(default), 0.0)

    async def _fetch_account_usdt_balance(self, exchange, is_simulated: bool) -> float:
        try:
            balance_data = await self._exchange_call(exchange.fetch_balance())
            default_balance = 10000.0 if is_simulated else 0.0
            return self._resolve_usdt_balance(balance_data, default_balance)
        except Exception:
            return 10000.0 if is_simulated else 0.0

    def _build_entry_order_params(self, account_config: dict, order_params: OrderParams) -> dict:
        """
        Attach exchange-native protection where supported.
        Bybit demo/live can carry TP/SL on the entry order itself.
        """
        if account_config.get("exchange", "").lower() != "bybit":
            return {}
        return {
            "takeProfit": str(order_params.tp_price),
            "stopLoss": str(order_params.sl_price),
            "tpslMode": "Full",
            "tpOrderType": "Market",
            "slOrderType": "Market",
            "tpTriggerBy": "LastPrice",
            "slTriggerBy": "LastPrice",
        }

    def _uses_attached_entry_protection(self, account_config: dict) -> bool:
        return account_config.get("exchange", "").lower() == "bybit"

    async def _apply_exchange_leverage(self, account_config: dict, exchange, symbol: str, target_leverage: float):
        """
        Best-effort leverage sync for live exchanges.

        Bybit futures orders are margin-checked against the symbol leverage already set on the account.
        The risk engine computes a decimal leverage target, but the exchange usually expects whole steps,
        so we round up to avoid under-sizing the margin requirement.
        """
        if account_config.get("exchange", "").lower() != "bybit" or isinstance(exchange, SimulatedExchange):
            return

        try:
            max_leverage = float(account_config.get("max_leverage", 10.0))
        except (TypeError, ValueError):
            max_leverage = 10.0

        leverage_to_set = max(1, min(int(math.ceil(target_leverage)), int(math.ceil(max_leverage))))

        try:
            await self._exchange_call(exchange.load_markets())
            market = exchange.market(symbol)
            if not market or not market.get("linear", False):
                raise ValueError(f"{symbol} is not configured as a linear perpetual market.")
            await self._exchange_call(exchange.set_leverage(leverage_to_set, symbol, {"category": "linear"}))
            await log(
                "execution",
                f"Applied Bybit leverage {leverage_to_set}x for {symbol} on {account_config.get('id', 'unknown')} "
                f"(target {target_leverage:.2f}x).",
            )
        except Exception as lev_err:
            await log(
                "execution",
                f"Failed to apply Bybit leverage {leverage_to_set}x for {symbol} on {account_config.get('id', 'unknown')}: "
                f"{lev_err}",
                level="WARNING",
            )

        return leverage_to_set

    def _apply_margin_safety_cap(
        self,
        order_params: OrderParams,
        free_balance: float,
        leverage: float,
        account_id: str,
        symbol: str,
    ) -> OrderParams:
        """
        Shrink the notional if the live balance cannot support the planned size.
        """
        if free_balance <= 0 or leverage <= 0:
            return order_params

        safety_buffer = 0.95
        max_notional = free_balance * leverage * safety_buffer
        if order_params.position_size_usd <= max_notional:
            return order_params

        sl_distance_pct = abs(order_params.entry_price - order_params.sl_price) / order_params.entry_price
        if sl_distance_pct <= 0:
            return order_params

        adjusted_position_size_usd = max_notional
        adjusted_risk_amount_usd = adjusted_position_size_usd * sl_distance_pct
        adjusted_quantity = adjusted_position_size_usd / order_params.entry_price

        log_sync(
            f"ExecutionEngine: Capping live order for {account_id} {symbol} from "
            f"${order_params.position_size_usd:.2f} to ${adjusted_position_size_usd:.2f} "
            f"using free balance ${free_balance:.2f} and leverage {leverage:.2f}x."
        )

        return OrderParams(
            entry_price=order_params.entry_price,
            sl_price=order_params.sl_price,
            tp_price=order_params.tp_price,
            quantity=adjusted_quantity,
            position_size_usd=adjusted_position_size_usd,
            leverage=order_params.leverage,
            direction=order_params.direction,
            risk_amount_usd=adjusted_risk_amount_usd,
        )

    def _extract_filled_qty(self, order_info: dict) -> float:
        info = order_info.get("info") or {}
        return max(
            self._safe_float(order_info.get("filled")),
            self._safe_float(info.get("cumExecQty")),
            self._safe_float(info.get("cum_exec_qty")),
        )

    def _extract_remaining_qty(self, order_info: dict, fallback_amount: float) -> float:
        info = order_info.get("info") or {}
        remaining = order_info.get("remaining")
        if remaining is not None:
            return max(self._safe_float(remaining), 0.0)
        leaves_qty = info.get("leavesQty") or info.get("leaves_qty")
        if leaves_qty is not None:
            return max(self._safe_float(leaves_qty), 0.0)
        return max(fallback_amount - self._extract_filled_qty(order_info), 0.0)

    def _extract_average_fill_price(self, order_info: dict, fallback_price: float) -> float:
        info = order_info.get("info") or {}
        return (
            self._safe_float(order_info.get("average"))
            or self._safe_float(info.get("avgPrice"))
            or self._safe_float(info.get("avg_price"))
            or self._safe_float(order_info.get("price"))
            or fallback_price
        )

    async def _cancel_entry_if_active(self, o: ManagedOrder, exchange, symbol: str, reason: str):
        if not o.entry_order_active or not o.exchange_order_id:
            return
        try:
            await self._exchange_call(exchange.cancel_order(o.exchange_order_id, symbol))
            await log("execution", f"Cancelled remaining entry order for {o.order_id}: {reason}")
        except Exception as cancel_err:
            await log("execution", f"Failed cancelling remaining entry for {o.order_id}: {cancel_err}", level="WARNING")
        finally:
            o.entry_order_active = False

    async def _fetch_position_size(self, exchange, symbol: str) -> Optional[float]:
        try:
            positions = await self._exchange_call(exchange.fetch_positions([symbol]))
        except Exception:
            return None
        for pos in positions or []:
            contracts = self._safe_float(pos.get("contracts"))
            if contracts:
                return abs(contracts)
            info = pos.get("info") or {}
            size = self._safe_float(info.get("size"))
            if size:
                return abs(size)
        return 0.0

    async def _place_separate_protection_orders(self, o: ManagedOrder, exchange, symbol: str):
        if o.protected_qty <= 0:
            return
        if not o.sl_exchange_order_id:
            try:
                sl_side = "sell" if o.direction == "LONG" else "buy"
                sl_ord = await self._exchange_call(exchange.create_order(
                    symbol=symbol,
                    type="stop",
                    side=sl_side,
                    amount=o.protected_qty,
                    price=o.current_sl,
                    params={"triggerPrice": o.current_sl, "reduceOnly": True}
                ))
                o.sl_exchange_order_id = sl_ord.get("id")
            except Exception as sl_err:
                await log("execution", f"Failed placing Stop Loss for {o.order_id}: {sl_err}", level="ERROR")

        if not o.tp_exchange_order_id:
            try:
                tp_side = "sell" if o.direction == "LONG" else "buy"
                tp_ord = await self._exchange_call(exchange.create_order(
                    symbol=symbol,
                    type="limit",
                    side=tp_side,
                    amount=o.protected_qty,
                    price=o.current_tp,
                    params={"reduceOnly": True}
                ))
                o.tp_exchange_order_id = tp_ord.get("id")
            except Exception as tp_err:
                await log("execution", f"Failed placing Take Profit for {o.order_id}: {tp_err}", level="ERROR")
        
    def set_accounts_config(self, accounts: List[dict]):
        self.accounts_config = accounts
        valid_account_ids = {acc.get("id") for acc in accounts if acc.get("id")}
        stale_account_ids = [account_id for account_id in list(self.exchanges) if account_id not in valid_account_ids]
        for account_id in stale_account_ids:
            self.exchanges.pop(account_id, None)

        for acc in accounts:
            account_id = acc.get("id")
            if not account_id:
                continue
            self.exchanges[account_id] = self._create_exchange(acc)

        if self.active_orders:
            before_count = len(self.active_orders)
            self.active_orders = [order for order in self.active_orders if order.account_id in valid_account_ids]
            pruned_count = before_count - len(self.active_orders)
            if pruned_count > 0:
                log_sync(f"ExecutionEngine: Pruned {pruned_count} active orders for removed accounts.", level="WARNING")

    def _create_exchange(self, account_config: dict):
        credentials = account_config.get("credentials", {})
        api_key = credentials.get("api_key", "")
        api_secret = credentials.get("secret") or credentials.get("api_secret") or ""
        
        # If API keys are missing or placeholders, fallback to SimulatedExchange
        placeholder_markers = ("PLACEHOLDER", "YOUR_", "REPLACE_ME", "CHANGEME")
        if (not api_key) or any(m in str(api_key).upper() for m in placeholder_markers):
            log_sync(f"ExecutionEngine: Initializing SIMULATED exchange for account {account_config['id']}.")
            return SimulatedExchange(account_config)
            
        exchange_name = account_config.get("exchange", "").lower()
        is_demo = account_config.get("id") == "bybit-demo" or "demo" in account_config.get("name", "").lower()
        
        try:
            if exchange_name == "bybit":
                exchange = ccxt.bybit({
                    "apiKey": api_key,
                    "secret": api_secret,
                    "options": {"defaultType": "linear"}
                })
                if credentials.get("demo_trading"):
                    exchange.enable_demo_trading(True)
                elif credentials.get("testnet") or is_demo:
                    exchange.set_sandbox_mode(True)
                return exchange
            elif exchange_name == "hyperliquid":
                exchange = ccxt.hyperliquid({
                    "apiKey": credentials.get("wallet_address", ""),
                    "secret": api_secret,
                })
                return exchange
        except Exception as e:
            log_sync(f"ExecutionEngine: Error creating real ccxt exchange for {account_config['id']}: {e}. Falling back to simulation.", level="ERROR")
            
        return SimulatedExchange(account_config)

    def get_active_orders(self) -> List[dict]:
        """Returns all managed active orders serialized for the frontend."""
        return [
            {
                "order_id": o.order_id,
                "exchange_order_id": o.exchange_order_id,
                "account_id": o.account_id,
                "coin": o.coin,
                "direction": o.direction,
                "current_entry": o.current_entry,
                "current_sl": o.current_sl,
                "current_tp": o.current_tp,
                "current_qty": o.current_qty,
                "status": o.status,
                "fill_price": o.fill_price,
                "fill_time": o.fill_time,
                "session_id": o.session_id,
                "exchange_name": o.exchange_name,
                "is_simulated": o.is_simulated,
                "entry_qty": o.entry_qty,
                "filled_qty": o.filled_qty,
                "protected_qty": o.protected_qty,
                "partial_fill_detected": o.partial_fill_detected,
                "entry_order_active": o.entry_order_active,
                "exchange_missing_count": o.exchange_missing_count
            } for o in self.active_orders
        ]

    def _managed_order_to_dict(self, o: ManagedOrder) -> dict:
        return {
            "order_id": o.order_id,
            "exchange_order_id": o.exchange_order_id,
            "account_id": o.account_id,
            "coin": o.coin,
            "direction": o.direction,
            "current_entry": o.current_entry,
            "current_sl": o.current_sl,
            "current_tp": o.current_tp,
            "current_qty": o.current_qty,
            "status": o.status,
            "fill_price": o.fill_price,
            "fill_time": o.fill_time,
            "session_id": o.session_id,
            "trade_journal_id": o.trade_journal_id,
            "risk_value": o.risk_value,
            "rr": o.rr,
            "exchange_name": o.exchange_name,
            "is_simulated": o.is_simulated,
            "tp_exchange_order_id": o.tp_exchange_order_id,
            "sl_exchange_order_id": o.sl_exchange_order_id,
            "entry_qty": o.entry_qty,
            "filled_qty": o.filled_qty,
            "protected_qty": o.protected_qty,
            "partial_fill_detected": o.partial_fill_detected,
            "entry_order_active": o.entry_order_active,
            "exchange_missing_count": o.exchange_missing_count,
        }

    def _dict_to_managed_order(self, item: dict) -> Optional[ManagedOrder]:
        required = ["order_id", "account_id", "coin", "direction", "current_entry", "current_sl", "current_tp", "current_qty", "status"]
        if any(k not in item for k in required):
            return None
        try:
            return ManagedOrder(
                order_id=str(item["order_id"]),
                exchange_order_id=item.get("exchange_order_id"),
                account_id=str(item["account_id"]),
                coin=str(item["coin"]).upper(),
                direction=str(item["direction"]).upper(),
                current_entry=float(item["current_entry"]),
                current_sl=float(item["current_sl"]),
                current_tp=float(item["current_tp"]),
                current_qty=float(item["current_qty"]),
                status=str(item["status"]),
                fill_price=(float(item["fill_price"]) if item.get("fill_price") is not None else None),
                fill_time=item.get("fill_time"),
                session_id=str(item.get("session_id", "")),
                trade_journal_id=str(item.get("trade_journal_id", item["order_id"])),
                risk_value=float(item.get("risk_value", 1.0)),
                rr=float(item.get("rr", 2.0)),
                exchange_name=str(item.get("exchange_name", "bybit")),
                is_simulated=bool(item.get("is_simulated", True)),
                tp_exchange_order_id=item.get("tp_exchange_order_id"),
                sl_exchange_order_id=item.get("sl_exchange_order_id"),
                entry_qty=float(item.get("entry_qty", item["current_qty"])),
                filled_qty=float(item.get("filled_qty", 0.0)),
                protected_qty=float(item.get("protected_qty", 0.0)),
                partial_fill_detected=bool(item.get("partial_fill_detected", False)),
                entry_order_active=bool(item.get("entry_order_active", True)),
                exchange_missing_count=int(item.get("exchange_missing_count", 0)),
            )
        except Exception:
            return None

    async def save_active_orders_snapshot(self):
        """
        Persist active orders atomically so restart can recover in-memory state.
        """
        async with self.snapshot_lock:
            try:
                os.makedirs(self.data_dir, exist_ok=True)
                payload = {
                    "schema_version": self.snapshot_schema_version,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "active_orders": [self._managed_order_to_dict(o) for o in self.active_orders],
                }
                with open(self.snapshot_tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)

                last_err = None
                for attempt in range(3):
                    try:
                        os.replace(self.snapshot_tmp_path, self.snapshot_path)
                        return
                    except OSError as replace_err:
                        last_err = replace_err
                        await asyncio.sleep(0.2 * (attempt + 1))

                raise last_err if last_err is not None else OSError("Unknown snapshot replace failure")
            except Exception as e:
                log_sync(f"ExecutionEngine: Failed saving active order snapshot: {e}", level="ERROR")
                try:
                    if os.path.exists(self.snapshot_tmp_path):
                        os.remove(self.snapshot_tmp_path)
                except Exception:
                    pass

    async def load_active_orders_snapshot(self):
        """
        Restore active orders from latest snapshot, with strong validation and dedupe.
        """
        if not os.path.exists(self.snapshot_path):
            log_sync("ExecutionEngine: No active order snapshot found; skipping restore.", level="INFO")
            return
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log_sync(f"ExecutionEngine: Snapshot parse failed; ignoring snapshot. Error: {e}", level="ERROR")
            return

        if not isinstance(data, dict):
            log_sync("ExecutionEngine: Snapshot invalid format (not object); ignoring.", level="ERROR")
            return

        items = data.get("active_orders", [])
        if not isinstance(items, list):
            log_sync("ExecutionEngine: Snapshot invalid format (active_orders not list); ignoring.", level="ERROR")
            return

        restored: List[ManagedOrder] = []
        seen_ids = set()
        valid_account_ids = {a.get("id") for a in self.accounts_config if a.get("id")}

        for item in items:
            if not isinstance(item, dict):
                continue
            order = self._dict_to_managed_order(item)
            if not order:
                continue
            if order.order_id in seen_ids:
                continue
            if order.account_id not in valid_account_ids:
                # Account no longer exists in config; skip safely.
                continue
            seen_ids.add(order.order_id)
            restored.append(order)

        self.active_orders = restored
        log_sync(f"ExecutionEngine: Restored {len(restored)} active orders from snapshot.", level="INFO")

    async def start_intent(self, intent: Intent):
        """Starts the order lifecycle for a user trade intent."""
        # Find account config
        acc_cfg = next((a for a in self.accounts_config if a["id"] == intent.account_id), None)
        if not acc_cfg:
            await log("execution", f"Account {intent.account_id} not found.", level="ERROR")
            return
            
        # Check if already trading/dragging this coin (globally across sessions)
        existing = next((o for o in self.active_orders if o.account_id == intent.account_id and o.coin == intent.coin), None)
        if existing:
            await log("execution", f"Account {intent.account_id} already has an active trade/position for {intent.coin}.", level="WARNING")
            return

        # Check if account has reached its concurrent positions limit
        max_positions = int(acc_cfg.get("max_positions", 3))
        active_total = [o for o in self.active_orders if o.account_id == intent.account_id]
        if len(active_total) >= max_positions:
            await log("execution", f"Account {intent.account_id} has reached its limit of {max_positions} active position(s).", level="ERROR")
            return

        if not self.session_manager.is_session_execution_active(intent.session_id):
            await log("execution", f"Session {intent.session_id} has ended; refusing to place order.", level="WARNING")
            return
            
        # Get candles and calculate entry/SL/TP levels
        candles = self.data_feed_manager.get_candles(intent.coin)
        if not candles:
            await log("execution", f"No candles available to calculate levels for {intent.coin}.", level="ERROR")
            return
            
        from backend.core.strategy import get_latest_levels, load_strategy_config
        strat_cfg = load_strategy_config(intent.coin)
        levels = get_latest_levels(candles, strat_cfg)
        
        entry_price = levels.evma
        sl_price = levels.lower_stop if intent.direction == "LONG" else levels.upper_stop
        
        # Get account balance
        exchange = self.exchanges.get(intent.account_id)
        is_simulated = isinstance(exchange, SimulatedExchange)
        
        try:
            balance = await self._fetch_account_usdt_balance(exchange, is_simulated)
        except Exception as e:
            await log("execution", f"Failed to fetch balance for {intent.account_id}: {e}. Using default $10000.", level="WARNING")
            balance = 10000.0
            
        # Calculate sizing
        try:
            order_params = RiskEngine.calculate_order(
                acc_cfg, entry_price, sl_price, intent.direction, intent.rr, balance, intent.risk_value
            )
        except Exception as e:
            await log("execution", f"Risk Engine sizing failure: {e}", level="ERROR")
            return
            
        # Place the drag order on the exchange
        symbol = intent.coin if is_simulated else ccxt_symbol(intent.coin, acc_cfg["exchange"].lower())
        exchange_order_id = None
        
        await log("execution", f"Placing Initial Limit Order on {intent.account_id} for {intent.coin} @ {order_params.entry_price}")

        try:
            if is_simulated:
                ord_res = await self._exchange_call(exchange.create_order(
                    symbol=symbol,
                    type="limit",
                    side="buy" if intent.direction == "LONG" else "sell",
                    amount=order_params.quantity,
                    price=order_params.entry_price
                ))
                exchange_order_id = ord_res["id"]
            else:
                applied_leverage = await self._apply_exchange_leverage(acc_cfg, exchange, symbol, order_params.leverage)
                if not applied_leverage:
                    applied_leverage = float(max(1, math.ceil(order_params.leverage)))
                live_balance = await self._fetch_account_usdt_balance(exchange, False)
                order_params = self._apply_margin_safety_cap(
                    order_params,
                    live_balance,
                    applied_leverage,
                    intent.account_id,
                    symbol,
                )
                live_max_notional = live_balance * applied_leverage * 0.95 if live_balance > 0 and applied_leverage > 0 else order_params.position_size_usd
                await log(
                    "execution",
                    f"Preflight {intent.account_id} {symbol}: balance=${live_balance:.2f}, "
                    f"applied_leverage={applied_leverage:.0f}x, max_notional=${live_max_notional:.2f}, "
                    f"order_notional=${order_params.position_size_usd:.2f}, qty={order_params.quantity:.6f}",
                )
                entry_order_params = self._build_entry_order_params(acc_cfg, order_params)
                # ccxt order execution
                ord_res = await self._exchange_call(exchange.create_order(
                    symbol=symbol,
                    type="limit",
                    side="buy" if intent.direction == "LONG" else "sell",
                    amount=order_params.quantity,
                    price=order_params.entry_price,
                    params=entry_order_params
                ))
                exchange_order_id = ord_res.get("id")
        except Exception as e:
            await log("execution", f"Exchange Placement failed for account {intent.account_id}: {e}", level="ERROR")
            return
            
        # Generate database journal record (status = open but order not filled yet)
        trade_id = f"trade_{int(time.time())}_{intent.account_id}_{intent.coin}"
        journal_data = {
            "id": trade_id,
            "account_id": intent.account_id,
            "exchange": acc_cfg["exchange"],
            "coin": intent.coin,
            "direction": intent.direction,
            "session_id": intent.session_id,
            "session_type": intent.session_type,
            "intent_time": datetime.now(timezone.utc).isoformat(),
            "open_time": None,  # Will be populated when filled
            "entry_price": order_params.entry_price,
            "evma_at_entry": levels.evma,
            "sl_price": order_params.sl_price,
            "tp_price": order_params.tp_price,
            "planned_rr": intent.rr,
            "position_size_usd": order_params.position_size_usd,
            "leverage_used": order_params.leverage,
            "atr_at_entry": levels.norm_atr * strat_cfg.baseline_volatility * levels.evma,
            "sl_distance_pct": abs(order_params.entry_price - order_params.sl_price) / order_params.entry_price,
            "status": "open"
        }
        await open_trade(journal_data)
        
        # Track the ManagedOrder
        managed = ManagedOrder(
            order_id=trade_id,
            exchange_order_id=exchange_order_id,
            account_id=intent.account_id,
            coin=intent.coin,
            direction=intent.direction,
            current_entry=order_params.entry_price,
            current_sl=order_params.sl_price,
            current_tp=order_params.tp_price,
            current_qty=order_params.quantity,
            status="dragging",
            session_id=intent.session_id,
            trade_journal_id=trade_id,
            risk_value=intent.risk_value,
            rr=intent.rr,
            exchange_name=acc_cfg["exchange"],
            is_simulated=is_simulated,
            entry_qty=order_params.quantity
        )
        self.active_orders.append(managed)
        self.session_manager.mark_session_traded(intent.session_id, intent.coin)
        await log("execution", f"Managed order created and tracking: {trade_id}")
        await self.save_active_orders_snapshot()

    async def on_candle_close(self, coin: str):
        """
        Triggered on every 1m closed candle.
        Only advances the EVMA drag logic when that 1m close completes a 5m candle
        boundary (06:05, 06:10, 06:15, ...).
        Simulates fills for simulated accounts if market close crossed limit price.
        """
        candles = self.data_feed_manager.get_candles(coin)
        if not candles:
            return
            
        last_candle = candles[-1]
        close_ts = int(last_candle["time"]) + 60
        if close_ts % 300 != 0:
            return
        
        from backend.core.strategy import get_latest_levels, load_strategy_config
        strat_cfg = load_strategy_config(coin)
        levels = get_latest_levels(candles, strat_cfg)
        
        for o in list(self.active_orders):
            if o.coin != coin:
                continue
                
            if o.status == "dragging":
                if not self.session_manager.is_session_execution_active(o.session_id):
                    continue
                # DRAG: Modify order to keep entry at latest EVMA price, recalculate TP/SL & qty
                acc_cfg = next((a for a in self.accounts_config if a["id"] == o.account_id), None)
                exchange = self.exchanges.get(o.account_id)
                
                # Sizing updates
                try:
                    balance_data = await self._exchange_call(exchange.fetch_balance())
                    balance = balance_data["total"]["USDT"] if o.is_simulated else balance_data.get("free", {}).get("USDT", 1000.0)
                except Exception:
                    balance = 10000.0
                    
                new_entry = levels.evma
                new_sl = levels.lower_stop if o.direction == "LONG" else levels.upper_stop
                
                try:
                    params = RiskEngine.calculate_order(
                        acc_cfg, new_entry, new_sl, o.direction, o.rr, balance, o.risk_value
                    )
                except Exception as sizing_err:
                    await log("execution", f"Failed sizing update during drag for {o.order_id}: {sizing_err}", level="ERROR")
                    continue
                    
                await log("execution", f"Dragging order {o.order_id}: Updating Price from {o.current_entry} to {new_entry}", level="DEBUG")
                
                # Update properties
                o.current_entry = params.entry_price
                o.current_sl = params.sl_price
                o.current_tp = params.tp_price
                o.current_qty = params.quantity
                o.entry_qty = params.quantity
                
                try:
                    symbol = o.coin if o.is_simulated else ccxt_symbol(o.coin, acc_cfg["exchange"].lower())
                    if o.is_simulated:
                        await self._exchange_call(exchange.edit_order(
                            order_id=o.exchange_order_id,
                            symbol=symbol,
                            type="limit",
                            side="buy" if o.direction == "LONG" else "sell",
                            amount=o.current_qty,
                            price=o.current_entry
                        ))
                    else:
                        # ccxt order edit/modify
                        edit_params = self._build_entry_order_params(acc_cfg, params)
                        try:
                            await self._exchange_call(exchange.edit_order(
                                id=o.exchange_order_id,
                                symbol=symbol,
                                type="limit",
                                side="buy" if o.direction == "LONG" else "sell",
                                amount=o.current_qty,
                                price=o.current_entry,
                                params=edit_params
                            ))
                        except Exception:
                            await self._exchange_call(exchange.edit_order(
                                id=o.exchange_order_id,
                                symbol=symbol,
                                type="limit",
                                side="buy" if o.direction == "LONG" else "sell",
                                amount=o.current_qty,
                                price=o.current_entry
                            ))
                except Exception as edit_err:
                    await log("execution", f"Failed to modify order {o.order_id} on exchange: {edit_err}. Keeping in state.", level="WARNING")
                    
                # Update journal entry in db
                await update_trade(o.order_id, {
                    "entry_price": o.current_entry,
                    "sl_price": o.current_sl,
                    "tp_price": o.current_tp,
                    "position_size_usd": params.position_size_usd,
                    "leverage_used": params.leverage,
                    "sl_distance_pct": abs(o.current_entry - o.current_sl) / o.current_entry
                })
                
                # Simulated Fill Logic
                if o.is_simulated:
                    # Determine if price hit evma entry
                    # If LONG, low price <= EVMA entry price
                    # If SHORT, high price >= EVMA entry price
                    l_low = last_candle["low"]
                    l_high = last_candle["high"]
                    is_filled = False
                    
                    if o.direction == "LONG" and l_low <= o.current_entry:
                        is_filled = True
                    elif o.direction == "SHORT" and l_high >= o.current_entry:
                        is_filled = True
                        
                    if is_filled:
                        o.status = "filled"
                        o.fill_price = o.current_entry
                        o.fill_time = datetime.now(timezone.utc).isoformat()
                        o.filled_qty = o.current_qty
                        o.protected_qty = o.current_qty
                        o.entry_order_active = False
                        await log("execution", f"Simulated Order {o.order_id} FILLED at {o.fill_price}!")
                        
                        # Update journal entry with open/fill data
                        await update_trade(o.order_id, {
                            "open_time": o.fill_time,
                            "entry_price": o.fill_price,
                            "status": "open"
                        })
            
            elif o.status == "filled":
                # Monitor TP/SL for Filled Position
                if o.is_simulated:
                    l_low = last_candle["low"]
                    l_high = last_candle["high"]
                    hit_tp = False
                    hit_sl = False
                    
                    if o.direction == "LONG":
                        if l_high >= o.current_tp:
                            hit_tp = True
                        elif l_low <= o.current_sl:
                            hit_sl = True
                    else:
                        if l_low <= o.current_tp:
                            hit_tp = True
                        elif l_high >= o.current_sl:
                            hit_sl = True
                            
                    if hit_tp or hit_sl:
                        o.status = "tp_hit" if hit_tp else "sl_hit"
                        exit_price = o.current_tp if hit_tp else o.current_sl
                        await log("execution", f"Simulated Position {o.order_id} hit {'TAKE PROFIT' if hit_tp else 'STOP LOSS'} at {exit_price}")
                        
                        # Close trade in journal DB
                        await close_trade(o.order_id, {
                            "exit_price": exit_price,
                            "exit_reason": "tp" if hit_tp else "sl",
                            "fees_usd": o.current_qty * exit_price * 0.0006  # Estimate fees
                        })
                        
                        self.active_orders.remove(o)
                        await self.save_active_orders_snapshot()

    async def poll_active_orders_lifecycle(self):
        """
        Background task running periodically to poll and synchronize real exchange order statuses
        (Bybit/Hyperliquid) and detect real fills, TP hits, SL hits, or cancel unfilled.
        """
        while True:
            try:
                for o in list(self.active_orders):
                    if o.is_simulated or o.status not in ("dragging", "filled"):
                        continue
                        
                    exchange = self.exchanges.get(o.account_id)
                    symbol = o.coin if o.is_simulated else ccxt_symbol(o.coin, o.exchange_name.lower())
                    
                    if o.status == "dragging":
                        # Fetch the open order status from the exchange to detect full or partial fill
                        try:
                            order_info = await self._exchange_call(exchange.fetch_order(o.exchange_order_id, symbol, params={"acknowledged": True}))
                            ord_status = order_info.get("status", "").lower()
                            
                            if ord_status in ("canceled", "cancelled", "rejected", "expired"):
                                await log("execution", f"Real Order {o.order_id} (exchange ID: {o.exchange_order_id}) was {ord_status} on exchange. Cleaning up.")
                                await close_trade(o.order_id, {
                                    "exit_price": o.current_entry,
                                    "exit_reason": "canceled_on_exchange",
                                    "fees_usd": 0.0
                                })
                                self.active_orders.remove(o)
                                await self.save_active_orders_snapshot()
                                continue
                                
                            filled_qty = self._extract_filled_qty(order_info)
                            remaining_qty = self._extract_remaining_qty(order_info, o.entry_qty or o.current_qty)
                            is_closed_fill = ord_status in ("closed", "filled") or (filled_qty > 0 and remaining_qty <= 1e-12)
                            is_partial_fill = filled_qty > 0 and not is_closed_fill

                            # Any fill stops dragging. Partial fills keep the remaining entry frozen until TP/SL resolves.
                            if is_partial_fill or is_closed_fill:
                                acc_cfg = next((a for a in self.accounts_config if a["id"] == o.account_id), {})
                                o.status = "filled"
                                o.partial_fill_detected = is_partial_fill
                                o.filled_qty = filled_qty
                                o.protected_qty = filled_qty
                                o.current_qty = filled_qty
                                o.fill_price = self._extract_average_fill_price(order_info, o.current_entry)
                                o.fill_time = datetime.now(timezone.utc).isoformat()
                                o.entry_order_active = is_partial_fill
                                
                                fill_label = "PARTIALLY FILLED" if is_partial_fill else "FILLED"
                                await log("execution", f"Real Order {o.order_id} {fill_label}: protected_qty={o.protected_qty}, remaining_entry_qty={remaining_qty}")
                                
                                if not self._uses_attached_entry_protection(acc_cfg):
                                    await self._place_separate_protection_orders(o, exchange, symbol)
                                    
                                # Update journal DB
                                await update_trade(o.order_id, {
                                    "open_time": o.fill_time,
                                    "entry_price": o.fill_price,
                                    "sl_price": o.current_sl,
                                    "tp_price": o.current_tp,
                                    "position_size_usd": o.protected_qty * o.fill_price,
                                    "status": "open"
                                })
                                await self.save_active_orders_snapshot()
                        except (ccxt.OrderNotFound, ccxt.InvalidOrder) as exchange_err:
                            position_size = await self._fetch_position_size(exchange, symbol)
                            if position_size is not None and position_size > 1e-12:
                                o.status = "filled"
                                o.partial_fill_detected = False
                                o.filled_qty = position_size
                                o.protected_qty = position_size
                                o.current_qty = position_size
                                o.fill_price = o.current_entry
                                o.fill_time = datetime.now(timezone.utc).isoformat()
                                o.entry_order_active = False
                                await log(
                                    "execution",
                                    f"Real Order {o.order_id} entry not found, but position exists on exchange "
                                    f"(size={position_size}); treating as filled."
                                )
                                if not self._uses_attached_entry_protection(acc_cfg):
                                    await self._place_separate_protection_orders(o, exchange, symbol)
                                await update_trade(o.order_id, {
                                    "open_time": o.fill_time,
                                    "entry_price": o.fill_price,
                                    "sl_price": o.current_sl,
                                    "tp_price": o.current_tp,
                                    "position_size_usd": o.protected_qty * o.fill_price,
                                    "status": "open"
                                })
                                await self.save_active_orders_snapshot()
                                continue

                            o.exchange_missing_count += 1
                            if o.exchange_missing_count < 2:
                                await log(
                                    "execution",
                                    f"Real Order {o.order_id} (exchange ID: {o.exchange_order_id}) could not be fetched "
                                    f"yet ({exchange_err}). Miss {o.exchange_missing_count}/2; keeping it alive.",
                                    level="WARNING"
                                )
                                await self.save_active_orders_snapshot()
                                continue

                            await log("execution", f"Real Order {o.order_id} (exchange ID: {o.exchange_order_id}) is dead or not found on exchange ({exchange_err}). Cleaning up.")
                            await close_trade(o.order_id, {
                                "exit_price": o.current_entry,
                                "exit_reason": "not_found_on_exchange",
                                "fees_usd": 0.0
                            })
                            self.active_orders.remove(o)
                            await self.save_active_orders_snapshot()
                            continue
                        except Exception as poll_err:
                            err_str = str(poll_err).lower()
                            err_class = poll_err.__class__.__name__
                            if "ordernotfound" in err_class.lower() or "order not found" in err_str or "not found" in err_str:
                                position_size = await self._fetch_position_size(exchange, symbol)
                                if position_size is not None and position_size > 1e-12:
                                    o.status = "filled"
                                    o.partial_fill_detected = False
                                    o.filled_qty = position_size
                                    o.protected_qty = position_size
                                    o.current_qty = position_size
                                    o.fill_price = o.current_entry
                                    o.fill_time = datetime.now(timezone.utc).isoformat()
                                    o.entry_order_active = False
                                    await log(
                                        "execution",
                                        f"Real Order {o.order_id} entry not found, but position exists on exchange "
                                        f"(size={position_size}); treating as filled."
                                    )
                                    if not self._uses_attached_entry_protection(acc_cfg):
                                        await self._place_separate_protection_orders(o, exchange, symbol)
                                    await update_trade(o.order_id, {
                                        "open_time": o.fill_time,
                                        "entry_price": o.fill_price,
                                        "sl_price": o.current_sl,
                                        "tp_price": o.current_tp,
                                        "position_size_usd": o.protected_qty * o.fill_price,
                                        "status": "open"
                                    })
                                    await self.save_active_orders_snapshot()
                                    continue

                                o.exchange_missing_count += 1
                                if o.exchange_missing_count < 2:
                                    await log(
                                        "execution",
                                        f"Real Order {o.order_id} (exchange ID: {o.exchange_order_id}) could not be fetched yet "
                                        f"({poll_err}). Miss {o.exchange_missing_count}/2; keeping it alive.",
                                        level="WARNING"
                                    )
                                    await self.save_active_orders_snapshot()
                                    continue

                                await log("execution", f"Real Order {o.order_id} (exchange ID: {o.exchange_order_id}) detected as not found/dead: {poll_err}. Cleaning up.")
                                await close_trade(o.order_id, {
                                    "exit_price": o.current_entry,
                                    "exit_reason": "not_found_on_exchange",
                                    "fees_usd": 0.0
                                })
                                self.active_orders.remove(o)
                                await self.save_active_orders_snapshot()
                                continue
                            else:
                                await log("execution", f"Failed checking fill status for {o.order_id}: {poll_err}", level="WARNING")
                            
                    elif o.status == "filled":
                        # Monitor TP/SL on Exchange
                        try:
                            # Periodically verify the position is still open on exchange.
                            # If the position is closed (size <= 1e-12), the execution is resolved/dead.
                            position_size = await self._fetch_position_size(exchange, symbol)
                            if position_size is not None and position_size <= 1e-12:
                                await self._cancel_entry_if_active(o, exchange, symbol, "position closed on exchange")
                                if o.sl_exchange_order_id:
                                    try:
                                        await self._exchange_call(exchange.cancel_order(o.sl_exchange_order_id, symbol))
                                    except Exception:
                                        pass
                                if o.tp_exchange_order_id:
                                    try:
                                        await self._exchange_call(exchange.cancel_order(o.tp_exchange_order_id, symbol))
                                    except Exception:
                                        pass
                                await log("execution", f"Real Position {o.order_id} detected closed/dead on exchange (size is 0). Cleaning up.")
                                await close_trade(o.order_id, {
                                    "exit_price": o.current_tp,
                                    "exit_reason": "closed_on_exchange",
                                    "fees_usd": o.protected_qty * o.current_entry * 0.0006
                                })
                                self.active_orders.remove(o)
                                await self.save_active_orders_snapshot()
                                continue

                            # Check stop loss status
                            if o.sl_exchange_order_id:
                                sl_info = await self._exchange_call(exchange.fetch_order(o.sl_exchange_order_id, symbol, params={"acknowledged": True}))
                                if sl_info.get("status", "").lower() == "filled":
                                    o.status = "sl_hit"
                                    await self._cancel_entry_if_active(o, exchange, symbol, "stop loss hit")
                                    # Cancel corresponding TP
                                    if o.tp_exchange_order_id:
                                        await self._exchange_call(exchange.cancel_order(o.tp_exchange_order_id, symbol))
                                    await log("execution", f"Real Position {o.order_id} hit STOP LOSS!")
                                    await close_trade(o.order_id, {
                                        "exit_price": o.current_sl,
                                        "exit_reason": "sl",
                                        "fees_usd": o.current_qty * o.current_sl * 0.0006
                                    })
                                    self.active_orders.remove(o)
                                    await self.save_active_orders_snapshot()
                                    continue
                                    
                            # Check take profit status
                            if o.tp_exchange_order_id:
                                tp_info = await self._exchange_call(exchange.fetch_order(o.tp_exchange_order_id, symbol, params={"acknowledged": True}))
                                if tp_info.get("status", "").lower() == "filled":
                                    o.status = "tp_hit"
                                    await self._cancel_entry_if_active(o, exchange, symbol, "take profit hit")
                                    # Cancel corresponding SL
                                    if o.sl_exchange_order_id:
                                        await self._exchange_call(exchange.cancel_order(o.sl_exchange_order_id, symbol))
                                    await log("execution", f"Real Position {o.order_id} hit TAKE PROFIT!")
                                    await close_trade(o.order_id, {
                                        "exit_price": o.current_tp,
                                        "exit_reason": "tp",
                                        "fees_usd": o.current_qty * o.current_tp * 0.0006
                                    })
                                    self.active_orders.remove(o)
                                    await self.save_active_orders_snapshot()
                                    continue
                        except Exception as poll_exit_err:
                            await log("execution", f"Failed checking TP/SL hits for filled {o.order_id}: {poll_exit_err}", level="WARNING")
                            
            except Exception as loop_err:
                await log("execution", f"Error in active orders lifecycle poll: {loop_err}", level="ERROR")
                
            await asyncio.sleep(5)  # Poll real exchange orders every 5 seconds
            await self.save_active_orders_snapshot()

    async def cancel_dragging_for_ended_sessions(self):
        """Cancels unfilled dragging orders for any session past its end_time."""
        ended_session_ids = {
            o.session_id
            for o in self.active_orders
            if o.status == "dragging"
            and not self.session_manager.is_session_execution_active(o.session_id)
        }
        for session_id in ended_session_ids:
            await self.cancel_all_for_session(session_id)

    async def cancel_all_for_session(self, session_id: str):
        """Cancels all pending dragging orders for the session when session ends."""
        for o in list(self.active_orders):
            if o.session_id == session_id and o.status == "dragging":
                exchange = self.exchanges.get(o.account_id)
                symbol = o.coin if o.is_simulated else ccxt_symbol(o.coin, o.exchange_name.lower())
                
                await log("execution", f"Canceling dragging order {o.order_id} due to session {session_id} ending.")
                try:
                    await self._exchange_call(exchange.cancel_order(o.exchange_order_id, symbol))
                except Exception as cancel_err:
                    await log("execution", f"Failed cancelling {o.order_id} on exchange: {cancel_err}", level="WARNING")
                    
                o.status = "cancelled"
                await update_trade(o.order_id, {"status": "cancelled", "exit_reason": "session_end"})
                self.active_orders.remove(o)
        await self.save_active_orders_snapshot()

    async def cancel_managed_order(self, order_id: str) -> bool:
        """
        Explicitly cancels a managed dragging order requested by the user.
        """
        # Find order
        o = next((order for order in self.active_orders if order.order_id == order_id), None)
        if not o:
            await log("execution", f"Cancel failed: Managed order {order_id} not found.", level="WARNING")
            return False
            
        if o.status != "dragging":
            await log("execution", f"Cancel failed: Managed order {order_id} has status '{o.status}', not 'dragging'.", level="WARNING")
            return False
            
        if o.filled_qty > 0 or o.partial_fill_detected:
            await log("execution", f"Cancel failed: Managed order {order_id} has been partially/fully filled.", level="WARNING")
            return False
            
        exchange = self.exchanges.get(o.account_id)
        symbol = o.coin if o.is_simulated else ccxt_symbol(o.coin, o.exchange_name.lower())
        
        await log("execution", f"User requested cancellation of dragging order {o.order_id} (Exchange ID: {o.exchange_order_id}).")
        
        # Cancel at the exchange before changing local state. A failed or
        # ambiguous exchange response must leave the order active for retry or
        # reconciliation rather than reporting a false cancellation.
        if not o.is_simulated and (not exchange or not o.exchange_order_id):
            raise ManagedOrderCancelError(
                "The exchange order could not be located for cancellation. The managed order remains active."
            )

        if exchange and o.exchange_order_id:
            try:
                await self._exchange_call(exchange.cancel_order(o.exchange_order_id, symbol))
                await log("execution", f"Cancelled entry order {o.exchange_order_id} on exchange for managed order {o.order_id}.")
            except Exception as cancel_err:
                await log("execution", f"Failed cancelling order {o.order_id} (Exchange ID: {o.exchange_order_id}) on exchange: {cancel_err}", level="WARNING")
                raise ManagedOrderCancelError(
                    "The exchange did not confirm cancellation. The managed order remains active; check the exchange before retrying."
                ) from cancel_err
                
        # 2. Update status and journal
        o.status = "cancelled"
        await update_trade(o.order_id, {"status": "cancelled", "exit_reason": "user_cancelled"})
        
        # 3. Remove from active orders list
        if o in self.active_orders:
            self.active_orders.remove(o)
            
        await self.save_active_orders_snapshot()
        return True

