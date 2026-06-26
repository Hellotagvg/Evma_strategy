# Configuration

All persistent backend config lives under `backend/config/`. Runtime data (SQLite, snapshots) under `backend/data/`.

---

## Backend authentication

| Variable | Default | Effect |
|----------|---------|--------|
| `KINETIC_BACKEND_PASSWORD` | `yellow` | Required on all `/api/*` routes (`X-Kinetic-Password` or `?password=`) and `/ws` (`?password=`) |

Set on Oracle via systemd `Environment=` or `EnvironmentFile`. Match the password in Kinetic Terminal **Settings** and `kinetic-ops` `.env`.

`/healthz` and `/readyz` are unauthenticated.

---

## `accounts.json`

### One row = one challenge or wallet

Each object in the `accounts` array represents a **single trading account** on an exchange: typically one prop-firm challenge, one funded Bybit account, or one Hyperliquid wallet. Give each row its own `id`, `enabled` flag, `risk` block, `max_positions`, and optional `fixed_tp_rr_by_coin`. The backend builds one ccxt client per row; `ExecutionEngine` routes intents and managed orders by `account_id`.

Scaling path (see [OPERATOR.md](OPERATOR.md)):

| Stage | `exchange` | Typical use |
|-------|------------|-------------|
| Now | `bybit` + demo | Challenge practice (`demo_trading: true`) |
| Next | `bybit` live | Funded challenge / live keys (`demo_trading: false`) |
| Later | `hyperliquid` | Own capital, separate wallets |

**Trade limits (per account):** `max_positions` (default 3) and at most one active managed order per coin. Not a global one-trade-per-session limit.

Top-level shape:

```json
{
  "accounts": [
    {
      "id": "bybit-demo",
      "name": "Challenge Account 1",
      "exchange": "bybit",
      "enabled": true,
      "credentials": {
        "api_key": "YOUR_API_KEY_HERE",
        "api_secret": "YOUR_API_SECRET_HERE",
        "testnet": false,
        "demo_trading": true
      },
      "risk": {
        "mode": "fixed_usd",
        "value": 100
      },
      "margin_pct": 20,
      "fixed_tp_rr_by_coin": {
        "BTC": 2.5,
        "ETH": 3.5,
        "SOL": 3.5
      },
      "max_leverage": 25,
      "max_position_size_usd": 19500,
      "max_positions": 3
    }
  ]
}
```

### Fields

| Field | Description |
|-------|-------------|
| `id` | Stable account id used in API payloads |
| `name` | Display name in UI |
| `exchange` | `bybit` or `hyperliquid` |
| `enabled` | If `false`, account rejected for intents and exchange writes |
| `credentials` | Exchange-specific secrets (see below) |
| `risk` | Risk mode; loaded as `risk_config` in memory |
| `margin_pct` | Percent of account balance reserved as margin before leverage is applied |
| `fixed_tp_rr_by_coin` | Optional per-coin R:R override for intents (overrides UI `rr`) |
| `max_leverage` | Hard cap used by `RiskEngine` after margin sizing |
| `max_position_size_usd` | Hard notional cap |
| `max_positions` | Max concurrent active managed orders for this account (default: 3) |

### Credentials

Use placeholders in repos and docs (`YOUR_API_KEY_HERE`, etc.). Never commit real keys.

**Bybit — demo (current default for challenges)**

```json
"credentials": {
  "api_key": "YOUR_API_KEY_HERE",
  "api_secret": "YOUR_API_SECRET_HERE",
  "testnet": false,
  "demo_trading": true
}
```

- `demo_trading: true` — Bybit **demo trading** API (`api-demo.bybit.com`)
- `testnet: false` — normal when using demo trading (not the old testnet sandbox)

**Bybit — live (after challenge funded)**

```json
"credentials": {
  "api_key": "YOUR_API_KEY_HERE",
  "api_secret": "YOUR_API_SECRET_HERE",
  "testnet": false,
  "demo_trading": false
}
```

**Hyperliquid (later — own wallets)**

```json
"credentials": {
  "wallet_address": "0xYOUR_WALLET_ADDRESS",
  "api_secret": "YOUR_PRIVATE_KEY_OR_SIGNING_SECRET"
}
```

Placeholder values (`YOUR_*`, `PLACEHOLDER`, etc.) cause the **execution engine** to use `SimulatedExchange` for that account. `exchange_ops` (manual writes) will error with a clear message instead.

### Risk modes (`risk.mode` / `risk_config.type`)

| Mode | Meaning | UI adjustment |
|------|---------|-----------------|
| `fixed_pct` | Fixed % of balance risked | Must match `value` exactly |
| `fixed_usd` | Fixed USD risk | Must match `value` exactly |
| `adjustable_pct` | % within min/max/step | Slider on intent form |
| `adjustable_usd` | USD within min/max/step | Slider on intent form |

Update risk at runtime: `POST /api/accounts/risk` (persists to `accounts.json`).

Update any account field at runtime (no restart): `PUT /api/accounts/{account_id}`, `PUT /api/accounts/config`, or Settings → Account configuration. After a manual file edit on the server, call `POST /api/accounts/reload` or use **kinetic-ops** `update-accounts`. See [API.md](API.md) and [OPERATIONS.md](OPERATIONS.md).

### Intent form adapts to account `risk_config`

On the **Strategy** tab, after selecting an account card, the intent form reads that row's `risk` (exposed as `risk_config` from `GET /api/accounts`). Risk and R:R controls are not shared across accounts. Logic lives in `src/intentFormHelpers.js`.

