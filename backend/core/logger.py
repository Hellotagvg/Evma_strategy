"""
SQLite-backed persistent log system for the trading backend.
All backend messages go through this module instead of print().
Logs are organized by UK Day (Europe/London timezone).
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
import pytz
import aiosqlite

# Path to database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "logs.db")

# In-memory queue for logs before db is initialized
_log_queue = []
_db_initialized = False
_db_lock = asyncio.Lock()
_uk_timezone = pytz.timezone("Europe/London")

def get_uk_time():
    """Get the current time in UK timezone (Europe/London)."""
    utc_now = datetime.now(pytz.utc)
    return utc_now.astimezone(_uk_timezone)

async def init_log_db():
    """Initializes the database and creates tables, then writes queued logs."""
    global _db_initialized
    os.makedirs(DATA_DIR, exist_ok=True)
    
    async with _db_lock:
        if _db_initialized:
            return
            
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    uk_date TEXT NOT NULL,
                    uk_time TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    message TEXT NOT NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_uk_date ON logs(uk_date)")
            await db.commit()
            
        _db_initialized = True
        
        # Flush queued logs
        if _log_queue:
            async with aiosqlite.connect(DB_PATH) as db:
                for entry in _log_queue:
                    await db.execute(
                        "INSERT INTO logs (timestamp, uk_date, uk_time, level, message) VALUES (?, ?, ?, ?, ?)",
                        (entry["timestamp"], entry["uk_date"], entry["uk_time"], entry["level"], entry["message"])
                    )
                await db.commit()
            _log_queue.clear()

VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

def _should_log(level: str) -> bool:
    active_level = os.environ.get("KINETIC_LOG_LEVEL", "INFO").upper()
    if active_level not in LOG_LEVEL_ORDER:
        active_level = "INFO"
    return LOG_LEVEL_ORDER.get(level.upper(), 1) >= LOG_LEVEL_ORDER[active_level]

def _normalize_log_inputs(first: str, second: str = None, level: str = "INFO"):
    """
    Backward-compatible argument normalization.
    Supports both:
      - log("message", level="ERROR")
      - log("component", "message")
    """
    lvl = (level or "INFO").upper()
    if second is not None:
        # Treat two positional args as (component, message)
        message = f"[{first}] {second}"
    else:
        # If second arg wasn't provided but first arg looks like a component
        # and 'level' is actually a free-form message from old call style,
        # swap safely.
        if lvl not in VALID_LEVELS and isinstance(level, str):
            message = f"[{first}] {level}"
            lvl = "INFO"
        else:
            message = first
    if lvl not in VALID_LEVELS:
        lvl = "INFO"
    return message, lvl

async def log(message: str, second: str = None, level: str = "INFO"):
    """
    Main logging function. Stores the log in SQLite and prints to stdout.
    Safe to call before init_log_db() (messages will be queued).
    """
    message, level = _normalize_log_inputs(message, second, level)
    if not _should_log(level):
        return
    now_utc = datetime.now(pytz.utc)
    timestamp = now_utc.isoformat()
    
    now_uk = now_utc.astimezone(_uk_timezone)
    uk_date = now_uk.strftime("%Y-%m-%d")
    uk_time = now_uk.strftime("%H:%M:%S.%f")[:-3]  # millisecond resolution
    
    # Print to stdout/stderr
    log_line = f"{timestamp} | {level:<7} | {message}"
    if level in ("ERROR", "CRITICAL"):
        print(log_line, file=sys.stderr)
    else:
        print(log_line)
        
    entry = {
        "timestamp": timestamp,
        "uk_date": uk_date,
        "uk_time": uk_time,
        "level": level,
        "message": message
    }
    
    if _db_initialized:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO logs (timestamp, uk_date, uk_time, level, message) VALUES (?, ?, ?, ?, ?)",
                    (timestamp, uk_date, uk_time, level, message)
                )
                await db.commit()
        except Exception as e:
            print(f"Failed to write log to DB: {e}", file=sys.stderr)
            _log_queue.append(entry)
    else:
        _log_queue.append(entry)

def log_sync(message: str, second: str = None, level: str = "INFO"):
    """
    Synchronous wrapper for log() that queues the write or schedules it on the event loop.
    Useful for synchronous context (e.g. strategy config loaders, callbacks).
    """
    message, level = _normalize_log_inputs(message, second, level)
    if not _should_log(level):
        return
    now_utc = datetime.now(pytz.utc)
    timestamp = now_utc.isoformat()
    
    now_uk = now_utc.astimezone(_uk_timezone)
    uk_date = now_uk.strftime("%Y-%m-%d")
    uk_time = now_uk.strftime("%H:%M:%S.%f")[:-3]
    
    log_line = f"{timestamp} | {level:<7} | {message}"
    if level in ("ERROR", "CRITICAL"):
        print(log_line, file=sys.stderr)
    else:
        print(log_line)
        
    entry = {
        "timestamp": timestamp,
        "uk_date": uk_date,
        "uk_time": uk_time,
        "level": level,
        "message": message
    }
    
    if _db_initialized:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(log(message, level=level))
            else:
                loop.run_until_complete(log(message, level=level))
        except Exception:
            _log_queue.append(entry)
    else:
        _log_queue.append(entry)

async def get_logs(date: str = None) -> list[dict]:
    """
    Retrieves logs for a UK date (YYYY-MM-DD), today if not specified.
    """
    if not date:
        date = get_uk_time().strftime("%Y-%m-%d")
        
    if not _db_initialized:
        # If DB not active yet, return matches from queue
        return [e for e in _log_queue if e["uk_date"] == date]
        
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT timestamp, uk_date, uk_time, level, message FROM logs WHERE uk_date = ? ORDER BY id ASC",
            (date,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def cleanup_logs(retention_days: int = 30, vacuum: bool = False):
    """
    Delete old logs by UK date retention window.
    """
    if retention_days < 1:
        return
    cutoff = (get_uk_time() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM logs WHERE uk_date < ?", (cutoff,))
        await db.commit()
        if vacuum:
            await db.execute("VACUUM")
            await db.commit()
