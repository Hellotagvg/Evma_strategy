"""Incremental log sync from backend to local JSONL files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from kinetic_ops.client import KineticClient
from kinetic_ops.config import LOGS_DIR
from kinetic_ops.state import touch_log_sync

UK_TZ = ZoneInfo("Europe/London")
RETENTION_DAYS = 30


@dataclass
class DateSyncResult:
    date: str
    status: str
    local_count: int
    server_count: int
    action: str


def uk_dates_in_window(days: int = RETENTION_DAYS) -> list[str]:
    today = datetime.now(UK_TZ).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def log_file_for_date(d: str) -> Path:
    return LOGS_DIR / f"{d}.jsonl"


def index_path() -> Path:
    return LOGS_DIR / "index.json"


def read_local_logs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "first_timestamp": None, "last_timestamp": None}
    timestamps = [r.get("timestamp") or "" for r in rows]
    return {
        "count": len(rows),
        "first_timestamp": min(timestamps),
        "last_timestamp": max(timestamps),
    }


def needs_sync(local: list[dict], server: list[dict]) -> bool:
    if len(local) != len(server):
        return True
    if not server:
        return False
    local_summary = summarize_rows(local)
    server_summary = summarize_rows(server)
    return (
        local_summary["first_timestamp"] != server_summary["first_timestamp"]
        or local_summary["last_timestamp"] != server_summary["last_timestamp"]
    )


def merge_server_logs(server_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep server order; dedupe by timestamp+message."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in server_rows:
        key = f"{row.get('timestamp')}|{row.get('message')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def update_index(date_results: list[DateSyncResult]) -> None:
    index: dict[str, Any] = {"dates": {}, "last_synced_at": datetime.now(UK_TZ).isoformat()}
    for result in date_results:
        path = log_file_for_date(result.date)
        rows = read_local_logs(path)
        index["dates"][result.date] = {
            **summarize_rows(rows),
            "last_synced_at": datetime.now(UK_TZ).isoformat(),
            "status": result.status,
        }
    index_path().write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def sync_date(client: KineticClient, d: str) -> DateSyncResult:
    path = log_file_for_date(d)
    local_rows = read_local_logs(path)
    server_rows = client.get_logs(d)
    server_rows = merge_server_logs(server_rows)

    if not needs_sync(local_rows, server_rows):
        return DateSyncResult(
            date=d,
            status="up_to_date",
            local_count=len(local_rows),
            server_count=len(server_rows),
            action="—",
        )

    prev_count = len(local_rows)
    write_jsonl(path, server_rows)
    action = "Fetched" if prev_count == 0 else f"{prev_count} → {len(server_rows)}"
    return DateSyncResult(
        date=d,
        status="updated",
        local_count=len(server_rows),
        server_count=len(server_rows),
        action=action,
    )


def sync_logs(
    client: KineticClient,
    *,
    target_date: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> list[DateSyncResult]:
    dates = [target_date] if target_date else uk_dates_in_window()
    results: list[DateSyncResult] = []
    total = len(dates)

    for idx, d in enumerate(dates, start=1):
        if progress_cb:
            progress_cb(d, idx, total)
        try:
            results.append(sync_date(client, d))
        except Exception as exc:
            results.append(DateSyncResult(
                date=d,
                status="error",
                local_count=len(read_local_logs(log_file_for_date(d))),
                server_count=0,
                action=str(exc),
            ))

    update_index(results)
    touch_log_sync({
        "dates_synced": len(results),
        "updated": sum(1 for r in results if r.status == "updated"),
        "errors": sum(1 for r in results if r.status == "error"),
    })
    return results


def status_report(client: KineticClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in uk_dates_in_window():
        local_path = log_file_for_date(d)
        local_rows = read_local_logs(local_path)
        local_summary = summarize_rows(local_rows)
        try:
            server_rows = client.get_logs(d)
            server_summary = summarize_rows(merge_server_logs(server_rows))
            gap = local_summary["count"] != server_summary["count"]
            rows.append({
                "date": d,
                "local_count": local_summary["count"],
                "server_count": server_summary["count"],
                "gap": gap,
                "local_last": local_summary["last_timestamp"],
                "server_last": server_summary["last_timestamp"],
            })
        except Exception as exc:
            rows.append({
                "date": d,
                "local_count": local_summary["count"],
                "server_count": None,
                "gap": True,
                "error": str(exc),
            })
    return rows


def total_local_lines() -> int:
    total = 0
    for path in LOGS_DIR.glob("*.jsonl"):
        total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return total
