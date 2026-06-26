"""
Trade Journaling Module using SQLite.
Records trading data and performance metrics during and after trade execution.
Supports flexible query filtering and column selection.
"""

import os
import uuid
from datetime import datetime, timezone, timedelta
import aiosqlite
from backend.core.logger import log

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "journal.db")

# Columns list for reference and validation
ALL_COLUMNS = [
    "id", "account_id", "exchange", "coin", "direction", "session_id", "session_type",
    "intent_time", "open_time", "close_time", "entry_price", "evma_at_entry", "sl_price",
    "tp_price", "planned_rr", "position_size_usd", "leverage_used", "slippage",
    "time_in_trade_seconds", "exit_price", "exit_reason", "realized_pnl_usd",
    "realized_pnl_pct", "fees_usd", "atr_at_entry", "sl_distance_pct", "status",
    "created_at", "updated_at"
]

DEFAULT_DISPLAYABLE_FIELDS = [
    "id", "coin", "direction", "session_type", "open_time", "close_time", "entry_price",
    "sl_price", "tp_price", "planned_rr", "position_size_usd", "exit_price", "exit_reason",
    "realized_pnl_usd", "realized_pnl_pct", "fees_usd", "time_in_trade_seconds"
]

async def init_journal_db():
    """Initializes the SQLite database and creates the trades table."""
    os.makedirs(DATA_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                account_id TEXT,
                exchange TEXT,
                coin TEXT,
                direction TEXT,
                session_id TEXT,
                session_type TEXT,
                intent_time TEXT,
                open_time TEXT,
                close_time TEXT,
                entry_price REAL,
                evma_at_entry REAL,
                sl_price REAL,
                tp_price REAL,
                planned_rr REAL,
                position_size_usd REAL,
                leverage_used REAL,
                slippage REAL,
                time_in_trade_seconds INTEGER,
                exit_price REAL,
                exit_reason TEXT,
                realized_pnl_usd REAL,
                realized_pnl_pct REAL,
                fees_usd REAL,
                atr_at_entry REAL,
                sl_distance_pct REAL,
                status TEXT DEFAULT 'open',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await db.commit()
    await log("journal", "Journal database initialized successfully.")

async def open_trade(trade_data: dict) -> str:
    """
    Creates a new journal entry when a trade is opened or partially opened.
    Generates a unique trade ID if not provided.
    """
    trade_id = trade_data.get("id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    # Defaults and initial values
    data = {
        "id": trade_id,
        "account_id": trade_data.get("account_id"),
        "exchange": trade_data.get("exchange"),
        "coin": trade_data.get("coin"),
        "direction": trade_data.get("direction"),
        "session_id": trade_data.get("session_id"),
        "session_type": trade_data.get("session_type"),
        "intent_time": trade_data.get("intent_time") or now,
        "open_time": trade_data.get("open_time") or now,
        "close_time": None,
        "entry_price": float(trade_data.get("entry_price") or 0.0),
        "evma_at_entry": float(trade_data.get("evma_at_entry") or 0.0),
        "sl_price": float(trade_data.get("sl_price") or 0.0),
        "tp_price": float(trade_data.get("tp_price") or 0.0),
        "planned_rr": float(trade_data.get("planned_rr") or 0.0),
        "position_size_usd": float(trade_data.get("position_size_usd") or 0.0),
        "leverage_used": float(trade_data.get("leverage_used") or 1.0),
        "slippage": float(trade_data.get("slippage") or 0.0),
        "time_in_trade_seconds": 0,
        "exit_price": None,
        "exit_reason": None,
        "realized_pnl_usd": 0.0,
        "realized_pnl_pct": 0.0,
        "fees_usd": float(trade_data.get("fees_usd") or 0.0),
        "atr_at_entry": float(trade_data.get("atr_at_entry") or 0.0),
        "sl_distance_pct": float(trade_data.get("sl_distance_pct") or 0.0),
        "status": "open",
        "created_at": now,
        "updated_at": now
    }
    
    columns = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    values = tuple(data.values())
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO trades ({columns}) VALUES ({placeholders})",
            values
        )
        await db.commit()
        
    await log("journal", f"Opened trade journal for ID {trade_id} (Coin: {data['coin']}, Direction: {data['direction']})")
    return trade_id

async def update_trade(trade_id: str, updates: dict):
    """Updates specific fields of an active or closed trade."""
    if not updates:
        return
        
    updates = updates.copy()
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Filter valid columns
    valid_updates = {k: v for k, v in updates.items() if k in ALL_COLUMNS and k != "id"}
    if not valid_updates:
        return
        
    set_clause = ", ".join(f"{k} = ?" for k in valid_updates.keys())
    values = tuple(valid_updates.values()) + (trade_id,)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE trades SET {set_clause} WHERE id = ?",
            values
        )
        await db.commit()
        
    await log("journal", f"Updated journal for trade ID {trade_id} with fields: {list(valid_updates.keys())}", level="DEBUG")

