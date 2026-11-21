"""
WebSocket and REST Data Feed Manager for Bybit linear perpetual market data.
Maintains live price feeds, aggregates 1m candles to 3m/5m, and fires callbacks.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
import aiohttp
import websockets
from backend.core.logger import log

BYBIT_REST_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

COIN_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT"
}
REVERSE_COIN_MAP = {v: k for k, v in COIN_MAP.items()}

class DataFeedManager:
    def __init__(self):
        self.candles_1m = {coin: [] for coin in COIN_MAP}  # Stores 1m normalized candles
        self.callbacks = {coin: [] for coin in COIN_MAP}
        self.running = False
        self.feed_mode = "ws"
        self.ws = None
        self.heartbeat_task = None
        self.session = None
        self.ws_connected = False
        self.rest_fallback_started = False
        self.rest_poll_interval_sec = 15
        self.last_ws_message_ts = None
        self.last_closed_candle_ts = {coin: None for coin in COIN_MAP}

    def register_callback(self, coin: str, callback):
        """Register a strategy callback to be invoked on aggregated candle closes."""
        coin = coin.upper()
        if coin in self.callbacks:
            self.callbacks[coin].append(callback)
            log_sync(f"DataFeedManager: Registered callback for {coin}")

    def get_candles(self, coin: str, timeframe: str = "5m") -> list[dict]:
        """
        Returns normalized and aggregated candles for a coin and timeframe (1m, 3m, 5m).

        Only fully closed 1m candles are used as input, and aggregated timeframes only
        include buckets whose closing boundary has completed. This keeps strategy math
        aligned to completed 5-minute candles instead of the current in-progress minute.
        """
        coin = coin.upper()
        candles = self.candles_1m.get(coin, [])
        
        if not candles:
            return []

        closed_candles = [c for c in candles if c.get("is_closed")]
        if not closed_candles:
            return []
            
        if timeframe == "1m":
            return closed_candles
        
        timeframe_min = int(timeframe.replace("m", ""))
        bucket_size = timeframe_min * 60
        
        buckets = {}
        for c in closed_candles:
            bucket_time = (c["time"] // bucket_size) * bucket_size
            if bucket_time not in buckets:
                buckets[bucket_time] = {
                    "time": bucket_time,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                    "is_closed": c["is_closed"]
                }
            else:
                buckets[bucket_time]["high"] = max(buckets[bucket_time]["high"], c["high"])
                buckets[bucket_time]["low"] = min(buckets[bucket_time]["low"], c["low"])
                buckets[bucket_time]["close"] = c["close"]
                buckets[bucket_time]["volume"] += c["volume"]
                if c["is_closed"]:
                    buckets[bucket_time]["is_closed"] = True

        # Drop the latest bucket unless its closing boundary has completed.
        latest_close_ts = int(closed_candles[-1]["time"]) + 60
        if latest_close_ts % bucket_size != 0:
            incomplete_bucket = (latest_close_ts // bucket_size) * bucket_size
            buckets.pop(incomplete_bucket, None)

        return sorted(buckets.values(), key=lambda x: x["time"])

    async def warm_up_candles(self):
        """Fetches historical 1m klines from Bybit linear REST on startup."""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=12)
            self.session = aiohttp.ClientSession(timeout=timeout)
        for coin, symbol in COIN_MAP.items():
            params = {
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "limit": "200"
            }
            try:
                await log("data_feed", f"Warming up candles for {coin} from REST...")
                async with self.session.get(BYBIT_REST_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_list = data.get("result", {}).get("list", [])
                        
                        # Bybit returns klines in reverse chronological order
                        normalized = []
                        for item in reversed(raw_list):
                            # Start time is in millisecond string
                            start_time = int(item[0]) // 1000
                            normalized.append({
                                "time": start_time,
                                "open": float(item[1]),
                                "high": float(item[2]),
                                "low": float(item[3]),
                                "close": float(item[4]),
                                "volume": float(item[5]),
                                "is_closed": True
                            })
                        self.candles_1m[coin] = normalized
                        await log("data_feed", f"Successfully warmed up {len(normalized)} candles for {coin}.")
                    else:
                        await log("data_feed", f"Failed to warm up candles for {coin}: HTTP {resp.status}", level="ERROR")
            except Exception as e:
                await log("data_feed", f"Error during kline warm up for {coin}: {e}", level="ERROR")

    async def _fetch_rest_candles(self, coin: str) -> list[dict]:
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=12)
            self.session = aiohttp.ClientSession(timeout=timeout)

        symbol = COIN_MAP[coin]
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "1",
            "limit": "200",
        }
        async with self.session.get(BYBIT_REST_URL, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            data = await resp.json()
            raw_list = data.get("result", {}).get("list", [])
            normalized = []
            for item in reversed(raw_list):
                start_time = int(item[0]) // 1000
                normalized.append({
                    "time": start_time,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "is_closed": True,
                })
            return normalized

    async def _sync_rest_candles(self, coin: str):
        normalized = await self._fetch_rest_candles(coin)
        if not normalized:
            return

        existing = self.candles_1m.get(coin, [])
        existing_by_time = {c["time"]: c for c in existing}
        updated = list(existing)
        seen_times = set(existing_by_time)

        for candle in normalized:
            candle_time = candle["time"]
            if candle_time in existing_by_time:
                existing_by_time[candle_time].update(candle)
            else:
                updated.append(candle)
                seen_times.add(candle_time)

        updated.sort(key=lambda c: c["time"])
        if len(updated) > 1440:
            updated = updated[-1440:]
        self.candles_1m[coin] = updated

        if len(normalized) < 2:
            return

        latest_closed = normalized[-2]
        latest_closed_time = latest_closed["time"]
        last_processed = self.last_closed_candle_ts.get(coin)
        if last_processed == str(latest_closed_time):
            return

        self.last_closed_candle_ts[coin] = str(latest_closed_time)
        await log("data_feed", f"REST fallback new 1m candle closed for {coin} @ {latest_closed['close']}", level="DEBUG")
        for callback in self.callbacks[coin]:
            try:
                asyncio.create_task(callback(coin))
            except Exception as cb_err:
                await log("data_feed", f"Callback error for {coin}: {cb_err}", level="ERROR")

    async def _rest_fallback_loop(self):
        if self.rest_fallback_started:
            return
        self.rest_fallback_started = True
        self.feed_mode = "rest"
        self.ws_connected = False
        await log("data_feed", "Switching to REST fallback feed for Bybit klines.", level="WARNING")

        try:
            while self.running and self.feed_mode == "rest":
                try:
                    for coin in COIN_MAP:
                        await self._sync_rest_candles(coin)
                except Exception as e:
                    await log("data_feed", f"REST fallback error: {e}", level="ERROR")
                await asyncio.sleep(self.rest_poll_interval_sec)
        finally:
            self.rest_fallback_started = False

    async def _bybit_heartbeat(self, ws):
        """Send Bybit's application-level ping to keep the public stream alive."""
        try:
            while self.running and self.ws is ws:
                await asyncio.sleep(20)
                await ws.send(json.dumps({"op": "ping"}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await log("data_feed", f"Bybit heartbeat failed: {e}", level="WARNING")
            try:
                await ws.close()
            except Exception:
                pass

    async def _stop_heartbeat(self):
        task = self.heartbeat_task
        self.heartbeat_task = None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def handle_websocket_messages(self):
        """Connects and listens to Bybit public websocket kline channels."""
        backoff = 1
        self.running = True
        
        while self.running:
            try:
                await log("data_feed", f"Connecting to Bybit WS: {BYBIT_WS_URL}")
                async with websockets.connect(BYBIT_WS_URL) as ws:
                    self.ws = ws
                    self.feed_mode = "ws"
                    self.ws_connected = True
                    backoff = 1  # Reset backoff on successful connection
                    
                    # Subscribe to 1m klines for all coins
                    sub_args = [f"kline.1.{symbol}" for symbol in COIN_MAP.values()]
                    sub_msg = {
                        "op": "subscribe",
                        "args": sub_args
                    }
                    await ws.send(json.dumps(sub_msg))
                    await log("data_feed", f"Subscribed to WS channels: {sub_args}")
                    self.heartbeat_task = asyncio.create_task(self._bybit_heartbeat(ws))
                    
                    while self.running:
                        msg_str = await ws.recv()
                        self.last_ws_message_ts = datetime.now(timezone.utc).isoformat()
                        msg = json.loads(msg_str)
                        
                        # Process kline update
                        topic = msg.get("topic", "")
                        if "kline" in topic:
                            data_list = msg.get("data", [])
                            symbol = topic.split(".")[-1]
                            coin = REVERSE_COIN_MAP.get(symbol)
                            
                            if not coin or not data_list:
                                continue
                                
                            for item in data_list:
                                start_time = int(item["start"]) // 1000
                                is_closed = item["confirm"]
                                
                                candle = {
                                    "time": start_time,
                                    "open": float(item["open"]),
                                    "high": float(item["high"]),
                                    "low": float(item["low"]),
                                    "close": float(item["close"]),
                                    "volume": float(item["volume"]),
                                    "is_closed": is_closed
                                }
                                
                                # Update in-memory candles
                                existing = self.candles_1m[coin]
                                if existing and existing[-1]["time"] == start_time:
                                    # Update current candle
                                    existing[-1] = candle
                                else:
                                    # Append new candle and cap at 1440 entries
                                    existing.append(candle)
                                    if len(existing) > 1440:
                                        existing.pop(0)
                                        
                                # If the 1m candle closed, aggregate it and fire callbacks
                                if is_closed:
                                    self.last_closed_candle_ts[coin] = datetime.now(timezone.utc).isoformat()
                                    await log("data_feed", f"New 1m candle closed for {coin} @ {candle['close']}", level="DEBUG")
                                    
                                    # Trigger callbacks for registered timeframe (default 5m)
                                    # Check if the aggregated timeframe candle just closed
                                    # Let's say strategy runs on 5m, so we check if start_time % 300 == 240 (the last minute of the 5m interval)
                                    # i.e., at start of minute 4 (time matches 4*60 offset), so the candle closes when it rolls over.
                                    # Let's invoke strategy callbacks on every 1m close, and let the strategy look at closed 5m candles!
                                    # This is extremely clean and reliable.
                                    for callback in self.callbacks[coin]:
                                        try:
                                            # Execute callback asynchronously
                                            asyncio.create_task(callback(coin))
                                        except Exception as cb_err:
                                            await log("data_feed", f"Callback error for {coin}: {cb_err}", level="ERROR")
                                            
            except websockets.exceptions.ConnectionClosed as e:
                await self._stop_heartbeat()
                self.ws_connected = False
                await log(
                    "data_feed",
                    f"Websocket connection lost (code={e.code}, reason={e.reason}).",
                    level="WARNING",
                )
                if self.running and not self.rest_fallback_started:
                    asyncio.create_task(self._rest_fallback_loop())
                if self.running:
                    await log("data_feed", f"Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(60, backoff * 2)
                    continue
            except Exception as e:
                await self._stop_heartbeat()
                self.ws_connected = False
                await log("data_feed", f"WebSocket error: {e}", level="ERROR")
                if self.running and not self.rest_fallback_started:
                    asyncio.create_task(self._rest_fallback_loop())
                if self.running:
                    await log("data_feed", f"Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(60, backoff * 2)
                    continue
                
            if self.running and self.feed_mode == "ws":
                await log("data_feed", f"Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def start(self):
        """Starts the data feed manager (warm-up historical data + run WebSocket)."""
        await self.warm_up_candles()
        if self.feed_mode == "rest":
            asyncio.create_task(self._rest_fallback_loop())
        else:
            asyncio.create_task(self.handle_websocket_messages())

    async def stop(self):
        """Stops the data feed manager."""
        self.running = False
        await self._stop_heartbeat()
        if self.ws:
            await self.ws.close()
        self.ws_connected = False
        if self.session:
            await self.session.close()
        await log("data_feed", "DataFeedManager stopped.")

    async def nudge_reconnect(self):
        """Force WS reconnect on staleness without stopping the process."""
        if self.feed_mode == "rest":
            return
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        self.ws_connected = False

    def get_health(self) -> dict:
        now_ts = datetime.now(timezone.utc).isoformat()
        return {
            "running": self.running,
            "ws_connected": self.ws_connected,
            "last_ws_message_ts": self.last_ws_message_ts,
            "last_closed_candle_ts": self.last_closed_candle_ts,
            "feed_mode": self.feed_mode,
            "now_ts": now_ts,
        }

# Sync helper for callbacks inside worker threads
def log_sync(message: str, level: str = "INFO"):
    from backend.core.logger import log_sync as l_sync
    l_sync(message, level=level)
