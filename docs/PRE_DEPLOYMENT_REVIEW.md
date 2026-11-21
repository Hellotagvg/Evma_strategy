# Pre-deployment review

**Review date:** 2026-06-23  
**Scope:** Full repo read — `backend/`, `src/`, `kinetic-ops/`, `cloudflare-worker/`, `deploy/`, existing docs  
**Reviewer:** AI-assisted final review before go-live

---

## Executive summary

The system is **functionally close to deployable** for Bybit demo multi-account operation with password-protected backend APIs, WebSocket-driven monitoring, and Kinetic Ops for remote account/log management. Several **security and integration gaps** must be closed before production: committed exchange credentials in `backend/config/accounts.json`, default backend password, unauthenticated exposure of full account secrets via `GET /api/accounts`, and Cloudflare Worker missing `api-demo.bybit.com` (breaks browser reads for demo trading when using the worker).

Session trade limits have **changed since June 1 docs**: limits are now **per-account** (`max_positions` + one active managed order per coin), not one global trade per UK session. Mock Mode has been **removed** from the frontend; all accounts come from the authenticated backend.

---

## 1. Architecture

### Components

| Component | Path | Role (current) |
|-----------|------|----------------|
| **Kinetic Terminal** | `src/` | React 19 + Vite 8 UI at GitHub Pages `/Evma_strategy/` |
| **Antigravity backend** | `backend/` | FastAPI: sessions, EVMA strategy, execution engine, journal, logs, ccxt writes, exchange monitor |
| **Kinetic Ops CLI** | `kinetic-ops/` | Local password-authenticated ops: log sync to JSONL, push `accounts.json`, status |
| **Cloudflare Worker** | `cloudflare-worker/worker.js` | Optional CORS proxy for **browser-signed exchange reads** only |

### Data flows

#### A. Trade intent → automated execution

```
UI POST /api/intent (X-Kinetic-Password)
  → validate coin/direction, account enabled, R:R, risk_value
  → session time window (is_accepting_intents)
  → per-account limits (max_positions, no duplicate coin)
  → ExecutionEngine.start_intent (async)
  → 5m candles → get_latest_levels → limit @ EVMA, SL at band, TP by R:R
  → ccxt (or SimulatedExchange if placeholder keys)
  → journal open_trade; mark_session_traded (audit); snapshot active_orders
```

On each **1m** candle close: `on_candle_close` may drag limits to latest EVMA. After session `end_time`, `session_execution_loop` cancels unfilled dragging orders.

#### B. WebSocket `/ws` (~1 Hz)

Requires `?password=` query param matching `KINETIC_BACKEND_PASSWORD` (default `yellow`).

Broadcast payload:

- `session_status` — UK session windows, `is_accepting_intents`
- `indicators` — per-coin EVMA, stops, price (from DataFeedManager + strategy)
- `active_orders` — managed drag/fill state from ExecutionEngine
- `exchange_monitor` — **new**: ccxt-polled positions + open orders for all enabled accounts

#### C. Position & limit monitoring (hybrid — changed since prior docs)

**Primary path (backend connected):**

1. `exchange_monitor` polls enabled accounts every **5s** via ccxt (`exchange_ops.fetch_positions` / `fetch_open_orders`)
2. Snapshot included in `/ws` `exchange_monitor` field
3. `App.jsx` applies snapshot to positions/limits UI
4. If snapshot stale (>10s), `POST /api/exchange/refresh-monitor` REST fallback

**Fallback path (backend WS offline):**

1. Browser polls `exchangeAPI.fetchPositions` / `fetchOpenOrders` (signed Bybit REST or HL info API)
2. Optional Cloudflare Worker `?url=` for CORS
3. On browser failure → backend `GET /api/exchange/positions|open-orders` fallback
4. For Bybit only when WS offline: optional **private WebSocket** (`subscribeBybitPrivateFeed`) for lower-latency updates

**Writes** (close, cancel, amend, place limit, TP/SL, batch cancel) always go **browser → backend `/api/exchange/*` → ccxt**. Keys never leave the server for writes.

#### D. Configuration

- Accounts: `backend/config/accounts.json` — hot-reload via API (`accounts_config.py`)
- Strategy: `backend/config/strategy_{btc,eth,sol}.json`
- Dashboard runtime: `backend/config/dashboard.json` — `cloudflare_worker_url` served to UI via `GET /api/dashboard/config`
- Frontend Settings: `kinetic_backend_url`, `kinetic_backend_password` in `localStorage`

