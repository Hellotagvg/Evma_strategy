"""
Async ccxt exchange instances for dashboard trading actions.
"""

from __future__ import annotations

import ccxt.async_support as ccxt_async

PLACEHOLDER_MARKERS = ("PLACEHOLDER", "YOUR_", "REPLACE_ME", "CHANGEME")


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    upper = str(value).upper()
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


def create_async_exchange(account_config: dict):
    credentials = account_config.get("credentials") or {}
    api_key = credentials.get("api_key", "")
    api_secret = credentials.get("secret") or credentials.get("api_secret") or ""
    exchange_name = (account_config.get("exchange") or "").lower()
    account_id = account_config.get("id", "unknown")

    if exchange_name == "bybit":
        if _is_placeholder(api_key) or _is_placeholder(api_secret):
            raise ValueError(f"Account '{account_id}' is missing valid Bybit API credentials.")
        exchange = ccxt_async.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "linear"},
            }
        )
        if credentials.get("demo_trading"):
            exchange.enable_demo_trading(True)
            exchange.options["enableDemoTrading"] = True
        elif credentials.get("testnet"):
            exchange.set_sandbox_mode(True)
        return exchange

    if exchange_name == "hyperliquid":
        wallet = credentials.get("wallet_address") or api_key
        if _is_placeholder(wallet) or _is_placeholder(api_secret):
            raise ValueError(f"Account '{account_id}' is missing valid Hyperliquid wallet/private key.")
        return ccxt_async.hyperliquid(
            {
                "apiKey": wallet,
                "secret": api_secret,
                "walletAddress": wallet,
            }
        )

    raise ValueError(f"Unsupported exchange '{exchange_name}' for account '{account_id}'.")


def normalize_coin(symbol: str) -> str:
    s = (symbol or "").upper()
    for token in ("USDT", "USDC", "-PERP", "PERP", "/", ":"):
        s = s.replace(token, "")
    return s.strip() or symbol


def ccxt_symbol(coin_or_symbol: str, exchange_name: str) -> str:
    coin = normalize_coin(coin_or_symbol)
    if exchange_name == "hyperliquid":
        return f"{coin}/USDC:USDC"
    if exchange_name == "bybit":
        return f"{coin}/USDT:USDT"
    return f"{coin}/USDT"


def bybit_linear_symbol(coin_or_symbol: str) -> str:
    coin = normalize_coin(coin_or_symbol)
    return f"{coin}USDT"
