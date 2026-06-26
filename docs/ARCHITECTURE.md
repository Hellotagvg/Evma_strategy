# Architecture

**Kinetic Terminal** (frontend) and **Antigravity Trading System** (backend) form a human-in-the-loop futures desk for BTC, ETH, and SOL.

---

## System context

```mermaid
flowchart TB
  subgraph Browser["Browser (GitHub Pages /Evma_strategy/)"]
    UI[App.jsx / Kinetic Terminal]
    EX_READ[exchangeAPI.js reads + fallback]
    EX_WRITE[exchangeAPI.js writes]
    CHART[TradingChart + MarketEngine]
  end

  subgraph Optional["Optional"]
    CF[Cloudflare Worker CORS proxy]
    KOPS[Kinetic Ops CLI]
  end

  subgraph Oracle["Oracle VM — FastAPI :8000"]
    API[backend/main.py]
    SESS[SessionManager]
    FEED[DataFeedManager]
    STRAT[strategy.py]
    EXEC[ExecutionEngine]
    MON[exchange_monitor]
    OPS[exchange_ops ccxt]
    DB[(SQLite journal + logs)]
  end

  subgraph Exchanges["Exchanges"]
    BYBIT[Bybit linear / demo]
    HL[Hyperliquid]
  end

  UI -->|WS /ws?password=| API
  UI -->|REST /api/* + X-Kinetic-Password| API
  KOPS -->|REST /api/*| API
  EX_WRITE -->|POST /api/exchange/*| API
  OPS --> BYBIT
  OPS --> HL
  MON --> BYBIT
  MON --> HL
  EX_READ -->|signed GET| BYBIT
  EX_READ -->|POST info| HL
  EX_READ -.->|optional| CF
  EX_READ -.->|fallback GET /api/exchange/*| API
  CF -.-> BYBIT
  CF -.-> HL
  FEED -->|public WS/REST| BYBIT
  API --> SESS
  API --> FEED
  API --> EXEC
  API --> MON
  EXEC --> STRAT
  EXEC --> OPS
  EXEC --> DB
  CHART -->|public market data| BYBIT
  CHART --> HL
```

---

## Component responsibilities

