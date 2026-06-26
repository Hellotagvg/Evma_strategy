import { aggregateCandles } from './candles/aggregate';
import { CandleStore } from './candles/store';
import { calculateEvmaDualStops } from './indicator/evmaDualStops';
import { BybitAdapter } from './adapters/bybit';
import { HyperliquidAdapter } from './adapters/hyperliquid';

export class MarketEngine {
  constructor({ workerUrl } = {}) {
    this.bybit = new BybitAdapter({ workerUrl });
    this.hyperliquid = new HyperliquidAdapter();
    this.store = new CandleStore();
    this.listeners = new Set();
    this.errorListeners = new Set();
    this.current = { exchange: 'hyperliquid', symbol: 'BTC', timeframe: '1m' };
    this.connected = false;
  }

  setWorkerUrl(workerUrl) {
    this.bybit.setWorkerUrl(workerUrl);
  }

  onUpdate(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onError(listener) {
    this.errorListeners.add(listener);
    return () => this.errorListeners.delete(listener);
  }

  emitUpdate() {
    const candles1m = this.store.getAll();
    const candles = aggregateCandles(candles1m, this.current.timeframe);
    const indicator = calculateEvmaDualStops(candles, this.current.symbol);
    const payload = { candles, indicator, meta: { ...this.current } };
    this.listeners.forEach((fn) => fn(payload));
  }

  emitError(error) {
    this.errorListeners.forEach((fn) => fn(error));
  }

  getAdapter(exchange) {
    return exchange === 'bybit' ? this.bybit : this.hyperliquid;
  }

  async connect({ exchange, symbol, timeframe }) {
    this.disconnect();
    this.current = { exchange, symbol, timeframe };
    const adapter = this.getAdapter(exchange);
    try {
      const recent = await adapter.fetchRecent1m(symbol);
      this.store.hydrate(recent);
      this.emitUpdate();
      adapter.connect1m(
        symbol,
        (candle) => {
          this.store.upsert(candle);
          this.emitUpdate();
        },
        (err) => this.emitError(err)
      );
      this.connected = true;
    } catch (err) {
      this.emitError(err);
      throw err;
    }
  }

  updateTimeframe(timeframe) {
    this.current = { ...this.current, timeframe };
    this.emitUpdate();
  }

  async switchMarket({ exchange, symbol }) {
    if (exchange === this.current.exchange && symbol === this.current.symbol) return;
    await this.connect({ exchange, symbol, timeframe: this.current.timeframe });
  }

  disconnect() {
    this.connected = false;
    this.bybit.disconnect();
    this.hyperliquid.disconnect();
  }
}
