"""CLI entry points — interactive menu and subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from rich.table import Table

from kinetic_ops import __version__
from kinetic_ops.accounts import (
    EXIT_BLOCKED,
    ActiveTradesBlocked,
    apply_accounts_config,
    format_active_orders,
    load_accounts_file,
    summarize_diff,
    validate_accounts,
    check_active_trades,
)
from kinetic_ops.client import KineticClient, EXIT_AUTH
from kinetic_ops.config import (
    CONFIG_ACCOUNTS_DIR,
    LOGS_DIR,
    backend_host_label,
    ensure_dirs,
    get_backend_password,
    get_backend_url,
    save_config,
    save_env_password,
)
from kinetic_ops import logs_sync
from kinetic_ops import state as app_state
from kinetic_ops import tui

EXIT_USER = 1
EXIT_CONN = 3


def build_client() -> KineticClient:
    return KineticClient(get_backend_url(), get_backend_password())


def connection_status(client: KineticClient) -> str:
    ok, _, _ = client.check_connection()
    if not client.password:
        return "auth"
    if ok:
        return "ok"
    try:
        with __import__("httpx").Client(timeout=5) as http:
            resp = http.get(f"{client.base_url}/healthz")
            if resp.status_code == 200:
                return "auth"
    except Exception:
        pass
    return "offline"


def setup_first_run(interactive: bool = True) -> int:
    ensure_dirs()
    url = get_backend_url()
    password = get_backend_password()

    if interactive and tui.supports_tui():
        if url == "http://localhost:8000":
            entered = tui.console.input(
                f"[{tui.MUTED}]Where is your Kinetic backend?[/] [{tui.INFO}]{url}[/]: "
            ).strip()
            if entered:
                url = entered.rstrip("/")
                save_config(backend_url=url)

        if not password:
            password = tui.prompt_hidden("Backend password: ")
            if password and tui.confirm("Save password to local .env?", default_no=False):
                save_env_password(password)
                save_config(password=password)

    client = KineticClient(url, password)
    ok, msg, _ = client.check_connection()
    if ok:
        if interactive:
            tui.success(f"Connected to {backend_host_label(url)} ({msg})")
        return 0
    if interactive:
        if "password" in msg.lower():
            tui.fail(msg, hint="Set KINETIC_BACKEND_PASSWORD or run Settings")
            return EXIT_CONN
        tui.fail(msg, hint="Check KINETIC_BACKEND_URL and that the backend is running")
        return EXIT_CONN
    print(msg, file=sys.stderr)
    return EXIT_CONN


def cmd_sync_logs(args) -> int:
    ensure_dirs()
    client = build_client()
    ok, msg, _ = client.check_connection()
    if not ok:
        if args.no_tui:
            print(msg, file=sys.stderr)
        else:
            tui.fail(msg, hint="Fix connection in Settings or .env")
        return EXIT_CONN

    if args.status:
        rows = logs_sync.status_report(client)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        table = tui.Table(show_header=True, header_style=tui.BRAND)
        table.add_column("Date", style=tui.INFO)
        table.add_column("Local", justify="right")
        table.add_column("Server", justify="right")
        table.add_column("Gap")
        for row in rows:
            gap = "yes" if row.get("gap") else "—"
            table.add_row(
                row["date"],
                str(row.get("local_count", 0)),
                str(row.get("server_count", "—")),
                gap,
            )
        tui.console.print(table)
        return 0

    if args.no_tui:
        results = logs_sync.sync_logs(client, target_date=args.date)
    else:
        results = tui.run_sync_progress(
            lambda progress_cb: logs_sync.sync_logs(
                client, target_date=args.date, progress_cb=progress_cb
            )
        )

    updated = sum(1 for r in results if r.status == "updated")
    total_lines = logs_sync.total_local_lines()

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
        return 0

    if not args.no_tui:
        tui.console.print(tui.sync_results_table(results))

    if updated == 0:
        msg = "Logs are up to date — nothing to fetch"
    else:
        msg = f"Sync complete — {total_lines:,} lines in {LOGS_DIR}"
    if args.no_tui:
        print(msg)
    else:
        tui.success(msg)
        if updated == 0:
            tui.info("Tip: run Sync logs daily; server only keeps 30 days")
    return 0


def cmd_update_accounts(args) -> int:
    ensure_dirs()
    client = build_client()
    ok, msg, _ = client.check_connection()
    if not ok:
        if args.no_tui:
            print(msg, file=sys.stderr)
        else:
            tui.fail(msg)
        return EXIT_CONN

    file_path: Optional[Path] = None
    if args.file:
        file_path = Path(args.file)
    elif args.pick or (not args.file and tui.supports_tui() and not args.no_tui):
        candidates = sorted(CONFIG_ACCOUNTS_DIR.glob("*.json"))
        candidates = [p for p in candidates if "example" not in p.name.lower()]
        if not candidates:
            tui.fail(f"No JSON files in {CONFIG_ACCOUNTS_DIR}", hint="Copy accounts.example.json")
            return EXIT_USER
        tui.info("Account config files:")
        for idx, path in enumerate(candidates, start=1):
            try:
                accs = load_accounts_file(path)
                enabled = sum(1 for a in accs if a.get("enabled", True))
                ids = ", ".join(a.get("id", "?") for a in accs[:4])
                tui.console.print(f"  [{tui.ACCENT}]{idx}[/] {path.name} — {enabled} enabled ({ids})")
            except Exception as exc:
                tui.console.print(f"  [{tui.ACCENT}]{idx}[/] {path.name} — [red]invalid: {exc}[/]")
        pick = tui.console.input(f"[{tui.ACCENT}]Pick number: [/]").strip()
        try:
            file_path = candidates[int(pick) - 1]
        except (ValueError, IndexError):
            tui.fail("Invalid selection")
            return EXIT_USER
    else:
        default = CONFIG_ACCOUNTS_DIR / "accounts.json"
        if not default.exists():
            print("Provide --file path/to/accounts.json", file=sys.stderr)
            return EXIT_USER
        file_path = default

    try:
        accounts = load_accounts_file(file_path)
    except Exception as exc:
        tui.fail(f"Could not read {file_path}: {exc}")
        return EXIT_USER

    val_errors = validate_accounts(accounts)
    if val_errors:
        tui.error_panel("Validation failed", val_errors)
        return EXIT_USER

    try:
        active_count, active_orders = check_active_trades(client)
    except PermissionError:
        tui.fail("Wrong password — check KINETIC_BACKEND_PASSWORD")
        return EXIT_CONN

    try:
        remote = client.get_accounts()
    except PermissionError:
        tui.fail("Wrong password — check KINETIC_BACKEND_PASSWORD")
        return EXIT_CONN

    diff = summarize_diff(accounts, remote)

    if not args.no_tui:
        preflight = (
            f"Active managed orders: [{tui.ERROR if active_count else tui.SUCCESS}]{active_count}[/]\n"
            f"Added: {', '.join(diff['added']) or '—'}\n"
            f"Removed: {', '.join(diff['removed']) or '—'}\n"
            f"Changed: {', '.join(diff['changed']) or '—'}\n"
            f"File: [blue]{file_path}[/]"
        )
        tui.console.print(Panel(
            preflight, title="Pre-flight", border_style="cyan"
        ))

    if active_count > 0:
        detail = format_active_orders(active_orders)
        msg = f"Can't update accounts: {active_count} active trade(s)"
        if args.no_tui:
            print(f"{msg} — {detail}", file=sys.stderr)
        else:
            tui.warn(f"{msg} on {detail}")
            tui.result_panel(
                "Update blocked",
                "Wait until the bot has no active limits.\n"
                "Check Kinetic Terminal → Strategy → Active orders.",
                style=tui.WARNING,
            )
        return EXIT_BLOCKED

    if not args.yes:
        if args.no_tui:
            ans = input("Apply this config to production? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                return EXIT_USER
        elif not tui.confirm("Apply this config to production?"):
            return EXIT_USER

    try:
        result = apply_accounts_config(client, accounts)
    except ActiveTradesBlocked as exc:
        return EXIT_BLOCKED
    except ValueError as exc:
        detail = exc.args[0]
        if isinstance(detail, dict) and "errors" in detail:
            tui.error_panel("Backend rejected config", detail["errors"])
        else:
            tui.fail(str(detail))
        return EXIT_USER

    count = result.get("accounts_count", len(accounts))
    success_msg = f"Accounts updated — {count} account(s) live"
    if args.no_tui:
        print(success_msg)
    else:
        tui.success(success_msg)
        tui.info("Verify with GET /api/accounts on the backend")
    return 0


def cmd_status(args) -> int:
    ensure_dirs()
    client = build_client()
    ok, msg, readyz = client.check_connection()

    try:
        active_count, _ = check_active_trades(client) if ok else (None, [])
    except PermissionError:
        active_count = None

    payload = {
        "backend_url": get_backend_url(),
        "connection": msg,
        "connected": ok,
        "trading_ready": bool(readyz and readyz.get("ready")),
        "ready": bool(readyz and readyz.get("ready")),
        "last_log_sync_at": app_state.get_last_sync_at(),
        "logs_dir": str(LOGS_DIR),
        "local_log_lines": logs_sync.total_local_lines(),
        "active_managed_orders": active_count,
        "last_accounts_update_at": app_state.get_last_accounts_update_at(),
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if ok else EXIT_CONN

    dot = "🟢" if ok else "🔴"
    tui.console.print(f"\n[{tui.BRAND}]Status[/]\n")
    tui.console.print(f"  Backend: {dot} {msg}")
    if ok and readyz is not None:
        feed_ready = bool(readyz.get("ready"))
        feed_style = tui.SUCCESS if feed_ready else tui.MUTED
        feed_label = "ready" if feed_ready else "not ready (feed)"
        tui.console.print(f"  Trading feed: [{feed_style}]{feed_label}[/]")
    tui.console.print(f"  Logs: {payload['local_log_lines']:,} lines — last sync {payload['last_log_sync_at'] or 'never'}")
    trades_style = tui.ERROR if (active_count or 0) > 0 else tui.SUCCESS
    tui.console.print(f"  Active trades: [{trades_style}]{active_count if active_count is not None else '?'}[/]")
    tui.console.print(f"  Config: last update {payload['last_accounts_update_at'] or 'never'}")
    return 0 if ok else EXIT_CONN


def interactive_loop() -> int:
    ensure_dirs()
    rc = setup_first_run(interactive=True)
    if rc != 0:
        return rc

    client = build_client()
    while True:
        try:
            tui.print_banner(get_backend_url(), connection_status(client))
            choice = tui.prompt_menu()
            if choice in ("q", "quit", "exit"):
                return 0
            if choice == "1":
                cmd_sync_logs(argparse.Namespace(date=None, status=False, yes=True, json=False, no_tui=False))
            elif choice == "2":
                cmd_update_accounts(argparse.Namespace(file=None, pick=True, yes=False, no_tui=False))
            elif choice == "3":
                cmd_status(argparse.Namespace(json=False))
            elif choice == "4":
                url = tui.console.input(f"Backend URL [{get_backend_url()}]: ").strip()
                if url:
                    save_config(backend_url=url)
                pw = tui.prompt_hidden("Password (blank to keep): ")
                if pw:
                    if tui.confirm("Save password to .env?", default_no=False):
                        save_env_password(pw)
                    save_config(password=pw)
                client = build_client()
                setup_first_run(interactive=True)
            else:
                tui.warn("Unknown option — pick 1-4 or q")
        except KeyboardInterrupt:
            tui.console.print(f"\n[{tui.MUTED}]Back to menu (Ctrl+C)[/]")
            continue


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kops",
        description="Kinetic Ops — sync logs and update accounts on your trading backend",
    )
    parser.add_argument("--version", action="version", version=f"kops {__version__}")
    parser.add_argument("--no-tui", action="store_true", help="Plain text output for scripts/CI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show stack traces on errors")

    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync-logs", help="Sync operational logs from backend")
    p_sync.add_argument("--date", help="Sync one UK date (YYYY-MM-DD)")
    p_sync.add_argument("--status", action="store_true", help="Show local vs server log status")
    p_sync.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_sync.add_argument("--json", action="store_true", help="Machine-readable output")

    p_acc = sub.add_parser("update-accounts", help="Push accounts.json to backend")
    p_acc.add_argument("--file", help="Path to accounts JSON")
    p_acc.add_argument("--pick", action="store_true", help="Pick from ./config/")
    p_acc.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_status = sub.add_parser("status", help="Backend health and sync state")
    p_status.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    try:
        if args.command is None:
            if args.no_tui:
                parser.print_help()
                return 0
            return interactive_loop()
        if args.command == "sync-logs":
            return cmd_sync_logs(args)
        if args.command == "update-accounts":
            return cmd_update_accounts(args)
        if args.command == "status":
            return cmd_status(args)
    except KeyboardInterrupt:
        if not args.no_tui:
            tui.console.print(f"\n[{tui.MUTED}]Cancelled[/]")
        return EXIT_USER
    except Exception as exc:
        if args.verbose:
            raise
        if args.no_tui:
            print(str(exc), file=sys.stderr)
        else:
            tui.fail(str(exc), hint="Use --verbose for details or check Settings")
        return EXIT_USER

    return 0


if __name__ == "__main__":
    sys.exit(main())
