"""
UK Timezone Trading Session Manager.
Handles session validation, BST/GMT, intent timeframes, and trade limits.
"""

import os
from datetime import datetime, time, timedelta
from dataclasses import dataclass
from typing import Optional
import pytz
from backend.core.logger import log, log_sync

_uk_timezone = pytz.timezone("Europe/London")

@dataclass
class Session:
    id: str                 # Format: YYYY-MM-DD_morning or YYYY-MM-DD_evening
    type: str               # "morning" or "evening"
    start_time: datetime    # Datetime in Europe/London
    end_time: datetime      # Datetime in Europe/London
    is_active: bool
    traded_coin: Optional[str] = None

class SessionManager:
    def __init__(self):
        self.traded_sessions = {}  # session_id -> coin_symbol (persisted or pre-populated)

    def get_uk_now(self) -> datetime:
        """Get current time in UK Europe/London timezone."""
        utc_now = datetime.now(pytz.utc)
        return utc_now.astimezone(_uk_timezone)

    async def pre_populate_from_journal(self):
        """Pre-populates the traded sessions list by querying the database."""
        try:
            # We import here to avoid circular imports
            from backend.core.journal import query_trades
            trades = await query_trades(filters={"status": "open"})
            closed_trades = await query_trades(filters={"status": "closed"})
            all_trades = trades + closed_trades
            for t in all_trades:
                session_id = t.get("session_id")
                coin = t.get("coin")
                if session_id and coin:
                    if session_id not in self.traded_sessions:
                        self.traded_sessions[session_id] = set()
                    self.traded_sessions[session_id].add(coin)
            await log("session", f"Pre-populated traded sessions state: {{k: list(v) for k, v in self.traded_sessions.items()}}")
        except Exception as e:
            await log("session", f"Failed to pre-populate session state from journal: {e}", level="WARNING")

    def get_current_session(self) -> Optional[Session]:
        """
        Returns the currently active session based on UK time, or None if outside session hours.
        Morning session: 06:00 - 08:00
        Evening session: 17:00 - 22:00
        """
        now = self.get_uk_now()
        today = now.date()
        
        # Define morning boundaries
        m_start = _uk_timezone.localize(datetime.combine(today, time(6, 0)))
        m_end = _uk_timezone.localize(datetime.combine(today, time(8, 0)))
        
        # Define evening boundaries
        e_start = _uk_timezone.localize(datetime.combine(today, time(17, 0)))
        e_end = _uk_timezone.localize(datetime.combine(today, time(22, 0)))
        
        if m_start <= now <= m_end:
            session_id = f"{today.strftime('%Y-%m-%d')}_morning"
            coins = self.traded_sessions.get(session_id)
            traded_coin_str = ", ".join(sorted(coins)) if coins else None
            return Session(
                id=session_id,
                type="morning",
                start_time=m_start,
                end_time=m_end,
                is_active=True,
                traded_coin=traded_coin_str
            )
        elif e_start <= now <= e_end:
            session_id = f"{today.strftime('%Y-%m-%d')}_evening"
            coins = self.traded_sessions.get(session_id)
            traded_coin_str = ", ".join(sorted(coins)) if coins else None
            return Session(
                id=session_id,
                type="evening",
                start_time=e_start,
                end_time=e_end,
                is_active=True,
                traded_coin=traded_coin_str
            )
            
        return None

    def is_session_execution_active(self, session_id: str) -> bool:
        """
        Returns True while limit dragging is allowed for this session (until session end_time).
        Morning ends 08:00 UK; evening ends 22:00 UK.
        """
        try:
            date_str, sess_type = session_id.rsplit("_", 1)
            if sess_type not in ("morning", "evening"):
                return False
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return False

        now = self.get_uk_now()
        if sess_type == "morning":
            end = _uk_timezone.localize(datetime.combine(day, time(8, 0)))
        else:
            end = _uk_timezone.localize(datetime.combine(day, time(22, 0)))
        return now <= end

    def is_accepting_intents(self) -> bool:
        """
        Intents are accepted during the session or up to 15 minutes before the session starts.
        Morning intents: 05:45 - 08:00
        Evening intents: 16:45 - 22:00
        """
        now = self.get_uk_now()
        today = now.date()
        
        # Morning boundaries
        m_intent_start = _uk_timezone.localize(datetime.combine(today, time(5, 45)))
        m_end = _uk_timezone.localize(datetime.combine(today, time(8, 0)))
        
        # Evening boundaries
        e_intent_start = _uk_timezone.localize(datetime.combine(today, time(16, 45)))
        e_end = _uk_timezone.localize(datetime.combine(today, time(22, 0)))
        
        return (m_intent_start <= now <= m_end) or (e_intent_start <= now <= e_end)

    def get_next_session(self) -> Session:
        """Computes the boundaries for the next upcoming trading session."""
        now = self.get_uk_now()
        today = now.date()
        
        m_start = _uk_timezone.localize(datetime.combine(today, time(6, 0)))
        e_start = _uk_timezone.localize(datetime.combine(today, time(17, 0)))
        
        if now < m_start:
            # Next is morning today
            m_end = _uk_timezone.localize(datetime.combine(today, time(8, 0)))
            session_id = f"{today.strftime('%Y-%m-%d')}_morning"
            coins = self.traded_sessions.get(session_id)
            traded_coin_str = ", ".join(sorted(coins)) if coins else None
            return Session(
                id=session_id,
                type="morning",
                start_time=m_start,
                end_time=m_end,
                is_active=False,
                traded_coin=traded_coin_str
            )
        elif now < e_start:
            # Next is evening today
            e_end = _uk_timezone.localize(datetime.combine(today, time(22, 0)))
            session_id = f"{today.strftime('%Y-%m-%d')}_evening"
            coins = self.traded_sessions.get(session_id)
            traded_coin_str = ", ".join(sorted(coins)) if coins else None
            return Session(
                id=session_id,
                type="evening",
                start_time=e_start,
                end_time=e_end,
                is_active=False,
                traded_coin=traded_coin_str
            )
        else:
            # Next is morning tomorrow
            tomorrow = today + timedelta(days=1)
            tm_start = _uk_timezone.localize(datetime.combine(tomorrow, time(6, 0)))
            tm_end = _uk_timezone.localize(datetime.combine(tomorrow, time(8, 0)))
            session_id = f"{tomorrow.strftime('%Y-%m-%d')}_morning"
            coins = self.traded_sessions.get(session_id)
            traded_coin_str = ", ".join(sorted(coins)) if coins else None
            return Session(
                id=session_id,
                type="morning",
                start_time=tm_start,
                end_time=tm_end,
                is_active=False,
                traded_coin=traded_coin_str
            )

    def can_trade(self, session_id: str) -> bool:
        """Checks if a trade can be executed for the given session ID."""
        # Deprecated: limit is now checked per-account in intent route.
        return True

    def mark_session_traded(self, session_id: str, coin: str):
        """Marks a session as traded with the specified coin symbol."""
        if session_id not in self.traded_sessions:
            self.traded_sessions[session_id] = set()
        if isinstance(self.traded_sessions[session_id], str):
            self.traded_sessions[session_id] = {self.traded_sessions[session_id]}
        self.traded_sessions[session_id].add(coin)
        log_sync(f"Session {session_id} has been marked as TRADED with coin: {coin}")

    def get_session_status(self) -> dict:
        """Returns session status metadata for display on the frontend."""
        now = self.get_uk_now()
        current = self.get_current_session()
        next_sess = self.get_next_session()
        
        status = {
            "current_time_uk": now.isoformat(),
            "is_accepting_intents": self.is_accepting_intents(),
            "active_session": None,
            "next_session": {
                "id": next_sess.id,
                "type": next_sess.type,
                "start_time": next_sess.start_time.isoformat(),
                "end_time": next_sess.end_time.isoformat(),
                "traded_coin": next_sess.traded_coin
            }
        }
        
        if current:
            status["active_session"] = {
                "id": current.id,
                "type": current.type,
                "start_time": current.start_time.isoformat(),
                "end_time": current.end_time.isoformat(),
                "traded_coin": current.traded_coin
            }
            
        return status
