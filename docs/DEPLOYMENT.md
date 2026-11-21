# Deployment

Target layout: **Amazon Lightsail** for the FastAPI backend, **GitHub Pages** for the static frontend, optional **Cloudflare Worker** for browser CORS, and **Kinetic Ops** on the operator workstation.

---

## Amazon Lightsail (backend)

Production host: `evmasystem.duckdns.org` (static IPv4 `13.50.121.203`), Ubuntu 24.04. The current instance is the 2-vCPU, 1-GB Lightsail plan with 1 GB swap configured.

- Source repo: `https://github.com/Hellotagvg/Evma_strategy`, branch `main`.
- Install path: `/opt/trading-dashboard`.
- Service: `kinetic-backend.service`, one Uvicorn worker, bound to `127.0.0.1:8000`.
- Nginx terminates HTTPS at `https://evmasystem.duckdns.org` and proxies HTTP/WebSocket traffic to Uvicorn.
- `/etc/kinetic-backend/backend.env` stores `KINETIC_BACKEND_PASSWORD`; do not commit or print it.
- Live accounts and SQLite state stay on the host under `backend/config/accounts.json` and `backend/data/`. Preserve both during code updates. `accounts.json` is git-ignored.
- Ubuntu firewall allows SSH (22), HTTP (80, for certificate renewal), and HTTPS (443). Lightsail networking must allow these ports too.
- A systemd timer runs Certbot renewal twice a day and reloads Nginx after renewal.

After updating source files, restart and inspect the service:

```bash
sudo systemctl restart kinetic-backend
sudo systemctl status kinetic-backend --no-pager
sudo journalctl -u kinetic-backend -n 100 --no-pager
curl -fsS https://evmasystem.duckdns.org/healthz
curl -fsS https://evmasystem.duckdns.org/readyz
```

`/healthz` only confirms process liveness. Use the authenticated dashboard and `/readyz` to verify feed and account readiness before relying on the system during a session. The feed sends Bybit's application-level heartbeat every 20 seconds and reconnects with backoff after a disconnect; REST polling is a fallback, not a ready WebSocket feed.

Keep `--workers 1`. Active orders, session state, and exchange monitor cache are in-process. The small Lightsail plan is suitable for light use but has limited memory and CPU burst capacity; swap helps with memory spikes but does not add CPU capacity.

---

## GitHub Pages (frontend)

- `vite.config.js` sets `base: '/Evma_strategy/'` — app at `https://<user>.github.io/Evma_strategy/`
- Build and publish:

```bash
npm install
VITE_BACKEND_URL=https://evmasystem.duckdns.org npm run build
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
- [ ] Keep port 8000 private; expose HTTPS on port 443 through Nginx

---

## TLS reverse proxy (recommended)

The current Nginx setup terminates HTTPS and proxies HTTP plus WebSocket traffic to `127.0.0.1:8000`. Frontend Settings use `https://evmasystem.duckdns.org`; the WebSocket URL is derived automatically.

---

## Cloudflare Worker

1. Deploy `cloudflare-worker/worker.js` (Wrangler: `cloudflare-worker/wrangler.toml`)
2. Add `api-demo.bybit.com` to `ALLOWED_HOSTS` if using Bybit demo
3. Set `ALLOWED_ORIGIN` to `https://hellotagvg.github.io` (not `*` in production)
4. Put worker URL in `backend/config/dashboard.json`:

```json
{
  "cloudflare_worker_url": "https://your-worker.workers.dev/"
}
```

---

## Kinetic Ops (operator machine)

Deploy the CLI on your laptop or ops host.

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
| FastAPI + uvicorn | Lightsail `127.0.0.1:8000` behind Nginx HTTPS |
| SQLite DBs | Lightsail `/opt/trading-dashboard/backend/data/` |
| Bybit market WS (bot feed) | Lightsail outbound to `stream.bybit.com` |
| Exchange monitor (ccxt poll) | Lightsail → exchanges every 5s |
| Position UI (primary) | Browser ← `/ws` `exchange_monitor` |
| Kinetic Ops | Operator workstation → Lightsail REST |

---

## Go-live sequence

See [PRE_DEPLOYMENT_REVIEW.md](PRE_DEPLOYMENT_REVIEW.md) Section 9 for the ordered checklist (rotate secrets → backend → TLS → worker → frontend → ops test → demo intent).
