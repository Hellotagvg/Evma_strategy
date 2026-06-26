import { TIMEFRAME_MINUTES } from '../constants';

export function aggregateCandles(candles1m, timeframe) {
  if (timeframe === '1m') return candles1m;
  const bucketSizeSec = (TIMEFRAME_MINUTES[timeframe] || 1) * 60;
  const buckets = [];
  let current = null;

  for (const candle of candles1m) {
    const bucketTime = Math.floor(candle.time / bucketSizeSec) * bucketSizeSec;

    if (!current || current.time !== bucketTime) {
      if (current) buckets.push(current);
      current = {
        time: bucketTime,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume || 0,
        isClosed: candle.isClosed
      };
      continue;
    }

    current.high = Math.max(current.high, candle.high);
    current.low = Math.min(current.low, candle.low);
    current.close = candle.close;
    current.volume += candle.volume || 0;
    current.isClosed = candle.isClosed;
  }

  if (current) buckets.push(current);
  return buckets;
}
