/**
 * exchangeAPI.js
 * Frontend-side exchange API client.
 * Handles authenticated requests to Bybit (Live & Demo) and Hyperliquid directly from the browser.
 * Uses the Cloudflare Worker proxy for CORS if configured.
 * Signing uses Web Crypto API (HMAC-SHA256) — no server needed.
 */

const RECV_WINDOW = '5000';
const BYBIT_BASE = 'https://api.bybit.com';
const BYBIT_DEMO_BASE = 'https://api-demo.bybit.com';
const BYBIT_PRIVATE_WS_MAINNET = 'wss://stream.bybit.com/v5/private';
const BYBIT_PRIVATE_WS_TESTNET = 'wss://stream-testnet.bybit.com/v5/private';
const BYBIT_PRIVATE_HEARTBEAT_MS = 20000;

let backendBaseUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';
let backendPassword = '';

const bybitPrivateFeeds = new Map();

export function configureBackendClient(url, password) {
  backendBaseUrl = (url || 'http://localhost:8000').replace(/\/$/, '');
  backendPassword = password || '';
}

// ---------------------------------------------------------------------------
// HMAC-SHA256 signing via Web Crypto API
// ---------------------------------------------------------------------------
async function hmacSHA256(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

// ---------------------------------------------------------------------------
// Build a Bybit-authenticated fetch
// ---------------------------------------------------------------------------
async function bybitRequest(account, method, path, params = {}, cfWorkerUrl = '') {
  const { api_key, api_secret, demo_trading } = account.credentials || {};
  const timestamp = Date.now().toString();

  let queryString = '';
  let body = '';

  if (method === 'GET') {
    queryString = new URLSearchParams(params).toString();
  } else {
    body = JSON.stringify(params);
  }

  const signPayload =
    method === 'GET'
      ? `${timestamp}${api_key}${RECV_WINDOW}${queryString}`
      : `${timestamp}${api_key}${RECV_WINDOW}${body}`;

  const sign = await hmacSHA256(api_secret, signPayload);

  const headers = {
    'Content-Type': 'application/json',
    'X-BAPI-API-KEY': api_key,
    'X-BAPI-SIGN': sign,
    'X-BAPI-SIGN-TYPE': '2',
    'X-BAPI-TIMESTAMP': timestamp,
    'X-BAPI-RECV-WINDOW': RECV_WINDOW,
  };

  // Demo trading header
  if (demo_trading) {
    headers['X-BAPI-BROKER'] = '';
    // Bybit demo uses a special header
    headers['Referer'] = 'bybit-demo';
  }

  const base = demo_trading ? BYBIT_DEMO_BASE : BYBIT_BASE;
  const fullUrl = `${base}${path}${queryString ? '?' + queryString : ''}`;

  // Route through CF Worker if configured
  let fetchUrl = fullUrl;
  if (cfWorkerUrl) {
    fetchUrl = `${cfWorkerUrl.replace(/\/$/, '')}?url=${encodeURIComponent(fullUrl)}`;
  }

  const res = await fetch(fetchUrl, {
    method,
    headers,
    body: method !== 'GET' ? body : undefined,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Bybit HTTP ${res.status}: ${text}`);
  }

  const json = await res.json();
  if (json.retCode !== 0) {
    throw new Error(`Bybit API error ${json.retCode}: ${json.retMsg}`);
  }
  return json.result;
}

function getBybitPrivateWsUrl(account) {
  const credentials = account?.credentials || {};
  return credentials.testnet ? BYBIT_PRIVATE_WS_TESTNET : BYBIT_PRIVATE_WS_MAINNET;
}

function getBybitPrivateFeedKey(account) {
  return account?.id || account?.name || 'bybit';
}

function isTerminalBybitOrderStatus(status) {
  const normalized = String(status || '').toLowerCase();
  return new Set(['filled', 'cancelled', 'canceled', 'rejected', 'deactivated']).has(normalized);
}

function isOpenBybitOrder(rawOrder) {
  const status = String(rawOrder?.orderStatus || '').toLowerCase();
  if (!status) return false;
  if (isTerminalBybitOrderStatus(status)) return false;

  const qty = parseFloat(rawOrder?.qty || 0);
  const cumExecQty = parseFloat(rawOrder?.cumExecQty || 0);
  if (qty > 0 && cumExecQty >= qty) return false;

  return new Set(['new', 'partiallyfilled', 'untriggered', 'triggered', 'created']).has(status) || qty > cumExecQty;
}

function snapshotBybitPrivateFeed(feed) {
  return {
    connected: feed.connected,
    authenticated: feed.authenticated,
    hasPositionSnapshot: feed.hasPositionSnapshot,
    hasOrderSnapshot: feed.hasOrderSnapshot,
    lastUpdateAt: feed.lastUpdateAt,
    lastError: feed.lastError,
    positions: [...feed.positionsBySymbol.values()],
    orders: [...feed.ordersById.values()],
  };
}

function emitBybitPrivateFeed(feed) {
  const snapshot = snapshotBybitPrivateFeed(feed);
  for (const listener of feed.listeners) {
    try {
      listener.onUpdate?.(snapshot);
    } catch (err) {
      console.warn('Bybit private feed listener failed:', err);
    }
  }
}

function subscribeBybitPrivateFeed(account, handlers = {}) {
  if ((account?.exchange || '').toLowerCase() !== 'bybit') {
    return () => {};
  }

  const apiKey = account?.credentials?.api_key || '';
  const apiSecret = account?.credentials?.api_secret || '';
  if (!apiKey || !apiSecret) {
    handlers.onError?.(new Error(`Missing API credentials for ${account?.id || 'bybit'} websocket feed.`));
    return () => {};
  }

  const feedKey = getBybitPrivateFeedKey(account);
  let feed = bybitPrivateFeeds.get(feedKey);
  if (!feed) {
    feed = {
      account,
      feedKey,
      ws: null,
      destroyed: false,
      connected: false,
      authenticated: false,
      reconnectTimer: null,
      heartbeatTimer: null,
      positionsBySymbol: new Map(),
      ordersById: new Map(),
      hasPositionSnapshot: false,
      hasOrderSnapshot: false,
      lastUpdateAt: 0,
      lastError: null,
      listeners: new Set(),
    };
    bybitPrivateFeeds.set(feedKey, feed);
  } else {
    feed.account = account;
  }

  const listener = {
    onUpdate: handlers.onUpdate,
    onError: handlers.onError,
  };
  feed.listeners.add(listener);

  const connect = async () => {
    if (feed.destroyed) return;

    const url = getBybitPrivateWsUrl(account);
    const ws = new WebSocket(url);
    feed.ws = ws;

    const clearTimers = () => {
      if (feed.heartbeatTimer) {
        clearInterval(feed.heartbeatTimer);
        feed.heartbeatTimer = null;
      }
    };

    const scheduleReconnect = () => {
      if (feed.destroyed || feed.reconnectTimer) return;
      feed.reconnectTimer = setTimeout(() => {
        feed.reconnectTimer = null;
        connect().catch((err) => {
          feed.lastError = err?.message || String(err);
          emitBybitPrivateFeed(feed);
        });
      }, 2000);
    };

    ws.onopen = async () => {
      try {
        feed.connected = true;
        feed.authenticated = false;
        feed.lastError = null;
        emitBybitPrivateFeed(feed);

        const expires = Date.now() + 1000;
        const signature = await hmacSHA256(apiSecret, `GET/realtime${expires}`);
        ws.send(JSON.stringify({
          op: 'auth',
          args: [apiKey, expires, signature],
        }));

        clearTimers();
        feed.heartbeatTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try {
              ws.send(JSON.stringify({ op: 'ping' }));
            } catch (err) {
              console.warn('Bybit private feed ping failed:', err);
            }
          }
        }, BYBIT_PRIVATE_HEARTBEAT_MS);
      } catch (err) {
        feed.lastError = err?.message || String(err);
        emitBybitPrivateFeed(feed);
        try {
          ws.close();
        } catch {
          /* noop */
        }
      }
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      if (msg?.op === 'auth') {
        if (msg.success) {
          feed.authenticated = true;
          feed.lastError = null;
          ws.send(JSON.stringify({
            op: 'subscribe',
            args: ['position.linear', 'order.linear'],
          }));
          emitBybitPrivateFeed(feed);
        } else {
          feed.lastError = msg.ret_msg || 'Bybit websocket auth failed';
          emitBybitPrivateFeed(feed);
          try {
            ws.close();
          } catch {
            /* noop */
          }
        }
        return;
      }

      if (!msg?.topic || !Array.isArray(msg.data)) {
        return;
      }

      let changed = false;
      if (String(msg.topic).startsWith('position')) {
        for (const item of msg.data) {
          const symbol = item?.symbol || '';
          if (!symbol) continue;
          const size = parseFloat(item.size || 0);
          if (size > 0) {
            feed.positionsBySymbol.set(symbol, normalizeBybitPosition(item, account));
          } else {
            feed.positionsBySymbol.delete(symbol);
          }
          changed = true;
        }
        feed.hasPositionSnapshot = true;
      } else if (String(msg.topic).startsWith('order')) {
        for (const item of msg.data) {
          const orderId = item?.orderId ? String(item.orderId) : '';
          if (!orderId) continue;
          if (isOpenBybitOrder(item)) {
            feed.ordersById.set(orderId, normalizeBybitOrder(item, account));
          } else {
            feed.ordersById.delete(orderId);
          }
          changed = true;
        }
        feed.hasOrderSnapshot = true;
      }

      if (changed) {
        feed.lastUpdateAt = Date.now();
        feed.lastError = null;
        emitBybitPrivateFeed(feed);
      }
    };

    ws.onerror = () => {
      feed.lastError = 'Bybit websocket connection error';
      emitBybitPrivateFeed(feed);
    };

    ws.onclose = () => {
      feed.connected = false;
      feed.authenticated = false;
      clearTimers();
      emitBybitPrivateFeed(feed);
      if (!feed.destroyed) {
        scheduleReconnect();
      }
    };
  };

  connect().catch((err) => {
    feed.lastError = err?.message || String(err);
    emitBybitPrivateFeed(feed);
    listener.onError?.(err);
  });

  return () => {
    feed.listeners.delete(listener);
    if (feed.listeners.size === 0) {
      feed.destroyed = true;
      if (feed.reconnectTimer) {
        clearTimeout(feed.reconnectTimer);
        feed.reconnectTimer = null;
      }
      if (feed.heartbeatTimer) {
        clearInterval(feed.heartbeatTimer);
        feed.heartbeatTimer = null;
      }
      try {
        feed.ws?.close();
      } catch {
        /* noop */
      }
      bybitPrivateFeeds.delete(feedKey);
    }
  };
}

function getBybitPrivateSnapshot(account) {
  if ((account?.exchange || '').toLowerCase() !== 'bybit') {
    return null;
  }
  const feed = bybitPrivateFeeds.get(getBybitPrivateFeedKey(account));
  return feed ? snapshotBybitPrivateFeed(feed) : null;
}

export { subscribeBybitPrivateFeed, getBybitPrivateSnapshot };

// ---------------------------------------------------------------------------
// Hyperliquid - uses JSON-based API, no REST signing required for reads
// POST-only API, public info endpoint for positions
// ---------------------------------------------------------------------------
async function hyperliquidRequest(account, payload, cfWorkerUrl = '') {
  const HL_URL = 'https://api.hyperliquid.xyz/info';
  let fetchUrl = HL_URL;
  if (cfWorkerUrl) {
    fetchUrl = `${cfWorkerUrl.replace(/\/$/, '')}?url=${encodeURIComponent(HL_URL)}`;
  }

  const res = await fetch(fetchUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Hyperliquid HTTP ${res.status}: ${text}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// PUBLIC API
// ---------------------------------------------------------------------------

/**
 * Fetch all open positions for a given account.
 * Returns array of normalized position objects.
 */
async function fetchPositionsBrowser(account, cfWorkerUrl = '') {
  const exchange = (account.exchange || '').toLowerCase();

  if (exchange === 'bybit') {
    const result = await bybitRequest(account, 'GET', '/v5/position/list', {
      category: 'linear',
      settleCoin: 'USDT',
    }, cfWorkerUrl);

    const rows = result?.list || [];
    return rows
      .filter((p) => parseFloat(p.size) > 0)
      .map((p) => normalizeBybitPosition(p, account));
  }

  if (exchange === 'hyperliquid') {
    const walletAddress = account.credentials?.wallet_address || account.credentials?.api_key;
    if (!walletAddress) return [];
    const data = await hyperliquidRequest(account, {
      type: 'clearinghouseState',
      user: walletAddress,
    }, cfWorkerUrl);
    return normalizeHyperliquidPositions(data, account);
  }

  return [];
}

export async function fetchPositions(account, cfWorkerUrl = '') {
  try {
    return await fetchPositionsBrowser(account, cfWorkerUrl);
  } catch (browserErr) {
    console.warn(
      `Browser positions fetch failed for ${account.id || account.name}, trying backend fallback:`,
      browserErr
    );
    const rows = await backendGet(
      `/api/exchange/positions?account_id=${encodeURIComponent(account.id)}`
    );
    return Array.isArray(rows) ? rows : [];
  }
}

/**
 * Fetch all open/pending limit orders for a given account.
 */
async function fetchOpenOrdersBrowser(account, cfWorkerUrl = '') {
  const exchange = (account.exchange || '').toLowerCase();

  if (exchange === 'bybit') {
    const result = await bybitRequest(account, 'GET', '/v5/order/realtime', {
      category: 'linear',
      settleCoin: 'USDT',
      limit: 50,
    }, cfWorkerUrl);

    const rows = result?.list || [];
    return rows.map((o) => normalizeBybitOrder(o, account));
  }

  if (exchange === 'hyperliquid') {
    const walletAddress = account.credentials?.wallet_address || account.credentials?.api_key;
    if (!walletAddress) return [];
    const data = await hyperliquidRequest(account, {
      type: 'openOrders',
      user: walletAddress,
    }, cfWorkerUrl);
    return (data || []).map((o) => normalizeHyperliquidOrder(o, account));
  }

  return [];
}

export async function fetchOpenOrders(account, cfWorkerUrl = '') {
  try {
    return await fetchOpenOrdersBrowser(account, cfWorkerUrl);
  } catch (browserErr) {
    console.warn(
      `Browser open-orders fetch failed for ${account.id || account.name}, trying backend fallback:`,
      browserErr
    );
    const rows = await backendGet(
      `/api/exchange/open-orders?account_id=${encodeURIComponent(account.id)}`
    );
    return Array.isArray(rows) ? rows : [];
  }
}

// ---------------------------------------------------------------------------
// Backend trading proxy (ccxt) — all venues: Bybit demo/live, Hyperliquid
// ---------------------------------------------------------------------------

async function backendGet(path) {
  const res = await fetch(`${backendBaseUrl}${path}`, {
    headers: {
      'X-Kinetic-Password': backendPassword,
    },
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
    /* empty */
  }
  if (!res.ok) {
    const detail = data.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
          : data.message || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

async function backendPost(path, body) {
  const res = await fetch(`${backendBaseUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Kinetic-Password': backendPassword,
    },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
    /* empty */
  }
  if (!res.ok) {
    const detail = data.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
          : data.message || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

/**
 * Close a position via a market reduce-only order.
 */
export async function closePosition(account, symbol, side, size, _cfWorkerUrl = '') {
  return backendPost('/api/exchange/close-position', {
    account_id: account.id,
    symbol,
    side,
    size,
  });
}

/**
 * Cancel an open limit order.
 */
export async function cancelOrder(account, symbol, orderId, _cfWorkerUrl = '') {
  return backendPost('/api/exchange/cancel-order', {
    account_id: account.id,
    symbol,
    order_id: String(orderId),
  });
}

/**
 * Amend an open limit order (price and quantity).
 */
export async function amendOrder(account, symbol, orderId, side, price, qty, _cfWorkerUrl = '') {
  return backendPost('/api/exchange/amend-order', {
    account_id: account.id,
    symbol,
    order_id: String(orderId),
    side,
    price,
    qty,
  });
}

/**
 * Place a new limit order.
 */
export async function createLimitOrder(account, coin, side, price, qty, _cfWorkerUrl = '') {
  return backendPost('/api/exchange/place-limit', {
    account_id: account.id,
    coin,
    side,
    price,
    qty,
  });
}

/**
 * Set TP and/or SL on an open position.
 */
export async function setPositionTPSL(account, symbol, side, tp, sl, _cfWorkerUrl = '') {
  return backendPost('/api/exchange/set-tpsl', {
    account_id: account.id,
    symbol,
    side,
    tp: tp ?? null,
    sl: sl ?? null,
  });
}

/**
 * Cancel multiple orders across accounts.
 * @param {Array<{ account_id: string, symbol: string, order_id: string }>} orders
 */
export async function cancelOrdersBatch(orders) {
  return backendPost('/api/exchange/cancel-batch', { orders });
}

export async function refreshExchangeMonitor() {
  return backendPost('/api/exchange/refresh-monitor', {});
}

// ---------------------------------------------------------------------------
// Normalization helpers
// ---------------------------------------------------------------------------

function normalizeBybitPosition(p, account) {
  const side = p.side === 'Buy' ? 'LONG' : 'SHORT';
  const entryPrice = parseFloat(p.avgPrice || p.entryPrice || 0);
  const markPrice = parseFloat(p.markPrice || 0);
  const size = parseFloat(p.size || 0);
  const positionValue = parseFloat(p.positionValue || size * entryPrice);
  const unrealisedPnl = parseFloat(p.unrealisedPnl || 0);
  const leverage = parseFloat(p.leverage || 1);
  const pnlPct = positionValue > 0 ? (unrealisedPnl / (positionValue / leverage)) * 100 : 0;
  const symbol = p.symbol || '';
  const baseSymbol = symbol.replace('USDT', '').replace('PERP', '');

  return {
    id: `${account.id}-${symbol}-${side}`,
    symbol: `${baseSymbol}-PERP`,
    baseSymbol,
    exchange: account.id,
    exchangeName: account.name,
    side,
    size: `${size} ${baseSymbol}`,
    sizeVal: size,
    value: `$${positionValue.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
    entry: entryPrice,
    mark: markPrice,
    pnl: unrealisedPnl,
    pnlPct,
    leverage: `${leverage}x`,
    isDemo: !!(account.credentials?.demo_trading),
    tp: parseFloat(p.takeProfit) || null,
    sl: parseFloat(p.stopLoss) || null,
    rawSymbol: symbol,
  };
}

function normalizeBybitOrder(o, account) {
  const side = o.side === 'Buy' ? 'LONG' : 'SHORT';
  const price = parseFloat(o.price || 0);
  const qty = parseFloat(o.qty || 0);
  const symbol = o.symbol || '';
  const baseSymbol = symbol.replace('USDT', '').replace('PERP', '');
  const filled = parseFloat(o.cumExecQty || 0);
  const filledPct = qty > 0 ? (filled / qty) * 100 : 0;
  const reduceOnly = String(o.reduceOnly || o.closeOnTrigger || '').toLowerCase() === 'true' || o.reduceOnly === true || o.closeOnTrigger === true;

  return {
    id: `${account.id}-${o.orderId}`,
    orderId: o.orderId,
    symbol: `${baseSymbol}-USDT`,
    baseSymbol,
    exchange: account.id,
    exchangeName: account.name,
    side,
    type: o.orderType || 'Limit Order',
    price,
    amount: qty,
    totalValue: price * qty,
    filled: filledPct,
    statusText: o.orderStatus || 'Open',
    rawSymbol: symbol,
    reduceOnly,
  };
}

function normalizeHyperliquidPositions(data, account) {
  const positions = data?.assetPositions || [];
  return positions
    .filter((ap) => {
      const pos = ap.position;
      return pos && parseFloat(pos.szi) !== 0;
    })
    .map((ap) => {
      const pos = ap.position;
      const szi = parseFloat(pos.szi || 0);
      const side = szi > 0 ? 'LONG' : 'SHORT';
      const size = Math.abs(szi);
      const entryPrice = parseFloat(pos.entryPx || 0);
      const markPrice = parseFloat(pos.markPx || entryPrice);
      const unrealisedPnl = parseFloat(pos.unrealizedPnl || 0);
      const leverage = parseFloat(pos.leverage?.value || pos.leverage || 1);
      const positionValue = size * entryPrice;
      const pnlPct = positionValue > 0 ? (unrealisedPnl / (positionValue / leverage)) * 100 : 0;
      const coin = pos.coin || ap.coin || 'UNKNOWN';

      return {
        id: `${account.id}-${coin}-${side}`,
        symbol: `${coin}-PERP`,
        baseSymbol: coin,
        exchange: account.id,
        exchangeName: account.name,
        side,
        size: `${size} ${coin}`,
        sizeVal: size,
        value: `$${positionValue.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
        entry: entryPrice,
        mark: markPrice,
        pnl: unrealisedPnl,
        pnlPct,
        leverage: `${leverage}x`,
        isDemo: false,
        tp: null,
        sl: null,
        rawSymbol: coin,
      };
    });
}

function normalizeHyperliquidOrder(o, account) {
  const side = o.side === 'B' ? 'LONG' : 'SHORT';
  const price = parseFloat(o.limitPx || 0);
  const qty = parseFloat(o.sz || 0);
  const coin = o.coin || '';

  return {
    id: `${account.id}-${o.oid}`,
    orderId: String(o.oid),
    symbol: `${coin}-USDT`,
    baseSymbol: coin,
    exchange: account.id,
    exchangeName: account.name,
    side,
    type: 'Limit Order',
    price,
    amount: qty,
    totalValue: price * qty,
    filled: 0,
    statusText: 'Open',
    rawSymbol: coin,
  };
}
