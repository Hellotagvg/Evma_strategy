# Strategy — EVMA dual dynamic ATR stops

The edge used for both **chart display** and **bot execution** is EVMA (exponential volume-weighted moving average) with **dual dynamic ATR stops**: stop distance widens when normalized volatility is high, within configured min/max percentages of EVMA.

---

## Implementations (must stay in sync)

| Location | Language | Entry points |
|----------|----------|--------------|
| `backend/core/strategy.py` | Python | `calculate_indicators`, `get_latest_levels`, `load_strategy_config` |
| `src/market/indicator/evmaDualStops.js` | JavaScript | `calculateEvmaDualStops` |

Python explicitly mirrors JavaScript EMA initialization (`result[0] = values[0]`, then standard EMA recursion). Use the same float semantics; do not switch to Decimal in one side only.

---

## Algorithm (shared logic)

### 1. Source price (hlc3)

\[
\text{src} = \frac{H + L + C}{3}
\]

### 2. Volume series

\[
\text{volSeries} = \text{volume} + 1
\]

### 3. True range & ATR

For each bar, true range is max of:

- \(H - L\)
- \(|H - \text{prevClose}|\)
- \(|L - \text{prevClose}|\)

First bar: JS uses `prevClose = close` on bar 0; Python uses \(H-L\) only on bar 0 (equivalent effect).

ATR = EMA(true range, `atr_length`).

### 4. EVMA

\[
\text{EVMA} = \frac{\text{EMA}(\text{src} \times \text{volSeries}, L)}{\text{EMA}(\text{volSeries}, L)}
\]

\(L\) = `evma_length` (JSON) / `evmaLen` (JS constants).

### 5. Dynamic stop distance

\[
\text{atrPct} = \frac{\text{ATR}}{\text{EVMA}}
\]

\[
\text{normAtr} = \mathrm{clamp}\left(\frac{\text{atrPct}}{\text{baseline\_volatility}}, 0, 1\right)
\]

\[
\text{dist} = \text{min\_stop\_pct} + (\text{max\_stop\_pct} - \text{min\_stop\_pct}) \times \text{normAtr}
\]

\[
\text{upperStop} = \text{EVMA} \times (1 + \text{dist}),\quad
\text{lowerStop} = \text{EVMA} \times (1 - \text{dist})
\]

### 6. Take profit (chart only in JS)

JS also computes optional TP bands for chart overlays:

```javascript
longTp  = evma * (1 + dist * tpMult)
shortTp = evma * (1 - dist * tpMult)
```

`tpMult` from `TP_MULTIPLIERS` in `src/market/constants.js` (BTC 2.5, ETH 3.5, SOL 3.5). The **bot** uses account `fixed_tp_rr_by_coin` and `RiskEngine` for actual TP prices on orders, not `TP_MULTIPLIERS` directly.

---

## Parameter mapping (JSON ↔ JS)

| `strategy_*.json` | JS `DEFAULT_INDICATOR_CONFIG` / per-chart |
|-------------------|-------------------------------------------|
| `evma_length` | `evmaLen` |
| `atr_length` | `atrLen` |
| `baseline_volatility` | `baselineVol` |
| `min_stop_pct` | `minStopPct` |
| `max_stop_pct` | `maxStopPct` |

Production JSON (all three coins today) uses longer periods than the JS defaults:

- `evma_length`: 75, `atr_length`: 100
- `min_stop_pct`: 0.0055, `max_stop_pct`: 0.009
- `baseline_volatility`: 0.005
- `timeframe`: `"5m"`

Charts may still use `DEFAULT_INDICATOR_CONFIG` until wired to backend strategy config — **compare `/ws` `indicators` to chart overlay** before trusting chart-only levels for decisions.

---

## Timeframes

| Layer | Timeframe | Notes |
|-------|-----------|-------|
| **DataFeedManager** | Stores **1m** from Bybit WS; aggregates to **3m**, **5m** | Default `get_candles(coin)` → **5m** |
| **Execution / WS broadcaster** | **5m** series | Matches `timeframe` in strategy JSON |
| **on_candle_close** | Fires every **1m** close | Recomputes levels from latest 5m aggregate each minute |
| **Frontend MarketEngine** | User-selectable: 1m, 3m, 5m, 15m | `SUPPORTED_TIMEFRAMES` in `constants.js`; aggregates from 1m store |

Changing bot timeframe requires:

1. Update `timeframe` in `strategy_*.json`
2. Pass that timeframe into `get_candles(coin, timeframe)` in `main.py`, `execution.py`
3. Align chart config if you want visual parity

---

## How the bot uses levels

| Direction | Entry limit | Initial stop |
|-----------|-------------|----------------|
| LONG | EVMA | `lower_stop` |
| SHORT | EVMA | `upper_stop` |

On each 1m close while `status == dragging`, entry is amended toward the new EVMA; SL/TP and size are recalculated via `RiskEngine`.

---

## Changing strategy safely

1. Edit `backend/config/strategy_<coin>.json` or use `POST /api/strategy/config`
2. Update `evmaDualStops.js` only if you change formulas (not just parameters)
3. Compare last bar: `/ws` indicators vs chart overlay vs `GET /api/strategy/config`
4. Run a paper/sim account (placeholder keys → `SimulatedExchange`) before live

No Strategy-tab UI exists for EVMA parameter editing — use API, server file edit, or future UI work.

See [OPERATIONS.md](OPERATIONS.md) for monitoring indicator errors on `/ws`.