---

## 2. Strategy & execution

### EVMA dual dynamic ATR stops

- Python: `backend/core/strategy.py`
- JavaScript: `src/market/indicator/evmaDualStops.js`
- Must stay in parity (see [STRATEGY.md](STRATEGY.md))

### Sessions (UK `Europe/London`)

| Session | Active | Intent acceptance |
|---------|--------|-------------------|
| Morning | 06:00–08:00 | 05:45–08:00 |
| Evening | 17:00–22:00 | 16:45–22:00 |

`is_accepting_intents()` is **time-only** — it does **not** block intents because a session was already traded.

### Per-account limits (current code)

Enforced in `POST /api/intent`:

1. **`max_positions`** (default 3) — max concurrent managed orders per account
2. **One active managed order per coin** per account at a time

`SessionManager.can_trade()` is **deprecated** (always returns `True`). `mark_session_traded` still records coins per session for **display/audit** in `traded_coin` (comma-separated list), not for blocking.

### Execution engine state

- In-memory `active_orders: List[ManagedOrder]`
- Persisted to `backend/data/active_orders_snapshot.json` every 10s + on lifecycle events + shutdown
- Restored on startup; unknown account IDs skipped
- `poll_active_orders_lifecycle` every 5s for fill/TP/SL detection on live exchanges
- `SimulatedExchange` when credentials are placeholders (`YOUR_*`, etc.)

### Multi-account

- One ccxt client per account in ExecutionEngine
- Exchange monitor polls all `enabled` accounts in parallel
- UI Strategy tab: account widgets with per-account risk/R:R form (`intentFormHelpers.js`)

---

## 3. Frontend (Kinetic Terminal)

### Tabs / navigation

- **Home** — account selection cards
- **Positions & Limits** — hub with positions/limits sub-views, filters, TP/SL and amend modals
- **Strategy** — account picker, sub-views: **Intent**, **Monitoring**, **Open Executions**
- **Journal** — filtered trade history from `/api/journal/query`
- **Settings panel** — backend URL/password, account JSON editor (PUT `/api/accounts/config`)

### Strategy tab widgets

- **Account cards** — exchange icon, risk summary, margin %, max position USD, `max_positions`, enabled state
- **Intent form** — dynamic risk (fixed locked panel vs adjustable slider), R:R (fixed per `fixed_tp_rr_by_coin` vs slider from `tp_rr_options`), coin/direction, submit to `/api/intent`
- **Monitoring** — live WS indicator levels per coin with implied TP from account R:R
- **Open Executions** — managed orders for selected account from WS `active_orders`

### Mock mode

**Removed.** Prior docs referencing Settings → Mock Mode and `localStorage kinetic_accounts` are obsolete. Backend auth failure shows empty accounts and red status dot.

### Monitoring cadence

- WS broadcast ~1 Hz (indicators, session, active orders, exchange_monitor)
- Account list refresh every 5s
- Stale monitor fallback at 10s → REST refresh
- WS client ping every 25s

---

## 4. Security & secrets

### Critical findings

| Severity | Issue | Detail |
|----------|-------|--------|
| **Critical** | Real API keys in repo | `backend/config/accounts.json` contains live-format Bybit demo keys and is **not** in root `.gitignore`. **Rotate keys on Bybit immediately**; add to `.gitignore`; use placeholders in repo. |
| **Critical** | Default backend password | `KINETIC_BACKEND_PASSWORD` defaults to `yellow` in code and UI. Must set strong secret on Oracle before exposing port 8000. |
| **High** | `GET /api/accounts` exposes secrets | Returns full credentials to any caller with password. Acceptable only behind VPN/restricted network; consider stripping secrets from GET or using read-only public fields. |
| **High** | CORS `allow_origins=["*"]` + `allow_credentials=True` | Permissive; combined with password header is workable but not ideal. Restrict origins when using TLS + GitHub Pages URL. |
| **Medium** | CF Worker `ALLOWED_ORIGIN = "*"` | Anyone can call worker if URL is known. Tighten to GitHub Pages origin. |
| **Medium** | CF Worker missing `api-demo.bybit.com` | Demo trading browser reads fail through worker (403 host not allowlisted). Live `api.bybit.com` works. |
| **Medium** | Password in WS query string | `?password=` may appear in logs/proxies. Prefer header auth for WS in future. |
| **Low** | `dashboard.json` may contain worker URL | Not secret but operator-specific; keep server-side only. |

