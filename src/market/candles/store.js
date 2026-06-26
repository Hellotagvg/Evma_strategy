import { MAX_1M_CANDLES } from '../constants';

const sortCandlesAsc = (candles) => [...candles].sort((a, b) => a.time - b.time);
const isValidNumber = (value) => Number.isFinite(value);

function isValidCandle(candle) {
  if (!candle) return false;
  return (
    Number.isInteger(candle.time) &&
    isValidNumber(candle.open) &&
    isValidNumber(candle.high) &&
    isValidNumber(candle.low) &&
    isValidNumber(candle.close) &&
    isValidNumber(candle.volume ?? 0)
  );
}

export class CandleStore {
  constructor(maxCandles = MAX_1M_CANDLES) {
    this.maxCandles = maxCandles;
    this.candles = [];
  }

  hydrate(candles) {
    const validated = candles.filter(isValidCandle);
    const sorted = sortCandlesAsc(validated);
    const deduped = [];
    let lastTime = null;
    for (const candle of sorted) {
      if (lastTime === candle.time) {
        deduped[deduped.length - 1] = candle;
      } else {
        deduped.push(candle);
      }
      lastTime = candle.time;
    }
    this.candles = deduped.slice(-this.maxCandles);
  }

  upsert(candle) {
    if (!isValidCandle(candle)) return;
    const last = this.candles[this.candles.length - 1];
    if (!last) {
      this.candles.push(candle);
      return;
    }

    if (candle.time === last.time) {
      this.candles[this.candles.length - 1] = candle;
      return;
    }

    if (candle.time > last.time) {
      this.candles.push(candle);
      if (this.candles.length > this.maxCandles) {
        this.candles.shift();
      }
      return;
    }

    const idx = this.candles.findIndex((item) => item.time === candle.time);
    if (idx >= 0) this.candles[idx] = candle;
  }

  getAll() {
    return this.candles;
  }
}
