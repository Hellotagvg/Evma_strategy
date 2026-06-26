import { MAX_1M_CANDLES } from '../constants';

function normalizeBybitKline(item) {
  const startMs = Number(item.start ?? item[0]);
  return {
    time: Math.floor(startMs / 1000),
    open: Number(item.open ?? item[1]),
    high: Number(item.high ?? item[2]),
    low: Number(item.low ?? item[3]),
    close: Number(item.close ?? item[4]),
    volume: Number(item.volume ?? item[5] ?? 0),
    isClosed: Boolean(item.confirm ?? true)
  };
}

function buildProxiedUrl(workerUrl, url) {
  const base = workerUrl?.trim()?.replace(/\/$/, '');
  if (!base) return url;
  return `${base}/?url=${encodeURIComponent(url)}`;
}

async function fetchJson(url, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export class BybitAdapter {
  constructor({ workerUrl } = {}) {
    this.workerUrl = workerUrl || '';
    this.ws = null;
  }

  setWorkerUrl(workerUrl) {
    this.workerUrl = workerUrl || '';
  }

  async fetchRecent1m(symbol) {
    const marketSymbol = `${symbol}USDT`;
    const url = `https://api.bybit.com/v5/market/kline?category=linear&symbol=${marketSymbol}&interval=1&limit=${MAX_1M_CANDLES}`;

    try {
      const data = await fetchJson(url);
      const list = data?.result?.list || [];
      return list.reverse().map((row) => normalizeBybitKline(row));
    } catch (error) {
      const proxied = buildProxiedUrl(this.workerUrl, url);
      if (proxied === url) throw error;
      const data = await fetchJson(proxied);
      const list = data?.result?.list || [];
      return list.reverse().map((row) => normalizeBybitKline(row));
    }
  }

  connect1m(symbol, onCandle, onError) {
    const marketSymbol = `${symbol}USDT`;
    this.disconnect();
    this.ws = new WebSocket('wss://stream.bybit.com/v5/public/linear');

    this.ws.onopen = () => {
      this.ws?.send(JSON.stringify({ op: 'subscribe', args: [`kline.1.${marketSymbol}`] }));
    };

    this.ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const data = payload?.data;
        if (!Array.isArray(data)) return;
        for (const item of data) onCandle(normalizeBybitKline(item));
      } catch (err) {
        onError?.(err);
      }
    };

    this.ws.onerror = () => {
      onError?.(new Error('Bybit websocket connection error'));
    };
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
