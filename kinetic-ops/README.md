# Kinetic Ops

Local CLI for **Kinetic Terminal** / **Antigravity Trading System** backends.

Sync operational logs to agent-friendly JSONL files and push `accounts.json` to production safely — no SSH required.

## Quick start

```bash
cd kinetic-ops
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
python -m kinetic_ops           # interactive menu
```

First run creates `./data/logs/` and `./config/`. You will be prompted for backend URL and password (saved to local `.env` if you choose).

## Configuration

| Source | Variable |
|--------|----------|
| Environment | `KINETIC_BACKEND_URL`, `KINETIC_BACKEND_PASSWORD` |
| Local `.env` | Same keys (see `.env.example`) |
| `config.json` | `backend_url`, `password` |

Never commit real API keys. Copy `config/accounts.example.json` → `config/accounts.json` and use placeholders locally.

## Commands

### Interactive (default)

```bash
python -m kinetic_ops
# or
python kops.py
```

Menu: Sync logs · Update accounts · Status · Settings

### Scripts / agents

```bash
python kops.py sync-logs --yes
python kops.py sync-logs --date 2026-06-23
python kops.py sync-logs --status
python kops.py update-accounts --file ./config/accounts.json --yes
python kops.py status --json
python kops.py --no-tui sync-logs --yes
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error / validation |
| 2 | Blocked — active managed trades |
| 3 | Connection / auth failure |

## Sync logs

- Fetches `GET /api/logs?date=YYYY-MM-DD` for each UK date in the 30-day retention window
- Stores canonical JSONL at `./data/logs/YYYY-MM-DD.jsonl`
- Maintains `./data/logs/index.json` with per-date counts and timestamps
- Incremental: only re-fetches dates where local copy differs from server (count / first / last timestamp)

## Update accounts

Pre-flight checks:

1. Local JSON validation (ids, exchange, credentials shape)
2. `GET /api/execution/status` — aborts if `active_managed_orders > 0`
3. Diff summary vs `GET /api/accounts`
4. Confirmation (or `--yes`)

Then `PUT /api/accounts/config` with the full `accounts` array.

## Auth

All `/api/*` routes require header:

```
X-Kinetic-Password: <your-backend-password>
```

Same password as Kinetic Terminal Settings and `KINETIC_BACKEND_PASSWORD` on the server.

Startup checks `/healthz` (reachable) and `GET /api/accounts` (password). Trading feed readiness (`/readyz`) is shown in **Status** for information only — ops tasks do not require the Bybit WebSocket.

## State file

`.kinetic_ops_state.json` in the working directory stores last sync time and config hash — no secrets.
