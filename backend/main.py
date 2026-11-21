"""
FastAPI Server for Semi-Automated Trading System.
Provides REST APIs for dashboard configuration, logs, journaling, and trade intents,
and a WebSocket server for real-time updates of calculations, sessions, and executions.
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Body, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.core.logger import log, log_sync, init_log_db, get_logs, cleanup_logs
from backend.core.journal import init_journal_db, query_trades, get_trade, DEFAULT_DISPLAYABLE_FIELDS, cleanup_journal
from backend.core.session import SessionManager
from backend.core.data_feed import DataFeedManager
from backend.core.execution import ExecutionEngine, Intent, ManagedOrderCancelError
from backend.core.strategy import StrategyConfig, load_strategy_config, get_latest_levels
from backend.core.risk import RiskEngine
from backend.core import exchange_ops
from backend.core import accounts_config
from backend.core.exchange_monitor import exchange_monitor, exchange_monitor_loop, exchange_monitor_watchdog_loop

# Initialize App
app = FastAPI(title="Antigravity Trading System", version="1.0.0")

# Enable CORS for frontend dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def resilient_http_middleware(request: Request, call_next):
    try:
        if request.url.path.startswith("/api") and request.method != "OPTIONS":
            supplied_password = (
                request.headers.get("x-kinetic-password")
                or request.query_params.get("password")
                or ""
            )
            if supplied_password != BACKEND_PASSWORD:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)
    except Exception as e:
        log_sync(f"Unhandled HTTP exception on {request.url.path}: {e}", level="ERROR")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log_sync(f"Global exception handler caught error on {request.url.path}: {exc}", level="ERROR")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
ACCOUNTS_PATH = os.path.join(CONFIG_DIR, "accounts.json")
DASHBOARD_CONFIG_PATH = os.path.join(CONFIG_DIR, "dashboard.json")
BACKEND_PASSWORD = os.environ.get("KINETIC_BACKEND_PASSWORD", "yellow")

# Instantiate Core managers
session_manager = SessionManager()
data_feed_manager = DataFeedManager()
execution_engine = ExecutionEngine(session_manager, data_feed_manager)
background_tasks: List[asyncio.Task] = []
startup_self_check_errors: List[str] = []
DISABLE_LIVE_FEED = os.environ.get("KINETIC_DISABLE_LIVE_FEED", "").strip().lower() in {"1", "true", "yes"}

def create_supervised_task(coro, task_name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=task_name)
    background_tasks.append(task)

    def _on_done(done_task: asyncio.Task):
        try:
            _ = done_task.result()
        except asyncio.CancelledError:
            log_sync(f"Background task cancelled: {task_name}", level="WARNING")
        except Exception as e:
            log_sync(f"Background task crashed: {task_name} -> {e}", level="ERROR")

    task.add_done_callback(_on_done)
    return task

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await log("api", f"WebSocket client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            log_sync("api", f"WebSocket client disconnected. Active connections: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        msg_str = json.dumps(message)
        stale_connections = []
        for conn in list(self.active_connections):
            try:
                await conn.send_text(msg_str)
            except Exception:
                stale_connections.append(conn)
        for conn in stale_connections:
            self.disconnect(conn)

ws_manager = ConnectionManager()

# Background Tasks
async def ws_broadcaster_loop():
    """Periodically broadcasts latest prices, indicator levels, active orders, and sessions to clients."""
    await asyncio.sleep(5)  # Let services warm up
    
    while True:
        try:
            # Gather Strategy calculations
            levels_payload = {}
            for coin in ("BTC", "ETH", "SOL"):
                candles = data_feed_manager.get_candles(coin)
                if candles:
                    try:
                        strat_cfg = load_strategy_config(coin)
                        levels = get_latest_levels(candles, strat_cfg)
                        levels_payload[coin] = {
                            "price": candles[-1]["close"],
                            "evma": levels.evma,
                            "upper_stop": levels.upper_stop,
                            "lower_stop": levels.lower_stop,
                            "dist_pct": levels.dist * 100,
                            "atr_pct": levels.atr_pct * 100,
                            "norm_atr": levels.norm_atr
                        }
                    except Exception as e:
                        levels_payload[coin] = {"error": str(e)}
                else:
                    levels_payload[coin] = {"status": "Warming up"}
            
            # Form broadcast packet
            payload = {
                "type": "update",
                "timestamp": session_manager.get_uk_now().isoformat(),
                "session_status": session_manager.get_session_status(),
                "indicators": levels_payload,
                "active_orders": execution_engine.get_active_orders(),
                "exchange_monitor": exchange_monitor.get_snapshot(),
            }
            
            await ws_manager.broadcast(payload)
        except Exception as e:
            log_sync(f"WS Broadcaster Error: {e}", level="ERROR")
            
        await asyncio.sleep(1)  # Broadcast every second for real-time responsiveness

def run_startup_self_checks() -> List[str]:
    errors = []
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception as e:
        errors.append(f"Cannot ensure config directory: {e}")
    if not os.path.exists(ACCOUNTS_PATH):
        errors.append(f"Missing accounts config: {ACCOUNTS_PATH}")
    else:
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "accounts" not in data:
                errors.append("accounts.json does not contain top-level 'accounts' key")
        except Exception as e:
            errors.append(f"accounts.json parse error: {e}")
    data_dir = os.path.join(BASE_DIR, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
        test_path = os.path.join(data_dir, ".write_test.tmp")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
    except Exception as e:
        errors.append(f"Data dir is not writable: {e}")
    return errors

async def maintenance_loop():
    """Periodic retention cleanup to protect disk."""
    while True:
        try:
            await cleanup_logs(retention_days=30, vacuum=False)
            await cleanup_journal(retention_days=180, vacuum=False)
        except Exception as e:
            log_sync(f"Maintenance loop error: {e}", level="ERROR")
        await asyncio.sleep(6 * 3600)

async def data_feed_watchdog_loop():
    """
    Watch feed freshness and nudge reconnect when stale.
    """
    stale_after_sec = 180
    while True:
        try:
            health = data_feed_manager.get_health()
            ts = health.get("last_ws_message_ts")
            if ts:
                last = datetime.fromisoformat(ts)
                age = (datetime.now(timezone.utc) - last).total_seconds()
                if age > stale_after_sec:
                    log_sync(f"Data feed stale ({int(age)}s). Nudging reconnect.", level="WARNING")
                    await data_feed_manager.nudge_reconnect()
            elif data_feed_manager.running:
                # If running but no message for prolonged period, also nudge.
                await asyncio.sleep(stale_after_sec)
                await data_feed_manager.nudge_reconnect()
        except Exception as e:
            log_sync(f"Data feed watchdog error: {e}", level="ERROR")
        await asyncio.sleep(30)

async def active_order_snapshot_loop():
    """
    Periodically snapshot in-memory active orders even when no lifecycle transitions happen.
    """
    while True:
        try:
            await execution_engine.save_active_orders_snapshot()
        except Exception as e:
            log_sync(f"Active order snapshot loop error: {e}", level="ERROR")
        await asyncio.sleep(10)

async def session_execution_loop():
    """Cancel unfilled dragging orders once their UK session end_time passes."""
    while True:
        try:
            await execution_engine.cancel_dragging_for_ended_sessions()
        except Exception as e:
            log_sync(f"Session execution loop error: {e}", level="ERROR")
        await asyncio.sleep(15)

# Helper functions to load/save accounts.json
def load_accounts() -> List[dict]:
    if not os.path.exists(ACCOUNTS_PATH):
        return []
    try:
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log_sync(f"Failed reading accounts config at {ACCOUNTS_PATH}: {e}", level="ERROR")
        return []
        
    # Map 'risk' fields to expected standard
    accounts = data.get("accounts", [])
    for acc in accounts:
        if "risk" in acc:
            # Keep both properties updated
            acc["risk_config"] = {
                "type": acc["risk"].get("mode", acc["risk"].get("type")),
                "value": acc["risk"].get("value"),
                "min": acc["risk"].get("min"),
                "max": acc["risk"].get("max"),
                "step": acc["risk"].get("step"),
                "default": acc["risk"].get("default")
            }
    return accounts

def save_accounts(accounts: List[dict]):
    try:
        accounts_config.persist_accounts(accounts, ACCOUNTS_PATH)
    except OSError as e:
        log_sync(f"Failed writing accounts config at {ACCOUNTS_PATH}: {e}", level="ERROR")
        raise HTTPException(status_code=500, detail="Failed to persist accounts configuration")


def _commit_accounts_update(accounts: List[dict], *, previous_accounts: Optional[List[dict]] = None) -> List[dict]:
    errors = accounts_config.update_accounts_config(
        accounts,
        path=ACCOUNTS_PATH,
        execution_engine=execution_engine,
        previous_accounts=previous_accounts,
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    return accounts

def load_dashboard_config() -> dict:
    if not os.path.exists(DASHBOARD_CONFIG_PATH):
        return {"cloudflare_worker_url": ""}
    try:
        with open(DASHBOARD_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"cloudflare_worker_url": ""}
        return {"cloudflare_worker_url": data.get("cloudflare_worker_url", "")}
    except Exception as e:
        log_sync(f"Failed reading dashboard config at {DASHBOARD_CONFIG_PATH}: {e}", level="ERROR")
        return {"cloudflare_worker_url": ""}

def save_dashboard_config(config: dict):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(DASHBOARD_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"cloudflare_worker_url": config.get("cloudflare_worker_url", "")}, f, indent=4)
    except Exception as e:
        log_sync(f"Failed writing dashboard config at {DASHBOARD_CONFIG_PATH}: {e}", level="ERROR")
        raise HTTPException(status_code=500, detail="Failed to persist dashboard configuration")

# Models
class RiskConfigUpdate(BaseModel):
    account_id: str
    type: str  # fixed_pct, fixed_usd, adjustable_pct, adjustable_usd
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    default: Optional[float] = None

class AccountCreate(BaseModel):
    id: str
    name: str
    exchange: str
    enabled: bool = True
    credentials: Dict[str, Any]
    risk: Dict[str, Any]
    margin_pct: Optional[float] = 20
    fixed_tp_rr_by_coin: Optional[Dict[str, float]] = None
    max_leverage: Optional[float] = 25
    max_position_size_usd: Optional[float] = None
    max_positions: Optional[int] = 3

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    exchange: Optional[str] = None
    enabled: Optional[bool] = None
    credentials: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    margin_pct: Optional[float] = None
    fixed_tp_rr_by_coin: Optional[Dict[str, float]] = None
    max_leverage: Optional[float] = None
    max_position_size_usd: Optional[float] = None
    max_positions: Optional[int] = None

class AccountsConfigUpdate(BaseModel):
    accounts: List[Dict[str, Any]]

class IntentPayload(BaseModel):
    account_id: str
    coin: str
    direction: str  # LONG, SHORT
    rr: float
    risk_value: float

class IntentCancelPayload(BaseModel):
    order_id: str


class StrategyConfigUpdate(BaseModel):
    coin: str
    evma_length: int = Field(ge=1)
    atr_length: int = Field(ge=1)
    baseline_volatility: float = Field(gt=0)
    min_stop_pct: float = Field(ge=0)
    max_stop_pct: float = Field(ge=0)

class DashboardConfigUpdate(BaseModel):
    cloudflare_worker_url: str = ""

class ExchangeClosePayload(BaseModel):
    account_id: str
    symbol: str
    side: str
    size: float

class ExchangeCancelPayload(BaseModel):
    account_id: str
    symbol: str
    order_id: str

class ExchangeAmendPayload(BaseModel):
    account_id: str
    symbol: str
    order_id: str
    side: str
    price: float
    qty: float

class ExchangePlaceLimitPayload(BaseModel):
    account_id: str
    coin: str
    side: str
    price: float
    qty: float

class ExchangeTpslPayload(BaseModel):
    account_id: str
    symbol: str
    side: str
    tp: Optional[float] = None
    sl: Optional[float] = None

class ExchangeCancelBatchPayload(BaseModel):
    orders: List[Dict[str, str]]

def _get_account_or_404(account_id: str) -> dict:
    account = next((a for a in load_accounts() if a.get("id") == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.get("enabled", True):
        raise HTTPException(status_code=400, detail="Account is disabled")
    return account

# Event Callbacks
async def on_new_candle_close(coin: str):
    """Callback fired by DataFeedManager whenever a 1m candle closes."""
    # Notify execution engine to manage trailing order edits or fill triggers
    await execution_engine.on_candle_close(coin)

# Lifespan / Startup Hook
@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()
    def _loop_exception_handler(_loop, context):
        err = context.get("exception")
        msg = context.get("message", "Unknown async loop exception")
        if err:
            log_sync(f"Async loop exception: {msg} | {err}", level="ERROR")
        else:
            log_sync(f"Async loop exception: {msg}", level="ERROR")
    loop.set_exception_handler(_loop_exception_handler)

    global startup_self_check_errors
    startup_self_check_errors = run_startup_self_checks()
    if startup_self_check_errors:
        for err in startup_self_check_errors:
            log_sync(f"Startup self-check: {err}", level="ERROR")

    # 1. Initialize databases
    await init_log_db()
    await init_journal_db()
    await log("api", "SQLite databases initialized.")
    
    # 2. Pre-populate SessionManager traded session status
    await session_manager.pre_populate_from_journal()
    
    # 3. Load account configurations
    accounts = load_accounts()
    execution_engine.set_accounts_config(accounts)
    await log("api", f"Loaded {len(accounts)} account configurations.")
    await execution_engine.load_active_orders_snapshot()
    await execution_engine.cancel_dragging_for_ended_sessions()
    
    if DISABLE_LIVE_FEED:
        await data_feed_manager.warm_up_candles()
        await log("api", "Live feed startup disabled via KINETIC_DISABLE_LIVE_FEED=1.")
        create_supervised_task(execution_engine.poll_active_orders_lifecycle(), "poll_active_orders_lifecycle")
        create_supervised_task(ws_broadcaster_loop(), "ws_broadcaster_loop")
        create_supervised_task(maintenance_loop(), "maintenance_loop")
        create_supervised_task(active_order_snapshot_loop(), "active_order_snapshot_loop")
        create_supervised_task(exchange_monitor_loop(load_accounts), "exchange_monitor_loop")
        create_supervised_task(exchange_monitor_watchdog_loop(load_accounts), "exchange_monitor_watchdog_loop")
        create_supervised_task(session_execution_loop(), "session_execution_loop")
        await log("api", "Offline operational loop tasks initialized.")
    else:
        # 4. Start live data feeds
        data_feed_manager.register_callback("BTC", on_new_candle_close)
        data_feed_manager.register_callback("ETH", on_new_candle_close)
        data_feed_manager.register_callback("SOL", on_new_candle_close)
        try:
            await data_feed_manager.start()
            await log("api", "Data feed manager started.")
        except Exception as e:
            await log("api", f"Data feed manager failed to start: {e}", level="ERROR")

        # 5. Start execution engines poll loops
        create_supervised_task(execution_engine.poll_active_orders_lifecycle(), "poll_active_orders_lifecycle")
        create_supervised_task(ws_broadcaster_loop(), "ws_broadcaster_loop")
        create_supervised_task(maintenance_loop(), "maintenance_loop")
        create_supervised_task(data_feed_watchdog_loop(), "data_feed_watchdog_loop")
        create_supervised_task(active_order_snapshot_loop(), "active_order_snapshot_loop")
        create_supervised_task(exchange_monitor_loop(load_accounts), "exchange_monitor_loop")
        create_supervised_task(exchange_monitor_watchdog_loop(load_accounts), "exchange_monitor_watchdog_loop")
        create_supervised_task(session_execution_loop(), "session_execution_loop")
        await log("api", "Operational loop tasks successfully initialized.")

@app.on_event("shutdown")
async def shutdown_event():
    try:
        await execution_engine.save_active_orders_snapshot()
    except Exception as e:
        log_sync(f"Shutdown snapshot save failed: {e}", level="ERROR")
    for task in list(background_tasks):
        if not task.done():
            task.cancel()
    await data_feed_manager.stop()
    await log("api", "Trading services shut down successfully.")

# REST APIs

@app.get("/api/accounts")
def get_accounts():
    """Lists all configured trading accounts, including credentials shapes and risk limits."""
    return load_accounts()

@app.get("/api/dashboard/config")
def get_dashboard_config():
    """Returns frontend runtime config stored on the backend."""
    return load_dashboard_config()

@app.post("/api/dashboard/config")
def update_dashboard_config(update: DashboardConfigUpdate):
    """Updates frontend runtime config stored on the backend."""
    config = {"cloudflare_worker_url": update.cloudflare_worker_url.strip()}
    save_dashboard_config(config)
    return {"status": "success", "config": config}

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "antigravity-trading-backend"}

@app.get("/readyz")
def readyz():
    feed_health = data_feed_manager.get_health()
    accounts_loaded = len(load_accounts())
    checks = {
        "startup_self_checks_ok": len(startup_self_check_errors) == 0,
        "feed_running": bool(feed_health.get("running")),
        "feed_ws_connected": bool(feed_health.get("ws_connected")),
        "accounts_loaded": accounts_loaded > 0,
    }
    ready = all(checks.values())
    return {
        "ready": ready,
        "checks": checks,
        "startup_self_check_errors": startup_self_check_errors,
        "feed": feed_health,
        "accounts_count": accounts_loaded,
    }

@app.post("/api/accounts/reload")
def reload_accounts_config():
    """Re-read accounts.json from disk and hot-reload the execution engine."""
    accounts = load_accounts()
    errors = accounts_config.validate_accounts(accounts)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    old_ids = {a.get("id") for a in execution_engine.accounts_config if a.get("id")}
    new_ids = {a.get("id") for a in accounts if a.get("id")}
    active_ids = {o.account_id for o in execution_engine.active_orders}
    errors = accounts_config.check_removals_safe(old_ids - new_ids, active_ids)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    accounts_config.apply_accounts_runtime(accounts, execution_engine)
    log_sync(f"Hot-reloaded {len(accounts)} account configuration(s) from disk.")
    return {"status": "success", "accounts_count": len(accounts)}

@app.put("/api/accounts/config")
def replace_accounts_config(payload: AccountsConfigUpdate):
    """Replace the full accounts list with validation and hot-reload (no restart)."""
    previous = load_accounts()
    accounts = _commit_accounts_update(payload.accounts, previous_accounts=previous)
    log_sync(f"Replaced accounts configuration ({len(accounts)} account(s)).")
    return {"status": "success", "accounts_count": len(accounts)}

@app.post("/api/accounts")
def create_account(payload: AccountCreate):
    """Add a new trading account, persist, and hot-reload."""
    previous = load_accounts()
    if any(a.get("id") == payload.id for a in previous):
        raise HTTPException(status_code=400, detail=f"Account id already exists: {payload.id}")

    accounts = list(previous)
    accounts.append(payload.model_dump(exclude_none=True))
    _commit_accounts_update(accounts, previous_accounts=previous)
    created = next(a for a in accounts if a.get("id") == payload.id)
    log_sync(f"Created account configuration: {payload.id}")
    return {"status": "success", "account": created}

@app.put("/api/accounts/{account_id}")
def update_account(account_id: str, payload: AccountUpdate):
    """Patch one account, persist, and hot-reload."""
    previous = load_accounts()
    index = next((i for i, a in enumerate(previous) if a.get("id") == account_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Account not found")

    accounts = list(previous)
    patch = payload.model_dump(exclude_none=True)
    accounts[index] = accounts_config.merge_account(accounts[index], patch)
    _commit_accounts_update(accounts, previous_accounts=previous)
    updated = accounts[index]
    log_sync(f"Updated account configuration: {account_id}")
    return {"status": "success", "account": updated}

@app.post("/api/accounts/risk")
def update_account_risk(update: RiskConfigUpdate):
    """Updates risk limits and models for a given trading account in accounts.json."""
    previous = load_accounts()
    acc = next((a for a in previous if a["id"] == update.account_id), None)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")

    accounts = list(previous)
    index = next(i for i, a in enumerate(accounts) if a["id"] == update.account_id)
    accounts[index] = accounts_config.merge_account(accounts[index], {
        "risk": {
            "mode": update.type,
            "value": update.value,
            "min": update.min,
            "max": update.max,
            "step": update.step,
            "default": update.default,
        }
    })
    _commit_accounts_update(accounts, previous_accounts=previous)
    updated = accounts[index]
    log_sync(f"Updated risk configuration for account {update.account_id}")
    return {"status": "success", "account": updated}

@app.post("/api/intent")
async def post_intent(payload: IntentPayload):
    """Receives a user trade placement intent, validates limits, and starts EVMA order dragging."""
    coin = payload.coin.upper()
    direction = payload.direction.upper()
    if coin not in {"BTC", "ETH", "SOL"}:
        raise HTTPException(status_code=400, detail="Unsupported coin. Supported coins: BTC, ETH, SOL.")
    if direction not in {"LONG", "SHORT"}:
        raise HTTPException(status_code=400, detail="Unsupported direction. Use LONG or SHORT.")

    accounts = load_accounts()
    account = next((a for a in accounts if a.get("id") == payload.account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.get("enabled", True):
        raise HTTPException(status_code=400, detail="Account is disabled")

    fixed_rr_by_coin = account.get("fixed_tp_rr_by_coin") or {}
    rr = float(fixed_rr_by_coin.get(coin, payload.rr))
    if rr <= 0 or rr > 10:
        raise HTTPException(status_code=400, detail="Invalid R:R value")

    risk_value = float(payload.risk_value)
    if not RiskEngine.validate_risk_input(account, risk_value):
        raise HTTPException(status_code=400, detail="Risk value is outside this account's configured risk limits")

    # Check if session is active / accepting intents
    if not session_manager.is_accepting_intents():
        raise HTTPException(
            status_code=400, 
            detail="Outside trading session hours. Intents are accepted up to 15m before and during session."
        )
        
    current_sess = session_manager.get_current_session() or session_manager.get_next_session()
    if not current_sess:
        raise HTTPException(status_code=400, detail="No active session found.")
        
    # Check if this coin is already being managed (dragging or filled) for this account
    active_for_coin = [o for o in execution_engine.active_orders if o.account_id == payload.account_id and o.coin == coin]
    if active_for_coin:
        raise HTTPException(
            status_code=400, 
            detail=f"Limit exceeded: Account already has an active trade/position for {coin}."
        )
        
    # Check if the account has reached its total concurrent position limit
    max_positions = int(account.get("max_positions", 3))
    active_total = [o for o in execution_engine.active_orders if o.account_id == payload.account_id]
    if len(active_total) >= max_positions:
        raise HTTPException(
            status_code=400, 
            detail=f"Limit exceeded: Account has reached its limit of {max_positions} active position(s)."
        )
        
    # Create intent and pass to engine
    intent = Intent(
        account_id=payload.account_id,
        coin=coin,
        direction=direction,
        rr=rr,
        risk_value=risk_value,
        session_id=current_sess.id,
        session_type=current_sess.type
    )
    
    # Asynchronously dispatch execution dragging
    create_supervised_task(
        execution_engine.start_intent(intent),
        f"start_intent:{intent.account_id}:{intent.coin}"
    )
    
    return {"status": "intent_accepted", "session_id": current_sess.id}

@app.post("/api/intent/cancel")
async def post_cancel_intent(payload: IntentCancelPayload):
    """
    Cancels an active drag limiting order if it hasn't been filled or partially filled.
    """
    try:
        success = await execution_engine.cancel_managed_order(payload.order_id)
    except ManagedOrderCancelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not success:
        raise HTTPException(
            status_code=409,
            detail="Failed to cancel dragging order. It may have been filled, partially filled, or not found."
        )
    return {"status": "success", "message": f"Drag limiting order {payload.order_id} cancelled."}

@app.get("/api/session")
def get_session():
    """Returns the current trading session and intent statuses."""
    return session_manager.get_session_status()

@app.get("/api/journal/query")
async def get_journal(
    coin: Optional[str] = None,
    direction: Optional[str] = None,
    exchange: Optional[str] = None,
    account_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    fields: Optional[str] = Query(None)  # Comma separated list of fields
):
    """
    Retrieves filtered logs from the trade journaling database.
    Allows column-level fields filter so frontend can request custom display layouts.
    """
    filters = {
        "coin": coin,
        "direction": direction,
        "exchange": exchange,
        "account_id": account_id,
        "date_from": date_from,
        "date_to": date_to,
        "status": status
    }
    
    selected_fields = fields.split(",") if fields else DEFAULT_DISPLAYABLE_FIELDS
    trades = await query_trades(filters=filters, fields=selected_fields)
    return trades

@app.get("/api/logs")
async def get_system_logs(date: Optional[str] = None):
    """Retrieves SQLite-backed operational logs for a UK calendar date."""
    logs = await get_logs(date)
    return logs

@app.get("/api/execution/status")
async def get_execution_status():
    """Active managed orders snapshot for ops tooling and pre-flight checks."""
    orders = execution_engine.get_active_orders()
    return {
        "active_managed_orders": len(orders),
        "active_orders": orders,
    }

@app.post("/api/exchange/close-position")
async def api_exchange_close_position(payload: ExchangeClosePayload):
    account = _get_account_or_404(payload.account_id)
    try:
        return await exchange_ops.close_position(
            account, payload.symbol, payload.side, payload.size
        )
    except Exception as e:
        log_sync(f"Exchange close failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/cancel-order")
async def api_exchange_cancel_order(payload: ExchangeCancelPayload):
    account = _get_account_or_404(payload.account_id)
    try:
        return await exchange_ops.cancel_order(account, payload.symbol, payload.order_id)
    except Exception as e:
        log_sync(f"Exchange cancel failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/amend-order")
async def api_exchange_amend_order(payload: ExchangeAmendPayload):
    account = _get_account_or_404(payload.account_id)
    try:
        return await exchange_ops.amend_order(
            account,
            payload.symbol,
            payload.order_id,
            payload.side,
            payload.price,
            payload.qty,
        )
    except Exception as e:
        log_sync(f"Exchange amend failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/place-limit")
async def api_exchange_place_limit(payload: ExchangePlaceLimitPayload):
    account = _get_account_or_404(payload.account_id)
    try:
        return await exchange_ops.place_limit_order(
            account, payload.coin, payload.side, payload.price, payload.qty
        )
    except Exception as e:
        log_sync(f"Exchange place limit failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/set-tpsl")
async def api_exchange_set_tpsl(payload: ExchangeTpslPayload):
    account = _get_account_or_404(payload.account_id)
    if payload.tp is None and payload.sl is None:
        raise HTTPException(status_code=400, detail="At least one of tp or sl is required")
    try:
        return await exchange_ops.set_position_tpsl(
            account, payload.symbol, payload.side, payload.tp, payload.sl
        )
    except Exception as e:
        log_sync(f"Exchange TP/SL failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.get("/api/exchange/positions")
async def api_exchange_positions(account_id: str = Query(...)):
    account = _get_account_or_404(account_id)
    try:
        return await exchange_ops.fetch_positions(account)
    except Exception as e:
        log_sync(f"Exchange positions read failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.get("/api/exchange/open-orders")
async def api_exchange_open_orders(account_id: str = Query(...)):
    account = _get_account_or_404(account_id)
    try:
        return await exchange_ops.fetch_open_orders(account)
    except Exception as e:
        log_sync(f"Exchange open-orders read failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/refresh-monitor")
async def api_exchange_refresh_monitor():
    try:
        await exchange_monitor.poll_once(load_accounts())
        return exchange_monitor.get_snapshot()
    except Exception as e:
        log_sync(f"Exchange monitor refresh failed: {e}", level="ERROR")
        raise HTTPException(status_code=400, detail=exchange_ops._format_error(e))

@app.post("/api/exchange/cancel-batch")
async def api_exchange_cancel_batch(payload: ExchangeCancelBatchPayload):
    if not payload.orders:
        return {"status": "ok", "cancelled": 0, "errors": []}
    by_account: Dict[str, List[dict]] = {}
    for item in payload.orders:
        aid = item.get("account_id")
        if not aid:
            continue
        by_account.setdefault(aid, []).append(item)
    total_cancelled = 0
    all_errors: List[str] = []
    for account_id, items in by_account.items():
        try:
            account = _get_account_or_404(account_id)
            result = await exchange_ops.cancel_orders_batch(account, items)
            total_cancelled += result.get("cancelled", 0)
            all_errors.extend(result.get("errors", []))
        except HTTPException:
            raise
        except Exception as e:
            all_errors.append(f"{account_id}: {e}")
    return {"status": "ok", "cancelled": total_cancelled, "errors": all_errors}

@app.get("/api/strategy/config")
def get_strategy_settings(coin: str):
    """Retrieves strategy EVMA and ATR configurations for a given coin ticker."""
    try:
        cfg = load_strategy_config(coin)
        return cfg.dict()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/strategy/config")
def update_strategy_settings(update: StrategyConfigUpdate):
    """Updates strategy settings for a coin and persists them to its strategy config JSON."""
    coin_lower = update.coin.strip().lower()
    if coin_lower not in {"btc", "eth", "sol"}:
        raise HTTPException(status_code=400, detail="Coin must be BTC, ETH, or SOL")
    if update.min_stop_pct > update.max_stop_pct:
        raise HTTPException(status_code=422, detail="Minimum stop distance cannot exceed maximum stop distance")

    config_path = os.path.join(CONFIG_DIR, f"strategy_{coin_lower}.json")
    
    if not os.path.exists(config_path):
        raise HTTPException(status_code=404, detail=f"Strategy config file strategy_{coin_lower}.json not found")
        
    # Read existing config to preserve symbols/R:R lists
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        
    cfg["evma_length"] = update.evma_length
    cfg["atr_length"] = update.atr_length
    cfg["baseline_volatility"] = update.baseline_volatility
    cfg["min_stop_pct"] = update.min_stop_pct
    cfg["max_stop_pct"] = update.max_stop_pct

    # Validate the complete configuration before persisting changes while
    # retaining any future or exchange-specific fields in the JSON file.
    StrategyConfig.model_validate(cfg)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
        
    log_sync(f"Updated and saved strategy configuration for {update.coin}")
    return {"status": "success", "config": cfg}

# WebSocket Endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if websocket.query_params.get("password", "") != BACKEND_PASSWORD:
        await websocket.close(code=1008)
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            # WebSocket keeps connected, wait for potential control inputs or keepalive
            data = await websocket.receive_text()
            # Ignore or parse control messages
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as ws_err:
        log_sync(f"WebSocket error on connection: {ws_err}", level="ERROR")
        ws_manager.disconnect(websocket)

# Sync logger helper
def log_sync(message: str, level: str = "INFO"):
    from backend.core.logger import log_sync as l_sync
    l_sync(message, level=level)