| `risk_config.type` | UI behavior | Value sent as `risk_value` |
|--------------------|-------------|----------------------------|
| `fixed_pct` | Locked panel (red tint), disabled slider | Exact `value` from config |
| `fixed_usd` | Same, message uses `$` amount | Exact `value` |
| `adjustable_pct` | Slider between `min` / `max` with `step` | User's slider value |
| `adjustable_usd` | Slider between `min` / `max` with `step` | User's slider value |

**R:R**

| Condition | UI behavior | Value sent as `rr` |
|-----------|-------------|-------------------|
| `fixed_tp_rr_by_coin[coin]` set on account | Amber locked panel; no slider | Fixed map value (backend also overrides) |
| No per-coin override | Slider from `tp_rr_options` in `GET /api/strategy/config?coin=` | User selection (snapped to allowed options) |

Coin and direction fields are unchanged. Submit is disabled when the account `enabled` flag is false or the session is not accepting intents.

### Margin sizing

- `margin_budget_usd = balance * margin_pct / 100`
- Raw notional derived from trade risk and stop distance
- Final order size capped by `margin_budget_usd * max_leverage` and `max_position_size_usd`
- `leverage_used` written to journal from capped order size

---

## `dashboard.json`

Server-side frontend runtime config:

```json
{
  "cloudflare_worker_url": "https://your-worker.workers.dev/"
}
```

- Served via `GET /api/dashboard/config`
- Updated via `POST /api/dashboard/config`
- UI loads this on startup (password required); used by `exchangeAPI.js` for browser read CORS proxy

---

## Strategy JSON (`strategy_{btc,eth,sol}.json`)

Per-coin file, e.g. `backend/config/strategy_btc.json`:

```json
{
  "coin": "BTC",
  "symbol": "BTCUSDT",
  "evma_length": 75,
  "evma_source": "hlc3",
  "atr_length": 100,
  "baseline_volatility": 0.005,
  "min_stop_pct": 0.0055,
  "max_stop_pct": 0.009,
  "timeframe": "5m",
  "tp_rr_options": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
}
```

Runtime updates: `GET/POST /api/strategy/config?coin=BTC` (POST updates lengths and stop fields only; preserves symbol and `tp_rr_options`). No Strategy-tab UI for EVMA params yet — use API or edit JSON on server.

---

## Frontend environment

| Variable | Used by | Default |
|----------|---------|---------|
| `VITE_BACKEND_URL` | `src/exchangeAPI.js` build-time default for backend POST | `http://localhost:8000` |

Set when building for production:

```bash
VITE_BACKEND_URL=https://your-oracle-host:8000 npm run build
```

**Runtime:** `App.jsx` uses Settings → `localStorage` `kinetic_backend_url` and `kinetic_backend_password` for most backend calls. Align build-time `VITE_BACKEND_URL` with production host for exchange write helpers.

---

## Frontend Settings (localStorage)

| Key | Purpose |
|-----|---------|
| `kinetic_backend_url` | Backend base URL (default `http://localhost:8000`) |
| `kinetic_backend_password` | Must match `KINETIC_BACKEND_PASSWORD` |

Accounts and CF worker URL come from the backend (`/api/accounts`, `/api/dashboard/config`), not localStorage.

---

## Backend environment

| Variable | Effect |
|----------|--------|
| `KINETIC_BACKEND_PASSWORD` | API and WS auth (default `yellow`) |
| `KINETIC_DISABLE_LIVE_FEED=1` | Skips live Bybit WS feed; warms candles from REST; keeps WS broadcast, exchange monitor, snapshots, lifecycle loops. `/readyz` may show feed not connected — degraded mode only |

---

## Cloudflare Worker

- Code: `cloudflare-worker/worker.js`
- URL stored in `backend/config/dashboard.json`, loaded by UI
- Proxies browser requests: `GET https://worker.example/?url=https://api.bybit.com/...`
- Allowlisted hosts in worker: `api.bybit.com`, `api-testnet.bybit.com`, `api.hyperliquid.xyz`, `api.hyperliquid-testnet.xyz`
- **Not yet allowlisted:** `api-demo.bybit.com` — add before using worker with Bybit demo trading
- Forwards signing headers (`X-BAPI-*`) on POST
- Does **not** proxy backend `/api/exchange/*`

Deploy separately; set `ALLOWED_ORIGIN` to your GitHub Pages URL in production.

---

## Kinetic Ops

| Source | Variable |
|--------|----------|
| `kinetic-ops/.env` | `KINETIC_BACKEND_URL`, `KINETIC_BACKEND_PASSWORD` |
| `kinetic-ops/config.json` | `backend_url`, `password` |
| `kinetic-ops/config/accounts.json` | Local copy for `update-accounts` (gitignored) |

See [kinetic-ops/README.md](../kinetic-ops/README.md).

---

## Backend paths (reference)

| Path | Purpose |
|------|---------|
| `backend/data/journal.db` | Trade journal |
| `backend/data/logs.db` | System logs |
| `backend/data/active_orders_snapshot.json` | Crash recovery for managed orders |

---

## Secrets in git

- Root `.gitignore` does **not** exclude `backend/config/accounts.json` — add it before pushing to a remote
- `kinetic-ops/.gitignore` excludes `.env` and `config/accounts.json`
- Rotate exchange keys if they were ever committed
