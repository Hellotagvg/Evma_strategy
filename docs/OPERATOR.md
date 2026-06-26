# Operator guide

Human-in-the-loop workflow for **Kinetic Terminal** and **Antigravity Trading System**. The operator approves trade **intents** during UK sessions; the backend places and drags limit entries on the EVMA line, manages TP/SL, and journals outcomes. The dashboard is for **monitoring and configuration**, not discretionary market entry for the edge.

See also: [CONFIG.md](CONFIG.md), [STRATEGY.md](STRATEGY.md), [OPERATIONS.md](OPERATIONS.md), [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md).

---

## Strategy (operator mental model)

| Layer | What it is |
|-------|------------|
| **Signal / levels** | EVMA indicator with dual dynamic ATR stops — per-coin params in `backend/config/strategy_{btc,eth,sol}.json` |
| **Session** | UK morning (06:00–08:00) and evening (17:00–22:00); intents from 15 minutes before session start through session end |
| **Operator choice** | Account, coin (BTC / ETH / SOL), direction (LONG / SHORT), risk (per account rules); R:R often from `fixed_tp_rr_by_coin` |
| **Automation** | Limit on EVMA, drag on each **1m** candle close (levels from **5m** aggregated series), TP/SL from risk engine; unfilled drags cancelled after session end |
| **Dashboard role** | Multi-account positions & limits (via WS `exchange_monitor`), settings, logs, journal |

Chart overlay (`src/market/indicator/evmaDualStops.js`) must match backend `strategy.py`; compare chart to `/ws` indicators if in doubt.

---

## Operator workflow (UK sessions)

1. **Before session** — `GET /readyz`; confirm feed connected, accounts loaded ([OPERATIONS.md](OPERATIONS.md)). Open Kinetic Terminal; verify Settings URL and password; green WS status.
2. **During intent window** — Morning: 05:45–08:00 UK; evening: 16:45–22:00 UK. UI shows **Accepting Intents** when `is_accepting_intents` is true.
3. **Select account** — Strategy tab → pick account card (enabled row from `accounts.json`).
4. **Configure intent** — Coin, LONG/SHORT. Form adapts: fixed risk = locked panel; adjustable = slider; R:R locked per `fixed_tp_rr_by_coin` or from strategy `tp_rr_options`. See [CONFIG.md — Intent form](CONFIG.md#intent-form-adapts-to-account-risk_config).
5. **Submit intent** — Backend validates time window, per-account limits, risk; starts execution asynchronously.
6. **Monitor** — WS (~1 Hz): session, indicators, active managed orders, **exchange_monitor** positions/limits. Strategy sub-views: **Intent**, **Monitoring**, **Open Executions**.
7. **During drag** — Limit tracks EVMA on 1m closes until fill or session end cancellation.
8. **After session** — Review journal; no new intents until next window (except pre-window for upcoming session).

---

## Intent flow (step-by-step)

```mermaid
sequenceDiagram
  participant Op as Operator (UI)
  participant API as POST /api/intent
  participant SM as SessionManager
  participant RE as RiskEngine
  participant EX as ExecutionEngine
  participant DF as DataFeedManager
  participant ST as strategy.py
  participant X as ccxt / SimulatedExchange
  participant J as journal
  participant WS as /ws broadcaster

  Op->>API: account_id, coin, direction, risk_value, rr
  API->>API: account enabled, coin/direction valid
  API->>RE: validate_risk_input(account, risk_value)
  API->>SM: is_accepting_intents() (time window)
  API->>API: max_positions + per-coin active check
  API->>EX: start_intent (background task)
  API-->>Op: intent_accepted, session_id

  EX->>DF: get_candles(coin) — 5m
  EX->>ST: get_latest_levels
  EX->>RE: calculate_order
  EX->>X: create_order limit @ EVMA
  EX->>J: open_trade
  EX->>SM: mark_session_traded (audit display)
  EX->>EX: ManagedOrder dragging; snapshot

  loop Each 1m candle close
    DF->>EX: on_candle_close(coin)
    EX->>X: edit_order drag to EVMA
  end

  loop After session end_time
    EX->>X: cancel unfilled dragging orders
  end

  WS-->>Op: session_status, indicators, active_orders, exchange_monitor
```

**Validation order in `POST /api/intent`:** coin/direction → account enabled → R:R (`fixed_tp_rr_by_coin` overrides UI) → risk → `is_accepting_intents()` → per-account `max_positions` → no duplicate active coin for account → `start_intent`.

**Cancel dragging:** `POST /api/intent/cancel` with engine `order_id` (no dedicated UI button yet).

---

## Scaling roadmap

| Phase | Exchange | Purpose |
|-------|----------|---------|
| **Now** | Bybit demo | Prop challenges (`demo_trading: true`) |
| **Next** | Bybit live | Funded accounts (`demo_trading: false`) |
| **Later** | Hyperliquid | Own wallets (`exchange: hyperliquid`) |

**Multi-account:** Each `accounts.json` row = one challenge/wallet. Execution and monitoring are per `account_id`. Multiple accounts may trade **in the same UK session** subject to each account's own `max_positions` and per-coin limits.

---

## Session and position limits (current behavior)

### Time windows

| Check | Behavior |
|-------|----------|
| **`is_accepting_intents()`** | True only during 05:45–08:00 and 16:45–22:00 UK |
| **`is_session_execution_active(session_id)`** | Dragging allowed until session `end_time` (08:00 / 22:00 UK) |
| **After session end** | `cancel_dragging_for_ended_sessions` cancels unfilled dragging limits |

### Per-account trade limits

| Check | Behavior |
|-------|----------|
| **`max_positions`** | Default 3; max concurrent managed orders for this account |
| **Per coin** | At most one active managed order per `account_id` + `coin` |
| **`start_intent`** | Also refuses duplicate `account_id` + `coin` + `session_id` |

### Session traded map (audit only)

`mark_session_traded(session_id, coin)` records which coins were traded in a session. `traded_coin` in session status is a **comma-separated display string**. It does **not** block additional intents from other accounts or coins (subject to per-account limits above).

`can_trade(session_id)` exists but **always returns `True`** (deprecated global one-trade-per-session rule removed).

### Historical note

Docs before 2026-06-23 described a **global** one-trade-per-UK-session limit. That is **no longer enforced** in `POST /api/intent`. If product policy requires one trade per account per session only, implement explicit `(session_id, account_id)` tracking — not currently in code.

---

## Robustness checklist

| Priority | Mechanism | Where |
|----------|-----------|--------|
| Session time rules | UK windows, intent pre-window | `session.py`, `POST /api/intent` |
| Per-account caps | `max_positions`, one coin | `POST /api/intent`, `execution.py` |
| Session end cleanup | Cancel unfilled drags | `session_execution_loop`, `execution.py` |
| Feed watchdog | Reconnect if WS silent ~180s | `data_feed_watchdog_loop` |
| Order snapshots | `active_orders_snapshot.json` | `execution.py` |
| Exchange monitor | 5s ccxt poll in WS | `exchange_monitor.py` |
| Password auth | All `/api/*`, `/ws` | `main.py` middleware |
| Single worker | One uvicorn process | `kinetic-backend.service` |

---

## What the dashboard is not

- **Not** the primary path for discretionary market entry aligned with the EVMA edge.
- **Not** relying solely on browser exchange polling when backend WS is up — `exchange_monitor` is primary.
- **Not** a multi-worker horizontally scaled backend without redesigning in-memory state.

For REST details see [API.md](API.md); for deployment see [DEPLOYMENT.md](DEPLOYMENT.md).
