# Operations runbook

Day-to-day checks for **Antigravity Trading System** on Oracle and **Kinetic Terminal** on GitHub Pages.

---

## Pre-session checklist

1. **`GET /readyz`** — `ready: true`, `feed_ws_connected: true`, `accounts_loaded: true`
2. **`GET /api/execution/status`** (with password) — know active managed order count
3. **Journal DB** — `backend/data/journal.db` present
4. **Clock** — VM uses UTC; session logic uses `Europe/London`
5. **Frontend** — Settings URL/password correct; WebSocket green status
6. **Accounts** — intended accounts `enabled: true` in `accounts.json`
7. **Exchange monitor** — `/ws` `exchange_monitor.stale` is false; positions match exchange

---

## `/readyz` failures

| Check | Likely cause | Action |
|-------|--------------|--------|
| `startup_self_checks_ok: false` | Missing/invalid `accounts.json` or data dir not writable | Fix JSON; `chmod` on `backend/data/` |
| `feed_running: false` | DataFeedManager failed to start | `journalctl -u kinetic-backend`; restart |
| `feed_ws_connected: false` | Bybit WS down, network block, or `KINETIC_DISABLE_LIVE_FEED=1` | See stale feed; do not trade live if feed required |
| `accounts_loaded: false` | Empty `accounts` array | Add at least one account |

`/healthz` can be OK while `/readyz` is not.

---

## Stale market feed

Watchdog (`data_feed_watchdog_loop`):

- No WS message for **180 seconds** → `nudge_reconnect()`
- Poll every 30s

**Symptoms:** `/ws` indicators `"Warming up"` or stale prices; dragging stops updating.

**Actions:**

1. Check `readyz` → `feed.last_ws_message_ts` age
2. Verify outbound connectivity to `stream.bybit.com` and `api.bybit.com`
3. `sudo systemctl restart kinetic-backend`
4. `journalctl -u kinetic-backend --since "1 hour ago" | grep -i data_feed`

---

## Stale exchange monitor

**Symptoms:** Positions/limits UI frozen; `exchange_monitor.stale: true` in WS payload.

**Actions:**

1. Check `position_errors` / `order_errors` in snapshot
2. `POST /api/exchange/refresh-monitor` with password
3. Verify ccxt credentials on server for each enabled account
4. Restart backend if monitor loop crashed (check logs)

Frontend auto-refreshes via REST when snapshot age exceeds **10 seconds**.

---

## Journal

- **Path:** `backend/data/journal.db`
- **API:** `GET /api/journal/query`
- **Open trades:** `status=open` — pre-populates session `traded_coin` display on restart
- **Retention:** 180 days (`maintenance_loop` every 6 hours)

Backup DB before manual edits.

---

## Active order snapshots

- **Path:** `backend/data/active_orders_snapshot.json`
- Written every **10s**, on lifecycle events, and on shutdown
- Restored on startup

After crash:

1. Verify logs for restored order count
2. Cross-check exchange via UI positions/limits
3. Cancel orphans via UI or `POST /api/exchange/cancel-order`

---

## System logs

- **Path:** `backend/data/logs.db`
- **UI:** Strategy → Logs panel (date picker)
- **API:** `GET /api/logs?date=YYYY-MM-DD`
- **Kinetic Ops:** `python kops.py sync-logs --yes` → `./data/logs/*.jsonl`
- **Retention:** 30 days

---

## Credential rotation

1. Revoke old keys on exchange
2. Update via Settings → Account configuration, `PUT /api/accounts/{id}`, kinetic-ops `update-accounts`, or SSH edit + `POST /api/accounts/reload`
3. No restart required for hot-reload
4. Add `backend/config/accounts.json` to `.gitignore`; rotate if ever committed
5. Update `KINETIC_BACKEND_PASSWORD` on server and in all clients (UI, kinetic-ops)

---

## Safe account updates (production)

| Method | When |
|--------|------|
| Settings → Load / Apply | Normal operator workflow |
| `PUT /api/accounts/{id}` | Small patch |
| `PUT /api/accounts/config` | Full replace |
| `kinetic-ops update-accounts` | Scripted push when flat |
| `POST /api/accounts/reload` | After SSH file edit |

Blocked when `active_managed_orders > 0`.

---

## Frontend operations

| Issue | Check |
|-------|--------|
| 401 on API | Settings password vs `KINETIC_BACKEND_PASSWORD` |
| Positions empty | WS connected? `exchange_monitor` errors? CF worker for browser fallback? |
| WS never connects | URL/password; `wss://` behind TLS |
| Exchange writes 400 | `detail` message; account disabled; real keys on server |
| Demo reads via CF worker fail | Add `api-demo.bybit.com` to worker allowlist |

---

## systemd

```bash
sudo systemctl status kinetic-backend
sudo systemctl restart kinetic-backend
journalctl -u kinetic-backend -f
```

Ensure **one worker** and production password in Environment.

---

## Kinetic Ops

```bash
cd kinetic-ops
python kops.py status --json
python kops.py sync-logs --yes
python kops.py update-accounts --file ./config/accounts.json --yes
```

Exit code **2** = active managed trades block account update.

---

## Maintenance windows

- Deploy backend: restart service; snapshot saves on shutdown
- Deploy frontend: `npm run deploy`; hard refresh `/Evma_strategy/`
- Deploy worker: update `dashboard.json` if URL changes

---

## Escalation data to capture

- `/readyz` JSON
- `GET /api/execution/status`
- Last 200 lines `journalctl -u kinetic-backend`
- `GET /api/logs?date=...`
- WS screenshot: indicators + `exchange_monitor.stale`
- Read path (monitor vs browser) vs write path (ccxt) vs bot (intent/drag)
