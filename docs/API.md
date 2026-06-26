# HTTP & WebSocket API

Base URL: `http://localhost:8000` (development) or your Oracle host in production.

OpenAPI title: **Antigravity Trading System** v1.0.0 — interactive docs at `/docs` when the server is running.

---

## Authentication

All routes under `/api/*` require the backend password unless noted.

| Method | Header | Query (alternative) |
|--------|--------|-------------------|
| REST | `X-Kinetic-Password: <secret>` | `?password=<secret>` |
| WebSocket `/ws` | — | `?password=<secret>` required on connect |

Password source: environment variable `KINETIC_BACKEND_PASSWORD` (default `yellow` — change in production).

**Unauthenticated:** `GET /healthz`, `GET /readyz`

**401 response:**

```json
{ "detail": "Unauthorized" }
```

---

## Health

### `GET /healthz`

Liveness probe. No auth.

**Response 200**

```json
{
  "ok": true,
  "service": "antigravity-trading-backend"
}
```

---

### `GET /readyz`

Readiness probe. No auth. `ready` is true only when all checks pass.

**Response 200**

```json
{
  "ready": true,
  "checks": {
    "startup_self_checks_ok": true,
    "feed_running": true,
    "feed_ws_connected": true,
    "accounts_loaded": true
  },
  "startup_self_check_errors": [],
  "feed": {
    "running": true,
    "ws_connected": true,
    "last_ws_message_ts": "2026-06-23T12:00:00+00:00",
    "last_closed_candle_ts": { "BTC": "...", "ETH": "...", "SOL": "..." },
    "now_ts": "..."
  },
  "accounts_count": 1
}
```

With `KINETIC_DISABLE_LIVE_FEED=1`, `feed_running` / `feed_ws_connected` may be false while other loops still run.

Startup self-checks fail if `accounts.json` is missing, invalid JSON, or `backend/data/` is not writable.

---

## Dashboard config

### `GET /api/dashboard/config`

Returns server-side UI runtime config.

**Response 200**

```json
{
  "cloudflare_worker_url": "https://your-worker.workers.dev/"
}
```

---

### `POST /api/dashboard/config`

**Body**

```json
{
  "cloudflare_worker_url": "https://your-worker.workers.dev/"
}
```

**Response 200**

```json
{
  "status": "success",
  "config": { "cloudflare_worker_url": "..." }
}
```

---

## Accounts

### `GET /api/accounts`

Returns the full `accounts` array from `accounts.json` (including credential shapes). **Protect this endpoint** — any caller with the password receives API key material.

**Response 200:** `Array<Account>`

---

### `POST /api/accounts/risk`

Update risk configuration for one account; persists to `accounts.json`.

**Body**

```json
{
  "account_id": "bybit-demo",
  "type": "fixed_pct",
  "value": 1.0,
  "min": null,
  "max": null,
  "step": null,
  "default": null
}
```

`type`: `fixed_pct` | `fixed_usd` | `adjustable_pct` | `adjustable_usd`

**Response 200:** `{ "status": "success", "account": { } }`

---

### `POST /api/accounts/reload`

Re-read `accounts.json` from disk and hot-reload the execution engine.

**Response 200:** `{ "status": "success", "accounts_count": 1 }`

**Errors 400:** validation errors in `detail.errors`

---

### `PUT /api/accounts/config`

Replace the full `accounts` array. Blocks removal of accounts with active managed orders.

**Body:** `{ "accounts": [ ... ] }`

**Response 200:** `{ "status": "success", "accounts_count": N }`

---

### `POST /api/accounts`

Add one account. Fails with `400` if `id` already exists.

---

### `PUT /api/accounts/{account_id}`

Patch a single account. `credentials` and `risk` are merged with existing values.

---

## Session & intents

### `GET /api/session`

Current UK session metadata.

**Response 200**

```json
{
  "current_time_uk": "2026-06-23T07:30:00+01:00",
  "is_accepting_intents": true,
  "active_session": {
    "id": "2026-06-23_morning",
    "type": "morning",
    "start_time": "...",
    "end_time": "...",
    "traded_coin": "BTC, ETH"
  },
  "next_session": { }
}
```

`traded_coin` is audit/display (comma-separated coins traded this session), not a hard block on new intents.

---

### `POST /api/intent`

Submit a human trade intent; starts background execution.

**Body**

```json
{
  "account_id": "bybit-demo",
  "coin": "BTC",
  "direction": "LONG",
  "rr": 2.5,
  "risk_value": 100
}
```

