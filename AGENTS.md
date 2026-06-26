# AGENTS.md — AI assistant guide

**Last reviewed:** 2026-06-23  
**Product names:** **Kinetic Terminal** (React UI) · **Antigravity Trading System** (FastAPI backend title)

---

## Purpose

Semi-automated crypto **futures** workstation for **BTC, ETH, and SOL** on Bybit (demo/live) and Hyperliquid. The human approves trade **intents**; the backend drags limit entries on the EVMA line, manages fills, and journals outcomes. Strategy levels are backtested (EVMA + dual dynamic ATR stops). This is **not** fully autonomous trading.

**Production backend:** Oracle Cloud free tier (typical shape: 3 vCPU, 12 GB RAM, 50 GB disk). Frontend is static on GitHub Pages under `/tcl/`.

---

## Components

| Component | Path | Role |
|-----------|------|------|
| **React frontend** | `src/` | Kinetic Terminal UI: charts, positions, limits, intents, settings |
| **FastAPI backend** | `backend/` | Sessions, strategy math, execution engine, journal, logs, exchange writes (ccxt), exchange monitor |
| **Kinetic Ops CLI** | `kinetic-ops/` | Local tool: sync logs to JSONL, push `accounts.json` via API (password auth) |
| **Cloudflare Worker** (optional) | `cloudflare-worker/worker.js` | CORS proxy for browser-signed **reads** to Bybit/Hyperliquid |

---

## Repo map

```
Trading dashboard/
├── AGENTS.md                 ← you are here
├── README.md
├── package.json              # npm: dev, build, deploy (gh-pages)
├── vite.config.js            # base: /tcl/
├── kinetic-ops/              # local ops CLI (log sync, account push)
│   ├── kops.py               # python kops.py
│   ├── kinetic_ops/          # python -m kinetic_ops
│   ├── requirements.txt
│   └── README.md
├── src/
│   ├── App.jsx               # main UI, WS, hybrid position monitoring
│   ├── exchangeAPI.js        # reads: browser (+ backend fallback); writes: POST → backend
│   ├── intentFormHelpers.js  # dynamic Strategy intent form (risk + R:R)
│   ├── components/TradingChart.jsx
│   └── market/               # chart candles + evmaDualStops.js
├── backend/
│   ├── main.py               # FastAPI app, REST, /ws, password middleware
│   ├── requirements.txt
│   ├── config/
│   │   ├── accounts.json     # secrets — do not commit real keys
│   │   ├── dashboard.json    # cloudflare_worker_url for UI
│   │   └── strategy_{btc,eth,sol}.json
│   ├── core/
│   │   ├── session.py        # UK morning/evening sessions
│   │   ├── strategy.py       # EVMA + ATR stops (parity with JS)
│   │   ├── data_feed.py      # Bybit WS/REST → 5m candles
│   │   ├── execution.py      # intent → drag → fill → journal
│   │   ├── exchange_ops.py   # /api/exchange/* via ccxt async
│   │   ├── exchange_monitor.py  # ccxt poll → WS exchange_monitor
│   │   ├── accounts_config.py   # validate, persist, hot-reload accounts
│   │   ├── risk.py, journal.py, logger.py
│   │   └── exchange_factory.py
│   └── data/                 # SQLite DBs, active_orders_snapshot.json
├── deploy/systemd/kinetic-backend.service
├── cloudflare-worker/
└── docs/                     # ARCHITECTURE, CONFIG, DEPLOYMENT, API, STRATEGY, OPERATIONS, OPERATOR, PRE_DEPLOYMENT_REVIEW
```

---

## Run commands

```bash
# Backend (from project root)
pip install -r backend/requirements.txt
# Set a strong password in production:
# export KINETIC_BACKEND_PASSWORD=your-secret
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Frontend
npm install
npm run dev

# Production frontend build + GitHub Pages
npm run deploy   # builds to dist/, publishes via gh-pages

# Kinetic Ops (from kinetic-ops/)
python -m kinetic_ops
```