### Auth model (current)

| Route | Auth |
|-------|------|
| `/healthz`, `/readyz` | None |
| `/api/*` | `X-Kinetic-Password` header or `?password=` query |
| `/ws` | `?password=` query param |
| Browser exchange reads | HMAC in browser (keys from `/api/accounts`) |
| Exchange writes | Backend ccxt with server-side keys |

### Git ignore patterns

| Path | Ignored? |
|------|----------|
| `kinetic-ops/.env` | Yes (`kinetic-ops/.gitignore`) |
| `kinetic-ops/config/accounts.json` | Yes |
| `backend/config/accounts.json` | **No** — risk |
| `node_modules`, `dist`, `.venv` | Yes (root `.gitignore`) |

---

## 5. Deployment readiness

### Oracle systemd (`deploy/systemd/kinetic-backend.service`)

- Single worker `--workers 1` ✓
- Port 8000 ✓
- **Missing:** `Environment=KINETIC_BACKEND_PASSWORD=...` — add via systemd drop-in or EnvironmentFile
- **Missing:** optional `KINETIC_DISABLE_LIVE_FEED` for degraded mode

### GitHub Pages

- `vite.config.js` `base: '/Evma_strategy/'` ✓
- Build with `VITE_BACKEND_URL=https://your-oracle-host:8000` for `exchangeAPI.js` default (App.jsx uses Settings localStorage at runtime for most calls)

### Health checks

- `/healthz` — liveness only
- `/readyz` — feed WS connected, accounts loaded, startup checks; **required before session trading**

### Kinetic Ops

- Ready for production ops: `python kops.py update-accounts`, `sync-logs`
- Blocks account updates when `active_managed_orders > 0`
- Documented in [DEPLOYMENT.md](DEPLOYMENT.md)

### Single-worker constraint

Active orders, session traded map, exchange monitor cache are **in-process**. Do not scale uvicorn workers without externalizing state.

---

## 6. Gaps / risks / blockers (ranked)

| Rank | Severity | Item | Action before go-live |
|------|----------|------|------------------------|
| 1 | **Blocker** | Committed exchange credentials | Rotate keys; gitignore `accounts.json`; scrub git history if repo is public |
| 2 | **Blocker** | Default password `yellow` | Set `KINETIC_BACKEND_PASSWORD` on server + update Settings/kinetic-ops |
| 3 | **Blocker** | CF Worker missing `api-demo.bybit.com` | Add host to `ALLOWED_HOSTS` in `worker.js` and redeploy worker |
| 4 | **High** | `GET /api/accounts` returns secrets to browser | Accept risk for now OR redact in API; ensure Oracle firewall restricts 8000 |
| 5 | **High** | Oracle port 8000 exposed without TLS | Use nginx/Caddy TLS + `wss://`; firewall to operator IPs |
| 6 | **Medium** | Chart uses JS default indicator params | Chart may disagree with bot until wired to backend strategy config |
| 7 | **Medium** | No UI for `POST /api/intent/cancel` | Cancel dragging via logs/ops or add UI button |
| 8 | **Medium** | `readyz` fails when `KINETIC_DISABLE_LIVE_FEED=1` | Expected — document degraded mode; don't use for live sessions |
| 9 | **Low** | Local dev port inconsistency | README uses 8010; App defaults 8000 — align operator docs |
| 10 | **Low** | ~~cancel-batch path mismatch~~ | Fixed 2026-06-23: `exchangeAPI.js` now calls `/api/exchange/cancel-batch` |

---

## 7. Testing checklist (per venue)

### Backend (all venues)

- [ ] `GET /healthz` returns 200
- [ ] `GET /readyz` returns `ready: true` with feed connected
- [ ] `GET /api/accounts` with password returns expected account rows
- [ ] `GET /ws?password=...` connects; receives `exchange_monitor`, `indicators`, `active_orders`
- [ ] `GET /api/session` shows correct UK window
- [ ] Placeholder account → intent runs on `SimulatedExchange` without exchange errors