**Limits enforced:**

- Session time window (`is_accepting_intents`)
- `max_positions` per account
- One active managed order per coin per account
- Risk within account rules; R:R overridden by `fixed_tp_rr_by_coin` when set

**Response 200:** `{ "status": "intent_accepted", "session_id": "..." }`

---

### `POST /api/intent/cancel`

Cancel a managed dragging order by engine `order_id`.

**Body:** `{ "order_id": "trade_..." }`

**Response 200:** `{ "status": "success", "message": "..." }`

---

## Journal & logs

### `GET /api/journal/query`

Query parameters: `coin`, `direction`, `exchange`, `account_id`, `date_from`, `date_to`, `status`, `fields` (comma-separated).

**Response 200:** `Array<TradeRecord>`

---

### `GET /api/logs`

**Query:** `date` optional `YYYY-MM-DD` (UK calendar date)

**Response 200:** `Array<LogEntry>`

---

### `GET /api/execution/status`

Pre-flight for account config changes (kinetic-ops blocks when `active_managed_orders > 0`).

**Response 200**

```json
{
  "active_managed_orders": 0,
  "active_orders": []
}
```

---

## Strategy config

### `GET /api/strategy/config?coin=BTC`

**Response 200:** strategy JSON as dict

---

### `POST /api/strategy/config`

**Body:** `coin`, `evma_length`, `atr_length`, `baseline_volatility`, `min_stop_pct`, `max_stop_pct`

Preserves `symbol`, `tp_rr_options`, etc.

---

## Exchange reads (ccxt fallback)

Used when browser-signed reads fail. Same password auth.

### `GET /api/exchange/positions?account_id=...`

**Response 200:** normalized position array

---

### `GET /api/exchange/open-orders?account_id=...`

**Response 200:** normalized open order array

---

### `POST /api/exchange/refresh-monitor`

Force one `exchange_monitor` poll; returns snapshot.

**Response 200:** same shape as `exchange_monitor` in WebSocket payload

```json
{
  "fetched_at": "2026-06-23T12:00:00+00:00",
  "positions": [],
  "open_orders": [],
  "position_errors": {},
  "order_errors": {},
  "stale": false,
  "poll_age_sec": 2
}
```

---

## Exchange writes (ccxt)

All require enabled account with real credentials.

### `POST /api/exchange/close-position`

Body: `account_id`, `symbol`, `side`, `size`

### `POST /api/exchange/cancel-order`

Body: `account_id`, `symbol`, `order_id`

### `POST /api/exchange/amend-order`

Body: `account_id`, `symbol`, `order_id`, `side`, `price`, `qty`

### `POST /api/exchange/place-limit`

Body: `account_id`, `coin`, `side`, `price`, `qty`

### `POST /api/exchange/set-tpsl`

Body: `account_id`, `symbol`, `side`, `tp`, `sl` (at least one required)

### `POST /api/exchange/cancel-batch`

```json
{
  "orders": [
    { "account_id": "bybit-demo", "symbol": "BTCUSDT", "order_id": "1" }
  ]
}
```

**Response 200:** `{ "status": "ok", "cancelled": N, "errors": [] }`

---

## WebSocket `/ws`

Connect: `ws://host:8000/ws?password=<secret>` (use `wss://` behind TLS).

Server accepts connections; inbound text is ignored (client may send `ping` keepalives). Reconnect with exponential backoff (frontend max 30s).

### Broadcast message (~1 Hz)

```json
{
  "type": "update",
  "timestamp": "2026-06-23T07:30:00+01:00",
  "session_status": { },
  "indicators": {
    "BTC": {
      "price": 65000.0,
      "evma": 64980.0,
      "upper_stop": 65300.0,
      "lower_stop": 64660.0,
      "dist_pct": 0.55,
      "atr_pct": 0.12,
      "norm_atr": 0.24
    }
  },
  "active_orders": [ ],
  "exchange_monitor": {
    "fetched_at": "...",
    "positions": [ ],
    "open_orders": [ ],
    "position_errors": { },
    "order_errors": { },
    "stale": false,
    "poll_age_sec": 3
  }
}
```

Per-coin indicator may be `{ "status": "Warming up" }` or `{ "error": "..." }`.

---

## Error envelope

| Code | Body |
|------|------|
| 401 | `{ "detail": "Unauthorized" }` |
| 400/404 | `{ "detail": "..." }` or `{ "detail": { "errors": ["..."] } }` |
| 500 | `{ "detail": "Internal server error" }` |