async def close_trade(trade_id: str, exit_data: dict):
    """Closes a trade journal entry and computes durations/PnL metrics."""
    now = datetime.now(timezone.utc).isoformat()
    
    # Fetch original trade details to compute metrics
    trade = await get_trade(trade_id)
    if not trade:
        await log("journal", f"Failed to close trade: Trade ID {trade_id} not found.", level="ERROR")
        return
        
    open_time_str = trade.get("open_time")
    open_time = datetime.fromisoformat(open_time_str) if open_time_str else datetime.now(timezone.utc)
    close_time = datetime.now(timezone.utc)
    time_in_trade = int((close_time - open_time).total_seconds())
    
    entry_price = float(trade.get("entry_price") or 0.0)
    exit_price = float(exit_data.get("exit_price") or 0.0)
    position_size_usd = float(trade.get("position_size_usd") or 0.0)
    direction = trade.get("direction", "LONG").upper()
    
    # Calculate Realized PnL % and USD if not provided directly
    realized_pnl_pct = exit_data.get("realized_pnl_pct")
    realized_pnl_usd = exit_data.get("realized_pnl_usd")
    
    if realized_pnl_pct is None:
        if entry_price > 0:
            if direction == "LONG":
                realized_pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            else:
                realized_pnl_pct = ((entry_price - exit_price) / entry_price) * 100
        else:
            realized_pnl_pct = 0.0
            
    if realized_pnl_usd is None:
        # PnL USD based on leverage/sizing
        realized_pnl_usd = position_size_usd * (realized_pnl_pct / 100)
        
    updates = {
        "status": "closed",
        "close_time": now,
        "exit_price": exit_price,
        "exit_reason": exit_data.get("exit_reason", "manual"),
        "realized_pnl_usd": float(realized_pnl_usd),
        "realized_pnl_pct": float(realized_pnl_pct),
        "fees_usd": float(exit_data.get("fees_usd") or trade.get("fees_usd") or 0.0),
        "slippage": float(exit_data.get("slippage") or trade.get("slippage") or 0.0),
        "time_in_trade_seconds": time_in_trade
    }
    
    await update_trade(trade_id, updates)
    await log("journal", f"Closed trade journal for ID {trade_id} (Reason: {updates['exit_reason']}, PnL: ${realized_pnl_usd:.2f})")

async def get_trade(trade_id: str) -> dict:
    """Retrieves all fields of a specific trade by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {}

async def query_trades(filters: dict = None, fields: list = None) -> list[dict]:
    """
    Queries and filters trades from the journal.
    
    Filters:
      - coin (str)
      - direction (str)
      - exchange (str)
      - account_id (str)
      - date_from (str, ISO format)
      - date_to (str, ISO format)
      - session_type (str)
      - exit_reason (str)
      - status (str)
      
    Fields:
      - List of specific columns to return. If omitted, returns DEFAULT_DISPLAYABLE_FIELDS.
    """
    filters = filters or {}
    fields = fields or DEFAULT_DISPLAYABLE_FIELDS
    
    # Validate selected fields
    selected_fields = [f for f in fields if f in ALL_COLUMNS]
    if not selected_fields:
        selected_fields = DEFAULT_DISPLAYABLE_FIELDS
        
    fields_clause = ", ".join(selected_fields)
    
    query_parts = [f"SELECT {fields_clause} FROM trades"]
    where_clauses = []
    params = []
    
    if "coin" in filters and filters["coin"]:
        where_clauses.append("coin = ?")
        params.append(filters["coin"].upper())
        
    if "direction" in filters and filters["direction"]:
        where_clauses.append("direction = ?")
        params.append(filters["direction"].upper())
        
    if "exchange" in filters and filters["exchange"]:
        where_clauses.append("exchange = ?")
        params.append(filters["exchange"].lower())
        
    if "account_id" in filters and filters["account_id"]:
        where_clauses.append("account_id = ?")
        params.append(filters["account_id"])
        
    if "date_from" in filters and filters["date_from"]:
        where_clauses.append("open_time >= ?")
        params.append(filters["date_from"])
        
    if "date_to" in filters and filters["date_to"]:
        where_clauses.append("open_time <= ?")
        params.append(filters["date_to"])
        
    if "session_type" in filters and filters["session_type"]:
        where_clauses.append("session_type = ?")
        params.append(filters["session_type"].lower())
        
    if "exit_reason" in filters and filters["exit_reason"]:
        where_clauses.append("exit_reason = ?")
        params.append(filters["exit_reason"].lower())
        
    if "status" in filters and filters["status"]:
        where_clauses.append("status = ?")
        params.append(filters["status"].lower())
        
    if where_clauses:
        query_parts.append("WHERE " + " AND ".join(where_clauses))
        
    query_parts.append("ORDER BY open_time DESC")
    
    query = " ".join(query_parts)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def cleanup_journal(retention_days: int = 180, vacuum: bool = False):
    """
    Delete old closed trades to cap DB growth.
    Keeps open trades regardless of age.
    """
    if retention_days < 1:
        return
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM trades WHERE status = 'closed' AND close_time IS NOT NULL AND close_time < ?",
            (cutoff_iso,)
        )
        await db.commit()
        if vacuum:
            await db.execute("VACUUM")
            await db.commit()