| Component | Responsibility |
|-----------|----------------|
| **App.jsx** | Tabs (home, positions/limits, strategy, journal), Settings (URL/password, account JSON editor), intent form, consumes `/ws` (indicators, session, active orders, **exchange_monitor**) |
| **intentFormHelpers.js** | Per-account risk and R:R form state (fixed vs adjustable, `fixed_tp_rr_by_coin`) |
| **exchangeAPI.js** | **Reads:** Bybit HMAC + Hyperliquid info from browser, backend REST fallback. **Writes:** POST to `/api/exchange/*`. Bybit private WS when backend offline |
| **TradingChart / market/** | Client-side candles (Bybit or Hyperliquid adapter), `evmaDualStops` overlay |
| **main.py** | HTTP API, password middleware, WebSocket broadcaster, background loops |
| **SessionManager** | UK timezone sessions, intent acceptance windows, traded-session audit map |
| **DataFeedManager** | Bybit public linear feed: warm-up REST, live WS, 1m store, aggregate to 3m/5m, fire callbacks on 1m close |
| **strategy.py** | EVMA + dual ATR stops (must match JS) |
| **ExecutionEngine** | Accept intents, place/drag limits, detect fills, TP/SL, journal, persist `active_orders_snapshot.json` |
| **exchange_monitor.py** | Poll enabled accounts via ccxt every 5s; snapshot in `/ws` |
| **exchange_ops.py** | ccxt async helpers for UI writes and monitor reads |
| **accounts_config.py** | Validate, atomic persist, hot-reload `accounts.json` |
| **journal.py / logger.py** | Trade history and operational logs in SQLite under `backend/data/` |
| **kinetic-ops/** | Operator CLI: log sync, account push, status (password auth) |

---

## Session flow

UK time (`Europe/London`, BST/GMT aware via `pytz`):

| Session | Active window | Intent acceptance |
|---------|---------------|-------------------|
| Morning | 06:00–08:00 | 05:45–08:00 |
| Evening | 17:00–22:00 | 16:45–22:00 |

```mermaid
sequenceDiagram
  participant UI as Frontend
  participant API as FastAPI
  participant SM as SessionManager

  UI->>API: GET /api/session
  API->>SM: get_session_status()
  SM-->>UI: active_session, next_session, is_accepting_intents

  UI->>API: POST /api/intent
  API->>SM: is_accepting_intents?
  API->>API: per-account max_positions + per-coin check
  alt rejected
    API-->>UI: 400 outside hours / limits exceeded
  else accepted
    API->>API: start_intent (background task)
    API-->>UI: intent_accepted
  end
```

Session IDs: `YYYY-MM-DD_morning` or `YYYY-MM-DD_evening`.

**Limit model:** Per-account `max_positions` and one active managed order per coin. `can_trade(session_id)` is deprecated (always `True`). `mark_session_traded` records coins for display only.

---

## Execution flow (automated intent)

```mermaid
sequenceDiagram
  participant UI as Frontend
  participant API as FastAPI
  participant EX as ExecutionEngine
  participant DF as DataFeedManager
  participant ST as strategy.py
  participant CCXT as ccxt / SimulatedExchange
  participant J as journal

  UI->>API: POST /api/intent
  API->>EX: start_intent(Intent)
  EX->>DF: get_candles(coin) default 5m
  EX->>ST: get_latest_levels(candles, config)
  ST-->>EX: evma, upper_stop, lower_stop
  EX->>EX: RiskEngine.calculate_order
  EX->>CCXT: create_order limit @ EVMA
  EX->>J: open_trade (status open)
  EX->>EX: ManagedOrder status=dragging, snapshot

  loop Each 1m candle close
    DF->>EX: on_candle_close(coin)
    EX->>ST: get_latest_levels
    EX->>CCXT: edit_order (drag entry to EVMA)
  end

  loop After session end_time
    API->>EX: cancel_dragging_for_ended_sessions
    EX->>CCXT: cancel unfilled dragging limits
  end
```

**Entry:** limit at EVMA. **Stop:** lower band (LONG) or upper band (SHORT). **Take profit:** from planned R:R in `RiskEngine`. Bybit entries may attach TP/SL on the order via `_build_entry_order_params`.

**Simulation:** accounts with placeholder API keys use `SimulatedExchange` in `execution.py`.

---

## WebSocket update flow

```mermaid
flowchart LR
  LOOP[ws_broadcaster_loop every ~1s]
  DF[DataFeedManager candles]
  ST[get_latest_levels per coin]
  SM[get_session_status]
  EX[get_active_orders]
  EM[exchange_monitor snapshot]
  WS[/ws clients/]

  LOOP --> DF --> ST
  LOOP --> SM
  LOOP --> EX
  LOOP --> EM
  LOOP --> WS
```

Payload shape: see [API.md](API.md#websocket-ws). Clients must connect with `?password=` matching `KINETIC_BACKEND_PASSWORD`.

---

## Position monitoring flow

```mermaid
flowchart TD
  WS[Backend WS connected]
  MON[exchange_monitor in payload]
  STALE{Snapshot stale > 10s?}
  REST[POST /api/exchange/refresh-monitor]
  OFF[Backend WS offline]
  BROWSER[Browser signed REST ± CF worker]
  BFALL[GET /api/exchange/positions|open-orders]
  BWS[Bybit private WS]

  WS --> MON --> UI[App.jsx positions/limits]
  MON --> STALE
  STALE -->|yes| REST --> UI
  OFF --> BROWSER --> UI
  BROWSER -->|fail| BFALL --> UI
  OFF --> BWS --> BROWSER
```

---

## Read vs write exchange architecture

| Operation | Path | Auth |
|-----------|------|------|
| Positions, open orders (primary) | Backend `exchange_monitor` → `/ws` | Server ccxt keys in `accounts.json` |
| Positions, open orders (fallback) | Browser → exchange REST | API key in browser (from `/api/accounts`) |
| Positions, open orders (fallback 2) | Browser → `GET /api/exchange/*` | `X-Kinetic-Password` |
| Close, cancel, amend, place limit, TP/SL, batch cancel | Browser → `/api/exchange/*` → ccxt | Server keys + password |
| Bot limit drag / fill | ExecutionEngine → ccxt | Same server keys |
| Public klines (bot + chart) | Bybit public / HL public | None |

This hybrid keeps monitoring reliable via server-side ccxt while preserving browser-signed reads as fallback when the backend WebSocket is down.

---

## Authentication

| Surface | Mechanism |
|---------|-----------|
| `/api/*` | `X-Kinetic-Password` header or `?password=` query |
| `/ws` | `?password=` query param |
| `/healthz`, `/readyz` | No auth |

Set `KINETIC_BACKEND_PASSWORD` on the server; default `yellow` is for local dev only.
