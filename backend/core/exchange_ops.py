"""
Exchange operations for the dashboard (reads, close, cancel, amend, TP/SL, new limits).
Uses ccxt async — same stack as the execution engine.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from backend.core.exchange_factory import (
    bybit_linear_symbol,
    ccxt_symbol,
    create_async_exchange,
    normalize_coin,
)
from backend.core.logger import log

CALL_TIMEOUT_SEC = 20


def _format_error(exc: Exception) -> str:
    msg = str(exc).strip()
    if not msg:
        return exc.__class__.__name__
    return msg


async def _call(coro):
    return await asyncio.wait_for(coro, timeout=CALL_TIMEOUT_SEC)


async def _reference_price(exchange, sym: str) -> float:
    ticker = await _call(exchange.fetch_ticker(sym))
    for key in ("last", "close", "bid", "ask"):
        val = ticker.get(key)
        if val is not None and float(val) > 0:
            return float(val)
    market = exchange.market(sym)
    info = market.get("info") or {}
    mid = info.get("midPx") or info.get("markPx")
    if mid is not None and float(mid) > 0:
        return float(mid)
    raise ValueError(f"Could not resolve a reference price for {sym}.")


def _bybit_params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {"category": "linear"}
    if extra:
        params.update(extra)
    return params


async def _with_exchange(account: dict, fn):
    exchange = create_async_exchange(account)
    try:
        return await fn(exchange)
    finally:
        await exchange.close()


async def close_position(account: dict, symbol: str, side: str, size: float) -> dict:
    exchange_name = account["exchange"].lower()
    sym = ccxt_symbol(symbol, exchange_name)
    close_side = "sell" if side.upper() == "LONG" else "buy"
    qty = float(size)

    async def _run(exchange):
        await _call(exchange.load_markets())
        ref_price = await _reference_price(exchange, sym)
        params: Dict[str, Any] = {"reduceOnly": True}
        if exchange_name == "bybit":
            params = _bybit_params(params)
        result = await _call(
            exchange.create_order(
                sym,
                "market",
                close_side,
                qty,
                ref_price,
                params,
            )
        )
        await log("exchange", f"Closed {side} {qty} on {sym} ({account['id']})")
        return {"status": "ok", "order": result}

    return await _with_exchange(account, _run)


async def cancel_order(account: dict, symbol: str, order_id: str) -> dict:
    exchange_name = account["exchange"].lower()
    sym = ccxt_symbol(symbol, exchange_name)

    async def _run(exchange):
        await _call(exchange.load_markets())
        params = _bybit_params() if exchange_name == "bybit" else {}
        result = await _call(exchange.cancel_order(str(order_id), sym, params))
        await log("exchange", f"Cancelled order {order_id} on {sym} ({account['id']})")
        return {"status": "ok", "order": result}

    return await _with_exchange(account, _run)


async def amend_order(
    account: dict,
    symbol: str,
    order_id: str,
    side: str,
    price: float,
    qty: float,
) -> dict:
    exchange_name = account["exchange"].lower()
    sym = ccxt_symbol(symbol, exchange_name)
    order_side = "buy" if side.upper() == "LONG" else "sell"

    async def _run(exchange):
        await _call(exchange.load_markets())
        params = _bybit_params() if exchange_name == "bybit" else {}
        try:
            result = await _call(
                exchange.edit_order(
                    str(order_id),
                    sym,
                    "limit",
                    order_side,
                    float(qty),
                    float(price),
                    params,
                )
            )
        except Exception as edit_err:
            if exchange_name != "bybit":
                raise edit_err
            result = await _call(
                exchange.private_post_v5_order_amend(
                    {
                        "category": "linear",
                        "symbol": bybit_linear_symbol(symbol),
                        "orderId": str(order_id),
                        "qty": exchange.amount_to_precision(sym, float(qty)),
                        "price": exchange.price_to_precision(sym, float(price)),
                    }
                )
            )
        await log("exchange", f"Amended order {order_id} on {sym} ({account['id']})")
        return {"status": "ok", "order": result}

    return await _with_exchange(account, _run)


async def place_limit_order(
    account: dict,
    coin: str,
    side: str,
    price: float,
    qty: float,
) -> dict:
    exchange_name = account["exchange"].lower()
    sym = ccxt_symbol(coin, exchange_name)
    order_side = "buy" if side.upper() == "LONG" else "sell"

    async def _run(exchange):
        await _call(exchange.load_markets())
        params: Dict[str, Any] = {"timeInForce": "Gtc"}
        if exchange_name == "bybit":
            params = _bybit_params(params)
        result = await _call(
            exchange.create_order(
                sym,
                "limit",
                order_side,
                float(qty),
                float(price),
                params,
            )
        )
        await log("exchange", f"Placed limit {side} {qty} @ {price} on {sym} ({account['id']})")
        return {"status": "ok", "order": result}

    return await _with_exchange(account, _run)


async def set_position_tpsl(
    account: dict,
    symbol: str,
    side: str,
    tp: Optional[float],
    sl: Optional[float],
) -> dict:
    exchange_name = account["exchange"].lower()

    if exchange_name == "bybit":
        return await _set_bybit_tpsl(account, symbol, tp, sl)

    if exchange_name == "hyperliquid":
        return await _set_hyperliquid_tpsl(account, symbol, side, tp, sl)

    raise ValueError(f"TP/SL not supported for exchange '{exchange_name}'")


async def _set_bybit_tpsl(account: dict, symbol: str, tp: Optional[float], sl: Optional[float]) -> dict:
    raw_symbol = bybit_linear_symbol(symbol)
    payload: Dict[str, Any] = {
        "category": "linear",
        "symbol": raw_symbol,
        "positionIdx": 0,
        "tpslMode": "Full",
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "tpTriggerBy": "LastPrice",
        "slTriggerBy": "LastPrice",
    }
    if tp is not None:
        payload["takeProfit"] = str(tp)
    if sl is not None:
        payload["stopLoss"] = str(sl)
    if tp is None and sl is None:
        raise ValueError("At least one of TP or SL must be provided.")

    async def _run(exchange):
        result = await _call(exchange.private_post_v5_position_trading_stop(payload))
        await log("exchange", f"Set TP/SL on {raw_symbol} ({account['id']})")
        return {"status": "ok", "result": result}

    return await _with_exchange(account, _run)


async def _set_hyperliquid_tpsl(
    account: dict,
    symbol: str,
    side: str,
    tp: Optional[float],
    sl: Optional[float],
) -> dict:
    coin = normalize_coin(symbol)
    sym = ccxt_symbol(coin, "hyperliquid")
    is_long = side.upper() == "LONG"
    close_side = "sell" if is_long else "buy"

    async def _run(exchange):
        await _call(exchange.load_markets())
        results: List[dict] = []
        qty = await _hyperliquid_position_size(exchange, sym)
        if qty <= 0:
            raise ValueError(f"No open position size found for {coin}.")

        ref_price = await _reference_price(exchange, sym)

        if tp is not None:
            tp_order = await _call(
                exchange.create_order(
                    sym,
                    "market",
                    close_side,
                    qty,
                    ref_price,
                    {"reduceOnly": True, "takeProfitPrice": float(tp)},
                )
            )
            results.append(tp_order)

        if sl is not None:
            sl_order = await _call(
                exchange.create_order(
                    sym,
                    "stop",
                    close_side,
                    qty,
                    float(sl),
                    {"reduceOnly": True, "triggerPrice": float(sl)},
                )
            )
            results.append(sl_order)

        if not results:
            raise ValueError("At least one of TP or SL must be provided.")

        await log("exchange", f"Set HL protection orders on {sym} ({account['id']})")
        return {"status": "ok", "orders": results}

    return await _with_exchange(account, _run)


async def _hyperliquid_position_size(exchange, sym: str) -> float:
    positions = await _call(exchange.fetch_positions([sym]))
    for pos in positions or []:
        contracts = pos.get("contracts")
        if contracts is not None and float(contracts) != 0:
            return abs(float(contracts))
        info = pos.get("info") or {}
        szi = info.get("position", {}).get("szi") if isinstance(info.get("position"), dict) else info.get("szi")
        if szi is not None and float(szi) != 0:
            return abs(float(szi))
    return 0.0


async def cancel_orders_batch(account: dict, items: List[dict]) -> dict:
    """Cancel multiple orders for one account. items: [{symbol, order_id}, ...]"""
    if not items:
        return {"status": "ok", "cancelled": 0, "errors": []}

    exchange_name = account["exchange"].lower()
    errors: List[str] = []
    cancelled = 0

    async def _run(exchange):
        nonlocal cancelled
        await _call(exchange.load_markets())
        params = _bybit_params() if exchange_name == "bybit" else {}
        for item in items:
            order_id = str(item.get("order_id", ""))
            try:
                sym = ccxt_symbol(item["symbol"], exchange_name)
                await _call(exchange.cancel_order(order_id, sym, params))
                cancelled += 1
            except Exception as e:
                errors.append(f"{order_id}: {_format_error(e)}")

    await _with_exchange(account, _run)
    return {"status": "ok", "cancelled": cancelled, "errors": errors}


# ---------------------------------------------------------------------------
# Read path — normalized positions & open orders (same shape as exchangeAPI.js)
# ---------------------------------------------------------------------------

_TERMINAL_BYBIT_ORDER_STATUSES = frozenset({"filled", "cancelled", "canceled", "rejected", "deactivated"})
_OPEN_BYBIT_ORDER_STATUSES = frozenset({"new", "partiallyfilled", "untriggered", "triggered", "created"})


def _format_usd(value: float) -> str:
    return f"${value:,.2f}"


def _base_symbol(symbol: str) -> str:
    return re.sub(r"USDT|PERP", "", symbol or "")


def _is_open_bybit_order(raw_order: dict) -> bool:
    status = str(raw_order.get("orderStatus") or "").lower()
    if not status:
        return False
    if status in _TERMINAL_BYBIT_ORDER_STATUSES:
        return False
    qty = float(raw_order.get("qty") or 0)
    cum_exec_qty = float(raw_order.get("cumExecQty") or 0)
    if qty > 0 and cum_exec_qty >= qty:
        return False
    return status in _OPEN_BYBIT_ORDER_STATUSES or qty > cum_exec_qty


def normalize_bybit_position(p: dict, account: dict) -> dict:
    side = "LONG" if p.get("side") == "Buy" else "SHORT"
    entry_price = float(p.get("avgPrice") or p.get("entryPrice") or 0)
    mark_price = float(p.get("markPrice") or 0)
    size = float(p.get("size") or 0)
    position_value = float(p.get("positionValue") or size * entry_price)
    unrealised_pnl = float(p.get("unrealisedPnl") or 0)
    leverage = float(p.get("leverage") or 1)
    pnl_pct = (unrealised_pnl / (position_value / leverage)) * 100 if position_value > 0 else 0.0
    symbol = str(p.get("symbol") or "")
    base_symbol = _base_symbol(symbol)
    credentials = account.get("credentials") or {}

    return {
        "id": f"{account['id']}-{symbol}-{side}",
        "symbol": f"{base_symbol}-PERP",
        "baseSymbol": base_symbol,
        "exchange": account["id"],
        "exchangeName": account.get("name", account["id"]),
        "side": side,
        "size": f"{size} {base_symbol}",
        "sizeVal": size,
        "value": _format_usd(position_value),
        "entry": entry_price,
        "mark": mark_price,
        "pnl": unrealised_pnl,
        "pnlPct": pnl_pct,
        "leverage": f"{leverage}x",
        "isDemo": bool(credentials.get("demo_trading")),
        "tp": float(p["takeProfit"]) if p.get("takeProfit") else None,
        "sl": float(p["stopLoss"]) if p.get("stopLoss") else None,
        "rawSymbol": symbol,
    }


def normalize_bybit_order(o: dict, account: dict) -> dict:
    side = "LONG" if o.get("side") == "Buy" else "SHORT"
    price = float(o.get("price") or 0)
    qty = float(o.get("qty") or 0)
    symbol = str(o.get("symbol") or "")
    base_symbol = _base_symbol(symbol)
    filled = float(o.get("cumExecQty") or 0)
    filled_pct = (filled / qty) * 100 if qty > 0 else 0.0
    reduce_only_raw = o.get("reduceOnly", o.get("closeOnTrigger"))
    reduce_only = (
        str(reduce_only_raw).lower() == "true"
        or reduce_only_raw is True
        or o.get("closeOnTrigger") is True
    )

    return {
        "id": f"{account['id']}-{o.get('orderId')}",
        "orderId": o.get("orderId"),
        "symbol": f"{base_symbol}-USDT",
        "baseSymbol": base_symbol,
        "exchange": account["id"],
        "exchangeName": account.get("name", account["id"]),
        "side": side,
        "type": o.get("orderType") or "Limit Order",
        "price": price,
        "amount": qty,
        "totalValue": price * qty,
        "filled": filled_pct,
        "statusText": o.get("orderStatus") or "Open",
        "rawSymbol": symbol,
        "reduceOnly": reduce_only,
    }


def normalize_hyperliquid_position(pos: dict, account: dict) -> dict:
    szi = float(pos.get("szi") or 0)
    side = "LONG" if szi > 0 else "SHORT"
    size = abs(szi)
    entry_price = float(pos.get("entryPx") or 0)
    mark_price = float(pos.get("markPx") or entry_price)
    unrealised_pnl = float(pos.get("unrealizedPnl") or 0)
    leverage_raw = pos.get("leverage")
    if isinstance(leverage_raw, dict):
        leverage = float(leverage_raw.get("value") or 1)
    else:
        leverage = float(leverage_raw or 1)
    position_value = size * entry_price
    pnl_pct = (unrealised_pnl / (position_value / leverage)) * 100 if position_value > 0 else 0.0
    coin = str(pos.get("coin") or "UNKNOWN")

    return {
        "id": f"{account['id']}-{coin}-{side}",
        "symbol": f"{coin}-PERP",
        "baseSymbol": coin,
        "exchange": account["id"],
        "exchangeName": account.get("name", account["id"]),
        "side": side,
        "size": f"{size} {coin}",
        "sizeVal": size,
        "value": _format_usd(position_value),
        "entry": entry_price,
        "mark": mark_price,
        "pnl": unrealised_pnl,
        "pnlPct": pnl_pct,
        "leverage": f"{leverage}x",
        "isDemo": False,
        "tp": None,
        "sl": None,
        "rawSymbol": coin,
    }


def normalize_hyperliquid_order(o: dict, account: dict) -> dict:
    side = "LONG" if o.get("side") == "B" else "SHORT"
    price = float(o.get("limitPx") or 0)
    qty = float(o.get("sz") or 0)
    coin = str(o.get("coin") or "")

    return {
        "id": f"{account['id']}-{o.get('oid')}",
        "orderId": str(o.get("oid")),
        "symbol": f"{coin}-USDT",
        "baseSymbol": coin,
        "exchange": account["id"],
        "exchangeName": account.get("name", account["id"]),
        "side": side,
        "type": "Limit Order",
        "price": price,
        "amount": qty,
        "totalValue": price * qty,
        "filled": 0,
        "statusText": "Open",
        "rawSymbol": coin,
        "reduceOnly": False,
    }


def _bybit_raw_position_from_ccxt(pos: dict) -> dict:
    info = pos.get("info") or {}
    if info.get("symbol"):
        return info
    symbol = bybit_linear_symbol(pos.get("symbol") or "")
    ccxt_side = str(pos.get("side") or "").lower()
    return {
        "symbol": symbol,
        "side": "Buy" if ccxt_side == "long" else "Sell",
        "size": str(abs(float(pos.get("contracts") or 0))),
        "avgPrice": str(pos.get("entryPrice") or 0),
        "markPrice": str(pos.get("markPrice") or 0),
        "positionValue": str(pos.get("notional") or 0),
        "unrealisedPnl": str(pos.get("unrealizedPnl") or 0),
        "leverage": str(pos.get("leverage") or 1),
        "takeProfit": info.get("takeProfit"),
        "stopLoss": info.get("stopLoss"),
    }


def _hyperliquid_raw_position_from_ccxt(pos: dict) -> dict:
    info = pos.get("info") or {}
    nested = info.get("position")
    if isinstance(nested, dict) and nested.get("coin"):
        return nested
    coin = normalize_coin(pos.get("symbol") or "")
    contracts = abs(float(pos.get("contracts") or 0))
    ccxt_side = str(pos.get("side") or "").lower()
    if ccxt_side == "short":
        signed_size = -contracts
    elif ccxt_side == "long":
        signed_size = contracts
    else:
        signed_size = float(pos.get("contracts") or 0)
    return {
        "coin": coin,
        "szi": str(signed_size),
        "entryPx": str(pos.get("entryPrice") or 0),
        "markPx": str(pos.get("markPrice") or pos.get("entryPrice") or 0),
        "unrealizedPnl": str(pos.get("unrealizedPnl") or 0),
        "leverage": {"value": pos.get("leverage") or 1},
    }


def _hyperliquid_raw_order_from_ccxt(order: dict) -> dict:
    info = order.get("info") or {}
    if info.get("oid") is not None:
        return info
    coin = normalize_coin(order.get("symbol") or "")
    ccxt_side = str(order.get("side") or "").lower()
    return {
        "oid": order.get("id"),
        "coin": coin,
        "side": "B" if ccxt_side == "buy" else "A",
        "limitPx": str(order.get("price") or 0),
        "sz": str(order.get("amount") or order.get("remaining") or 0),
    }


async def fetch_positions(account: dict) -> List[dict]:
    exchange_name = account["exchange"].lower()

    async def _run(exchange):
        await _call(exchange.load_markets())
        if exchange_name == "bybit":
            rows = await _call(
                exchange.fetch_positions(
                    symbols=None,
                    params={"category": "linear", "settleCoin": "USDT"},
                )
            )
            normalized = []
            for pos in rows or []:
                raw = _bybit_raw_position_from_ccxt(pos)
                if float(raw.get("size") or 0) <= 0:
                    continue
                normalized.append(normalize_bybit_position(raw, account))
            return normalized

        if exchange_name == "hyperliquid":
            rows = await _call(exchange.fetch_positions())
            normalized = []
            for pos in rows or []:
                raw = _hyperliquid_raw_position_from_ccxt(pos)
                if float(raw.get("szi") or 0) == 0:
                    continue
                normalized.append(normalize_hyperliquid_position(raw, account))
            return normalized

        raise ValueError(f"Unsupported exchange '{exchange_name}' for position reads.")

    return await _with_exchange(account, _run)


async def fetch_open_orders(account: dict) -> List[dict]:
    exchange_name = account["exchange"].lower()

    async def _run(exchange):
        await _call(exchange.load_markets())
        if exchange_name == "bybit":
            result = await _call(
                exchange.private_get_v5_order_realtime(
                    {"category": "linear", "settleCoin": "USDT", "limit": 50}
                )
            )
            rows = (result.get("result") or {}).get("list") or []
            return [
                normalize_bybit_order(row, account)
                for row in rows
                if _is_open_bybit_order(row)
            ]

        if exchange_name == "hyperliquid":
            rows = await _call(exchange.fetch_open_orders())
            return [
                normalize_hyperliquid_order(_hyperliquid_raw_order_from_ccxt(row), account)
                for row in rows or []
            ]

        raise ValueError(f"Unsupported exchange '{exchange_name}' for open-order reads.")

    return await _with_exchange(account, _run)