### Bybit demo (`demo_trading: true`)

- [ ] Browser positions/limits populate (via WS monitor or browser+CF worker)
- [ ] CF worker proxies `api-demo.bybit.com` if worker is used
- [ ] Intent during window → limit appears on demo account
- [ ] 1m drag updates limit price toward EVMA
- [ ] Fill → TP/SL visible on exchange; journal `open` → `closed`
- [ ] Session end cancels unfilled drag orders
- [ ] Manual close/cancel/amend/TP-SL from UI works
- [ ] Cancel-all limits (batch) works

### Bybit live (`demo_trading: false`)

- [ ] Same as demo with **live** keys and `api.bybit.com`
- [ ] Leverage and margin caps respected (`margin_pct`, `max_leverage`, `max_position_size_usd`)
- [ ] Attached TP/SL on entry order (Bybit-specific)

### Hyperliquid

- [ ] `wallet_address` + `api_secret` in accounts.json
- [ ] Positions/orders in monitor snapshot
- [ ] Intent → limit placement and fill lifecycle
- [ ] Manual writes via backend ccxt

### Frontend (GitHub Pages)

- [ ] App loads at `/Evma_strategy/`
- [ ] Settings: production backend URL + password
- [ ] Strategy intent form respects fixed/adjustable risk
- [ ] Journal and logs panels load
- [ ] Account config apply via Settings (no restart)

### Kinetic Ops

- [ ] `python kops.py status --json` shows auth OK
- [ ] `sync-logs` pulls 30-day window
- [ ] `update-accounts` blocked with active managed orders
- [ ] `update-accounts --yes` succeeds when flat

---

## 8. What changed since 2026-06-01 docs

- **Removed** frontend Mock Mode; backend auth required for accounts
- **Added** `KINETIC_BACKEND_PASSWORD` middleware on all `/api/*` routes
- **Added** `exchange_monitor` — backend ccxt polling in WS payload
- **Added** `GET /api/exchange/positions|open-orders` browser fallback
- **Added** `GET/POST /api/dashboard/config` for CF worker URL (server-side)
- **Added** `accounts_config.py` — validate, atomic persist, hot-reload
- **Added** `kinetic-ops/` CLI (log sync, account push, TUI)
- **Added** `session_execution_loop` — auto-cancel dragging orders after session end
- **Added** `KINETIC_DISABLE_LIVE_FEED` offline/degraded backend mode
- **Added** Bybit private WS in `exchangeAPI.js` (fallback when backend WS down)
- **Added** `intentFormHelpers.js` — dynamic Strategy intent form
- **Changed** session limits from global one-trade-per-session to **per-account** `max_positions` + one coin
- **Changed** position monitoring primary path to WS `exchange_monitor` (not browser-only)
- **Deprecated** `SessionManager.can_trade()` blocking (still exists, always True)

---

## 9. Recommended go-live sequence

1. **Rotate** any credentials ever committed; add `backend/config/accounts.json` to `.gitignore`
2. **Deploy backend** to Oracle with strong `KINETIC_BACKEND_PASSWORD`, writable `backend/data/`, real `accounts.json` on server only
3. **Open firewall** only to operator IPs (or VPN); add TLS reverse proxy
4. **Verify** `/readyz` during a market hour with feed connected
5. **Update Cloudflare Worker** — add `api-demo.bybit.com`, set `ALLOWED_ORIGIN` to GitHub Pages URL
6. **Set** `backend/config/dashboard.json` `cloudflare_worker_url` on server
7. **Build & deploy frontend** with `VITE_BACKEND_URL`; configure Settings password
8. **Run Kinetic Ops** `status` and a test `sync-logs`
9. **Paper test** one intent on Bybit demo during morning/evening window; verify drag, fill, journal
10. **Operator sign-off** using Section 7 checklists per account/venue

---

*Full operator workflow: [OPERATOR.md](OPERATOR.md) · Config reference: [CONFIG.md](CONFIG.md) · Deploy steps: [DEPLOYMENT.md](DEPLOYMENT.md)*
# Historical review notice

This review was written before the deployment moved to Amazon Lightsail and before the production site was confirmed at `https://hellotagvg.github.io/Evma_strategy/`. For current host details and deploy steps, use [DEPLOYMENT.md](DEPLOYMENT.md). Treat the original Oracle-specific findings below as historical.
