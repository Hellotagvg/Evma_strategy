# Deployment

Target layout: **Oracle Cloud free tier** VM for the backend, **GitHub Pages** for the static frontend, optional **Cloudflare Worker** for browser CORS, **Kinetic Ops** on the operator workstation.

---

## Oracle Cloud (backend)

Typical Always Free shape (verify in your tenancy):

- **3 OCPU** (Ampere) or equivalent
- **12 GB** RAM
- **50 GB** boot volume

### Server setup (outline)

1. Create an Oracle Linux or Ubuntu instance; open inbound **TCP 8000** (or 443 behind nginx) from your IP or VPN only.
2. Clone the repo to e.g. `/opt/trading-dashboard`.
3. Python venv and dependencies:

```bash
cd /opt/trading-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

4. Place `backend/config/accounts.json` on the server with real keys — **never commit** to git.
5. Set backend password:

```bash
export KINETIC_BACKEND_PASSWORD='use-a-long-random-secret'
```

6. Optional `backend/config/dashboard.json` with Cloudflare Worker URL.
7. Ensure `backend/data/` is writable.

### systemd

Unit file: `deploy/systemd/kinetic-backend.service`

```ini
ExecStart=/opt/trading-dashboard/.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

**Add password** via drop-in (recommended):

```bash
sudo mkdir -p /etc/systemd/system/kinetic-backend.service.d
sudo tee /etc/systemd/system/kinetic-backend.service.d/password.conf <<'EOF'
[Service]
Environment=KINETIC_BACKEND_PASSWORD=your-production-secret
EOF
```

Install:

```bash
sudo cp deploy/systemd/kinetic-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kinetic-backend
sudo systemctl start kinetic-backend
journalctl -u kinetic-backend -f
```

**Keep `--workers 1`.** Active orders, session state, and exchange monitor cache are in-process.

Adjust `User`, `WorkingDirectory`, and `ExecStart` paths to match your install.

### Health checks

| Endpoint | Use |
|----------|-----|
| `GET /healthz` | Liveness — process up (no auth) |
| `GET /readyz` | Readiness — startup self-checks, feed running, WS connected, accounts loaded |

Use `/readyz` for monitoring alerts before relying on the bot during sessions.

### Pre-session verification

```bash
curl -s http://localhost:8000/readyz | jq .
curl -s -H "X-Kinetic-Password: $KINETIC_BACKEND_PASSWORD" http://localhost:8000/api/execution/status
```

---

## GitHub Pages (frontend)

- `vite.config.js` sets `base: '/Evma_strategy/'` — app at `https://<user>.github.io/Evma_strategy/`
- Build and publish:

```bash
npm install
VITE_BACKEND_URL=https://YOUR_ORACLE_HOST:8000 npm run build
npm run deploy
```

`package.json` runs `predeploy` → `build`, then `gh-pages -b frontend -d dist`.

### Post-deploy checklist

- [ ] `VITE_BACKEND_URL` points to production API host
- [ ] Settings in UI: production backend URL + **same** password as `KINETIC_BACKEND_PASSWORD`
- [ ] CORS: backend allows `*` origins today — restrict when using TLS + known frontend origin
- [ ] WebSocket: browser reaches `wss://YOUR_HOST/ws?password=...` (derived from Settings URL)
- [ ] `dashboard.json` on server has CF worker URL if browser CORS blocks exchange reads
- [ ] CF worker allowlists `api-demo.bybit.com` if using Bybit demo through worker
- [ ] `accounts.json` on server has keys for accounts you trade
- [ ] Firewall restricts port 8000 to operator IPs or VPN

---

## TLS reverse proxy (recommended)

Terminate HTTPS on nginx/Caddy and proxy to `127.0.0.1:8000`. Expose `wss` for `/ws` on the same host. Frontend Settings then use `https://your-domain` — WS URL is derived automatically.

---

## Cloudflare Worker

1. Deploy `cloudflare-worker/worker.js` (Wrangler: `cloudflare-worker/wrangler.toml`)
2. Add `api-demo.bybit.com` to `ALLOWED_HOSTS` if using Bybit demo
3. Set `ALLOWED_ORIGIN` to `https://<user>.github.io` (not `*` in production)
4. Put worker URL in `backend/config/dashboard.json`:

```json
{
  "cloudflare_worker_url": "https://your-worker.workers.dev/"
}
```

---

## Kinetic Ops (operator machine)

Deploy the CLI on your laptop or ops host — not on Oracle unless you prefer.

```bash
cd kinetic-ops
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env        # set KINETIC_BACKEND_URL and KINETIC_BACKEND_PASSWORD
python -m kinetic_ops
```

### Production workflows

| Task | Command |
|------|---------|
| Check connectivity | `python kops.py status --json` |
| Pull logs (30-day window) | `python kops.py sync-logs --yes` |
| Push accounts (when flat) | `python kops.py update-accounts --file ./config/accounts.json --yes` |

`update-accounts` aborts if `active_managed_orders > 0`. See [kinetic-ops/README.md](../kinetic-ops/README.md).

---

## What runs where

| Artifact | Host |
|----------|------|
| `dist/` static assets | GitHub Pages `/Evma_strategy/` |
| FastAPI + uvicorn | Oracle VM :8000 |
| SQLite DBs | Oracle `backend/data/` |
| Bybit market WS (bot feed) | Oracle outbound to `stream.bybit.com` |
| Exchange monitor (ccxt poll) | Oracle → exchanges every 5s |
| Position UI (primary) | Browser ← `/ws` `exchange_monitor` |
| Kinetic Ops | Operator workstation → Oracle REST |

---

## Go-live sequence

See [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md) Section 9 for the ordered checklist (rotate secrets → backend → TLS → worker → frontend → ops test → demo intent).
