# Trading Dashboard

Semi-automated crypto futures workstation (**Kinetic Terminal** UI + **Antigravity Trading System** backend) for BTC, ETH, and SOL on Bybit and Hyperliquid.

## Quick start

```bash
# Backend (from project root)
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r backend/requirements.txt
set KINETIC_BACKEND_PASSWORD=yellow   # change in production
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
npm install && npm run dev
```

1. Configure `backend/config/accounts.json` with placeholder or real keys — see [docs/CONFIG.md](docs/CONFIG.md).
2. Open the app; in **Settings** set backend URL (`http://localhost:8000`) and password (must match `KINETIC_BACKEND_PASSWORD`).
3. Health: `GET http://localhost:8000/healthz` · Readiness: `GET http://localhost:8000/readyz`

**Notes:**

- Accounts load from `GET /api/accounts` (password required). There is no Mock Mode — backend auth is mandatory for account data.
- `npm run dev` uses Vite's native config loader (`--configLoader native`) for Windows compatibility.
- Optional: `KINETIC_DISABLE_LIVE_FEED=1` skips live Bybit WS feed but keeps WS broadcast, snapshots, and exchange monitor loops.
- Local dev may use port `8010` if 8000 is busy — update Settings URL accordingly.

## Kinetic Ops (optional)

Local CLI for log sync and remote `accounts.json` updates without SSH:

```bash
cd kinetic-ops
pip install -r requirements.txt
python -m kinetic_ops
```

See [kinetic-ops/README.md](kinetic-ops/README.md).

## Documentation

| Doc | Audience |
|-----|----------|
| [docs/PRE_DEPLOYMENT_REVIEW.md](docs/PRE_DEPLOYMENT_REVIEW.md) | **Pre-go-live review** — blockers, checklists, architecture delta |
| [AGENTS.md](AGENTS.md) | AI assistants — repo map, data flows, do-not-break rules |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, session & execution flows |
| [docs/CONFIG.md](docs/CONFIG.md) | accounts.json, strategy JSON, env vars, auth |
| [ACCOUNT_AND_EVMA_CONFIGURATION.md](ACCOUNT_AND_EVMA_CONFIGURATION.md) | Frontend account and EVMA configuration flows |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Lightsail systemd, GitHub Pages `/Evma_strategy/`, kinetic-ops |
| [docs/API.md](docs/API.md) | REST & WebSocket reference |
| [docs/STRATEGY.md](docs/STRATEGY.md) | EVMA dual ATR stops, JS/Python parity |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | readyz, stale feed, journal, rotation |
| [docs/OPERATOR.md](docs/OPERATOR.md) | Operator workflow, intent flow, per-account limits |

**Exchange model:** positions/orders **monitored** via backend `exchange_monitor` in `/ws` (primary), with browser-signed reads and backend REST fallbacks; **writes** via backend `/api/exchange/*` (ccxt).