Health: `GET /healthz` (liveness) · `GET /readyz` (feed + accounts + startup checks)

---

## Config locations

| What | Where |
|------|--------|
| Accounts & API keys | `backend/config/accounts.json` |
| CF worker URL (served to UI) | `backend/config/dashboard.json` |
| Per-coin strategy params | `backend/config/strategy_btc.json`, `strategy_eth.json`, `strategy_sol.json` |
| Frontend API base (build-time default for exchangeAPI) | `VITE_BACKEND_URL` env at build time |
| Backend URL + password (runtime) | Settings → `localStorage` `kinetic_backend_url`, `kinetic_backend_password` |
| Backend password (server) | `KINETIC_BACKEND_PASSWORD` env (default `yellow` — change in production) |
| Ops CLI config | `kinetic-ops/.env`, `kinetic-ops/config.json` |

See [docs/CONFIG.md](docs/CONFIG.md).

---

## Operator model

Semi-automated **human-in-the-loop**: during UK sessions the operator submits trade intents (account, coin, direction, risk); the backend validates session time and per-account limits, computes EVMA levels from 5m candles, places a limit on EVMA, drags on 1m closes, sets TP/SL, journals, and broadcasts on `/ws`. Kinetic Terminal is for monitoring (multi-account positions/limits), config, logs, and journal — not discretionary market entry for the edge.

Full workflow, intent sequence, session-limit semantics, and robustness checklist: **[docs/OPERATOR.md](docs/OPERATOR.md)**. Pre-deployment review: **[docs/PRE_DEPLOYMENT_REVIEW.md](docs/PRE_DEPLOYMENT_REVIEW.md)**.

---

## Scaling plan

| Phase | Target |
|-------|--------|
| Now | Bybit demo (`demo_trading`) — prop challenge accounts |
| Next | Bybit live — funded accounts after challenge |
| Later | Hyperliquid — own capital, separate wallets |

Each row in `accounts.json` is one challenge or one wallet (own risk, R:R, `enabled`, `max_positions`). Execution is per account via `ExecutionEngine` + ccxt; UI monitors all enabled accounts. Limits: **`max_positions` per account** (default 3) and **one active managed order per coin** per account — see [docs/OPERATOR.md](docs/OPERATOR.md).

---

## Secrets policy

- **Never** commit real API keys, private keys, or wallet secrets.
- `accounts.json` is required at backend startup but is **not** in root `.gitignore` today — treat as local-only and use placeholders (`YOUR_API_KEY_HERE`) in examples and repos. **Rotate** if keys were ever committed.
- All `/api/*` routes require `X-Kinetic-Password` (or `?password=`). WebSocket `/ws` requires `?password=`.
- `GET /api/accounts` returns credential shapes to authenticated callers — protect the backend network.
- Browser **reads** may use credentials from `/api/accounts` with HMAC signing in the browser (Bybit) or HL info API; optional CF worker for CORS.
- **Writes** (close, cancel, amend, place limit, TP/SL) go through the backend with ccxt — keys must exist in `accounts.json` on the server.

---

## Data flows

### 1. Trade intent & automated execution (backend)

```
UI POST /api/intent
  → session time + per-account limits + risk checks
  → ExecutionEngine.start_intent (async task)
  → 5m candles from DataFeedManager
  → strategy.get_latest_levels → limit on EVMA, SL at band, TP by R:R
  → ccxt (or SimulatedExchange if placeholder keys)
  → journal open_trade; mark session traded (audit); snapshot active orders
```

On each **1m** Bybit candle close, `on_candle_close` may **drag** open limits to the latest EVMA (5m-aggregated series). After session end, unfilled dragging orders are cancelled.

### 2. WebSocket `/ws` (backend → UI)

~1 Hz broadcast: `session_status`, per-coin `indicators`, `active_orders`, `exchange_monitor` (ccxt positions/orders). Requires `?password=` matching `KINETIC_BACKEND_PASSWORD`.

### 3. Position & open-order monitoring (hybrid)

