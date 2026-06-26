import { MAX_1M_CANDLES } from '../constants';

function normalizeHyperliquidCandle(item) {
  const startMs = Number(item.t ?? item.time);
  return {
    time: Math.floor(startMs / 1000),
    open: Number(item.o ?? item.open),
    high: Number(item.h ?? item.high),
    low: Number(item.l ?? item.low),
    close: Number(item.c ?? item.close),
    volume: Number(item.v ?? item.volume ?? 0),
    isClosed: true
  };
}

export class HyperliquidAdapter {
  constructor() {
    this.ws = null;
  }

  async fetchRecent1m(symbol) {
    const endTime = Date.now();
    const startTime = endTime - MAX_1M_CANDLES * 60 * 1000;
    const payload = {
      type: 'candleSnapshot',
      req: { coin: symbol, interval: '1m', startTime, endTime }
    };
    const res = await fetch('https://api.hyperliquid.xyz/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`Hyperliquid HTTP ${res.status}`);
    const data = await res.json();
    return (Array.isArray(data) ? data : []).map(normalizeHyperliquidCandle);
  }

  connect1m(symbol, onCandle, onError) {
    this.disconnect();
    this.ws = new WebSocket('wss://api.hyperliquid.xyz/ws');

    this.ws.onopen = () => {
      this.ws?.send(
        JSON.stringify({
          method: 'subscribe',
          subscription: { type: 'candle', coin: symbol, interval: '1m' }
        })
      );
    };

    this.ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const data = payload?.data;
        if (!data) return;
        if (Array.isArray(data)) {
          for (const item of data) onCandle(normalizeHyperliquidCandle(item));
          return;
        }
        if (data?.t || data?.time) onCandle(normalizeHyperliquidCandle(data));
      } catch (err) {
        onError?.(err);
      }
    };

    this.ws.onerror = () => onError?.(new Error('Hyperliquid websocket connection error'));
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
