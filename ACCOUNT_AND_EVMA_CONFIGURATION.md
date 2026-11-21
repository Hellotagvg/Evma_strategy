# Account and EVMA Configuration

Agent reference for changing account credentials or per-coin EVMA strategy settings through Kinetic Terminal and the Antigravity backend.

## Account configuration

### Source of truth and UI flow

- The backend account list is stored in `backend/config/accounts.json`. It contains API credentials and must remain local/private.
- In **Settings > Account configuration**, the frontend loads the full list from authenticated `GET /api/accounts` into an editable JSON draft.
- **Apply to backend** sends the full array to `PUT /api/accounts/config`. The backend validates it, checks that removed or renamed accounts have no active managed orders, persists it, and hot-reloads the runtime account list without a restart.
- **Reload from disk** calls `POST /api/accounts/reload`; use this after editing `accounts.json` directly on the server.
- The frontend refreshes its account list every five seconds, so accounts changed by another tool appear on the page shortly after the backend accepts the change.

The editor replaces the full account array. Preserve every account row you want to keep when applying changes.

### Account fields and credentials

Each row is one exchange account or wallet and needs a unique, stable `id`, `name`, supported `exchange`, `credentials`, and `risk` configuration. Supported exchanges are `bybit` and `hyperliquid`; `enabled`, `max_positions`, margin/leverage limits, and `fixed_tp_rr_by_coin` are optional configuration fields.

- **Bybit:** `credentials.api_key`, `credentials.api_secret`, plus `demo_trading` or `testnet` as appropriate.
- **Hyperliquid:** `credentials.wallet_address` (or `api_key` for the address) and `credentials.api_secret` (or `secret`) for the signing secret.
- **Risk:** `risk.mode` is `fixed_pct`, `fixed_usd`, `adjustable_pct`, or `adjustable_usd`. Fixed modes need `value`; adjustable modes need `min`, `max`, and `step`.

Use placeholders in examples only. Never commit real API keys, private keys, wallet secrets, or local account files. The root `.gitignore` excludes `backend/config/accounts.json`, `kinetic-ops/.env`, and `kinetic-ops/config.json`; keep live account files local. Commit only the placeholder `backend/config/accounts.example.json`.

### Account API and Kinetic Ops

All `/api/*` endpoints require `X-Kinetic-Password` (or the legacy `?password=` parameter). The frontend uses the header; the same password must match `KINETIC_BACKEND_PASSWORD` on the server.

| Operation | Endpoint / tool | Behavior |
|---|---|---|
| Read current accounts | `GET /api/accounts` | Returns full account rows, including credentials, to authenticated callers. |
| Add one account | `POST /api/accounts` | Rejects a duplicate `id`. |
| Patch one account | `PUT /api/accounts/{account_id}` | Merges supplied `credentials` and `risk` fields with the existing objects. |
| Replace all accounts | `PUT /api/accounts/config` | Validates and hot-reloads the complete account array. |
| Reload server file | `POST /api/accounts/reload` | Re-reads `backend/config/accounts.json`, validates, and hot-reloads it. |
| Push from operator workstation | Kinetic Ops `update-accounts` | Validates locally, checks backend managed-order status, shows a diff/confirmation, then sends the full array. |

Example Kinetic Ops invocation (run from `kinetic-ops/`):

```powershell
python kops.py update-accounts --file .\config\accounts.json
```

Use `--yes` only when intentionally skipping the confirmation after reviewing the account file. Do not remove or rename an account while it has active managed orders; the backend rejects that configuration change.

### Credential security

`GET /api/accounts` returns API credentials to authenticated callers because the browser may need credentials for signed exchange reads. Keep the backend behind HTTPS and restrict network access. Use a strong production backend password. The account JSON editor holds its draft in page memory; backend URL/password are stored separately in browser local storage. Do not paste secrets into screenshots, logs, chat, or source control.

Placeholder credentials make managed strategy intents use `SimulatedExchange`; manual `exchange_ops` reads and writes that require exchange authentication report missing-credential errors. An enabled placeholder account is not connected to a real exchange.

## EVMA strategy settings from the frontend

### UI and API flow

The separate **Settings > EVMA strategy settings** form selects BTC, ETH, or SOL and loads that coin's backend settings with `GET /api/strategy/config?coin=BTC` (replace `BTC` with the selected coin). **Reload values** fetches the current backend values again; **Save EVMA settings** sends the editable fields to `POST /api/strategy/config`.

The form edits:

- `evma_length` - positive integer.
- `atr_length` - positive integer.
- `baseline_volatility` - positive value.
- `min_stop_pct` and `max_stop_pct` - non-negative stop distances, with minimum no greater than maximum.

The UI displays volatility and stop distances as percentages. The API and strategy JSON store them as fractions: for example, `0.005` is `0.5%`. The form divides percentage inputs by 100 when saving and multiplies values by 100 when loading.

The backend only accepts BTC, ETH, and SOL, validates submitted values, and preserves other strategy config fields such as `symbol`, `evma_source`, `timeframe`, and `tp_rr_options`. It persists the update to `backend/config/strategy_{btc,eth,sol}.json`. The WebSocket broadcaster reloads the coin's strategy config during its periodic indicator calculation, so a backend restart is not required.

Values checked on 2026-11-17 were the same for all three coins. Always load the selected coin from the API; these values can change:

| Setting | Stored value | Form display |
|---|---:|---:|
| EVMA length | 75 | 75 |
| ATR length | 100 | 100 |
| Baseline volatility | 0.005 | 0.5% |
| Minimum stop distance | 0.0055 | 0.55% |
| Maximum stop distance | 0.009 | 0.9% |

`evma_source` is currently `hlc3` and `timeframe` is `5m`; they are read-only context in this form. Do not add controls for fields the backend endpoint does not accept without updating backend validation and strategy parity with `src/market/indicator/evmaDualStops.js`.

## Related references

- [Configuration reference](docs/CONFIG.md)
- [API reference](docs/API.md)
- [Kinetic Ops guide](kinetic-ops/README.md)
- [Operator workflow](docs/OPERATOR.md)
- [Strategy parity and parameters](docs/STRATEGY.md)