**Primary (backend connected):** `exchange_monitor` snapshot in `/ws` → `App.jsx` positions/limits tables. Stale fallback: `POST /api/exchange/refresh-monitor`.

**Fallback (WS offline):** Browser `exchangeAPI.fetchPositions` / `fetchOpenOrders` → signed exchange REST (± CF worker) → on failure `GET /api/exchange/positions|open-orders`. Bybit private WS when backend down.

### 4. Manual exchange writes (UI → backend → ccxt)

```
exchangeAPI.closePosition | cancelOrder | amendOrder | createLimitOrder | setPositionTPSL | cancelOrdersBatch
  → POST /api/exchange/*
  → exchange_ops → create_async_exchange(account) → ccxt
```

Requires authenticated backend (password in Settings).

---

## Do-not-break rules

1. **Session limits** — Per-account `max_positions` (default 3) and one active managed order per coin. Intent time windows in `session.py`. Enforced in `POST /api/intent`.
2. **Strategy parity** — Backend `backend/core/strategy.py` must stay aligned with `src/market/indicator/evmaDualStops.js`.
3. **Single uvicorn worker** — `deploy/systemd/kinetic-backend.service` uses `--workers 1`. Active orders and session state are in-memory.
4. **5m aggregation** — Bot uses `data_feed_manager.get_candles(coin)` default `5m`; strategy JSON `timeframe` should stay consistent.
5. **Password auth** — Do not remove middleware from `/api/*` without a replacement; frontend and kinetic-ops depend on `X-Kinetic-Password`.
6. **Exchange monitor** — WS payload `exchange_monitor` is the primary positions/limits source when connected; keep poll interval and stale fallback behavior coherent with `App.jsx`.

---

## Common tasks → files

| Task | Files |
|------|--------|
| Change session hours / intent window | `backend/core/session.py` |
| Adjust EVMA/ATR/stop params per coin | `backend/config/strategy_*.json`, `POST /api/strategy/config` |
| Add account or risk mode | `backend/config/accounts.json`, `backend/core/accounts_config.py`, `backend/core/risk.py` |
| Change intent sizing / dragging | `backend/core/execution.py`, `backend/core/risk.py` |
| Exchange monitor / WS snapshot | `backend/core/exchange_monitor.py`, `main.py` ws_broadcaster_loop |
| New REST endpoint | `backend/main.py` |
| Exchange write behavior | `backend/core/exchange_ops.py`, `backend/core/exchange_factory.py` |
| UI layout / intents / monitoring | `src/App.jsx`, `src/intentFormHelpers.js` |
| Chart indicators | `src/market/indicator/evmaDualStops.js`, `src/components/TradingChart.jsx` |
| Browser read signing | `src/exchangeAPI.js` |
| CORS proxy | `cloudflare-worker/worker.js` |
| Deploy backend | `deploy/systemd/kinetic-backend.service`, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Deploy frontend | `vite.config.js` (`base: '/tcl/'`), `npm run deploy` |
| Ops: sync logs / update accounts | `kinetic-ops/` (`python -m kinetic_ops`) |

---

## Further reading

- [docs/PRE_DEPLOYMENT_REVIEW.md](docs/PRE_DEPLOYMENT_REVIEW.md) — last review before deployment (2026-06-23)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/CONFIG.md](docs/CONFIG.md)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/API.md](docs/API.md)
- [docs/STRATEGY.md](docs/STRATEGY.md)
- [docs/OPERATIONS.md](docs/OPERATIONS.md)
- [docs/OPERATOR.md](docs/OPERATOR.md)

---

## Status

| Item | State |
|------|--------|
| Coins | BTC, ETH, SOL |
| Exchanges | Bybit (demo/live), Hyperliquid |
| Backend hosting | Oracle free tier (documented target) |
| Frontend hosting | GitHub Pages `/tcl/` |
| Backend auth | Password on `/api/*` and `/ws` |
| Position monitoring | WS `exchange_monitor` + browser/REST fallbacks |
| Mock mode | Removed (backend-only accounts) |
| Docs refreshed | 2026-06-23 |
