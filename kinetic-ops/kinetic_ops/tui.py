"""Rich TUI helpers — colours, panels, menu, progress."""

from __future__ import annotations

import sys
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

console = Console()

BRAND = "bold cyan"
SUCCESS = "green"
WARNING = "yellow"
ERROR = "red"
MUTED = "dim"
ACCENT = "magenta"
INFO = "blue"


def supports_tui() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def print_banner(backend_url: str, connection: str) -> None:
    host = backend_url.replace("https://", "").replace("http://", "").split("/")[0]
    dot = {"ok": "🟢", "offline": "🔴", "auth": "🟡"}.get(connection, "🔴")
    title = Text("Kinetic Ops", style=BRAND)
    subtitle = Text(f"  {dot} {host}", style=MUTED)
    console.print(Panel(Text.assemble(title, subtitle), border_style="cyan"))


def success(msg: str) -> None:
    console.print(f"[{SUCCESS}]✅ {msg}[/]")


def warn(msg: str) -> None:
    console.print(f"[{WARNING}]⚠️  {msg}[/]")


def fail(msg: str, *, hint: Optional[str] = None) -> None:
    console.print(f"[{ERROR}]❌ {msg}[/]")
    if hint:
        console.print(f"[{MUTED}]💡 {hint}[/]")


def info(msg: str) -> None:
    console.print(f"[{INFO}]{msg}[/]")


def error_panel(title: str, lines: list[str]) -> None:
    body = "\n".join(f"• {line}" for line in lines)
    console.print(Panel(body, title=f"[{ERROR}]{title}[/]", border_style="red"))


def result_panel(title: str, body: str, *, style: str = SUCCESS) -> None:
    console.print(Panel(body, title=f"[{style}]{title}[/]", border_style=style))


def sync_results_table(results) -> Table:
    table = Table(show_header=True, header_style=BRAND)
    table.add_column("Date", style=INFO)
    table.add_column("Status")
    table.add_column("Local", justify="right")
    table.add_column("Server", justify="right")
    table.add_column("Action", style=MUTED)
    for r in results:
        status_style = SUCCESS if r.status == "up_to_date" else (WARNING if r.status == "updated" else ERROR)
        table.add_row(
            r.date,
            f"[{status_style}]{r.status.replace('_', ' ')}[/]",
            str(r.local_count),
            str(r.server_count),
            r.action,
        )
    return table


def run_sync_progress(sync_fn: Callable) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Syncing logs…", total=1)

        def cb(d: str, idx: int, total: int) -> None:
            progress.update(task_id, completed=idx - 1, total=total, description=f"Syncing {d}")

        results = sync_fn(progress_cb=cb)
        progress.update(task_id, completed=progress.tasks[task_id].total)
    return results


def prompt_menu() -> str:
    console.print()
    console.print(f"[{BRAND}]What would you like to do?[/]\n")
    options = [
        ("1", "Sync logs", "Download & update local log archive"),
        ("2", "Update accounts", "Push accounts.json when no trades are active"),
        ("3", "Status", "Backend health, log sync state, active trades"),
        ("4", "Settings", "URL, password, paths"),
        ("q", "Quit", ""),
    ]
    for key, label, sub in options:
        line = f"  [{ACCENT}]{key}[/] {label}"
        if sub:
            line += f" [{MUTED}]— {sub}[/]"
        console.print(line)
    console.print()
    choice = console.input(f"[{ACCENT}]> [/]").strip().lower()
    return choice


def confirm(prompt: str, default_no: bool = True) -> bool:
    suffix = "[y/N]" if default_no else "[Y/n]"
    ans = console.input(f"{prompt} {suffix} ").strip().lower()
    if not ans:
        return not default_no
    return ans in ("y", "yes")


def prompt_hidden(prompt: str) -> str:
    import getpass
    return getpass.getpass(prompt)
