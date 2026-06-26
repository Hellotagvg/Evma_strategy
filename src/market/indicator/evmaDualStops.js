import { DEFAULT_INDICATOR_CONFIG, TP_MULTIPLIERS } from '../constants';

function ema(values, period) {
  if (!values.length) return [];
  const out = [];
  const k = 2 / (period + 1);
  let prev = values[0];
  out.push(prev);
  for (let i = 1; i < values.length; i += 1) {
    prev = values[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function calculateAtr(candles, period) {
  const tr = [];
  for (let i = 0; i < candles.length; i += 1) {
    const c = candles[i];
    const prevClose = i > 0 ? candles[i - 1].close : c.close;
    const t = Math.max(
      c.high - c.low,
      Math.abs(c.high - prevClose),
      Math.abs(c.low - prevClose)
    );
    tr.push(t);
  }
  return ema(tr, period);
}

export function calculateEvmaDualStops(candles, symbol, config = DEFAULT_INDICATOR_CONFIG) {
  if (!candles.length) {
    return { evma: [], upperStop: [], lowerStop: [], longTp: [], shortTp: [] };
  }

  const src = candles.map((c) => (c.high + c.low + c.close) / 3);
  const volSeries = candles.map((c) => (c.volume || 0) + 1);
  const numerator = ema(src.map((v, i) => v * volSeries[i]), config.evmaLen);
  const denominator = ema(volSeries, config.evmaLen);
  const atrSeries = calculateAtr(candles, config.atrLen);

  const evma = [];
  const upperStop = [];
  const lowerStop = [];
  const longTp = [];
  const shortTp = [];
  const tpMult = TP_MULTIPLIERS[symbol] ?? 2.5;
  const stopMin = Math.min(config.minStopPct, config.maxStopPct);
  const stopMax = Math.max(config.minStopPct, config.maxStopPct);

  for (let i = 0; i < candles.length; i += 1) {
    const time = candles[i].time;
    const evmaVal = denominator[i] ? numerator[i] / denominator[i] : 0;
    const atrVal = atrSeries[i] || 0;
    const atrPct = evmaVal !== 0 ? atrVal / evmaVal : 0;
    const normAtr = Math.min(Math.max(atrPct / config.baselineVol, 0), 1);
    const dist = stopMin + (stopMax - stopMin) * normAtr;
    const up = evmaVal * (1 + dist);
    const low = evmaVal * (1 - dist);

    evma.push({ time, value: evmaVal });
    upperStop.push({ time, value: up });
    lowerStop.push({ time, value: low });
    longTp.push({ time, value: evmaVal * (1 + dist * tpMult) });
    shortTp.push({ time, value: evmaVal * (1 - dist * tpMult) });
  }

  return { evma, upperStop, lowerStop, longTp, shortTp };
}
