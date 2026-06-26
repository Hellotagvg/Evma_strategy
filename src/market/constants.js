export const SUPPORTED_SYMBOLS = ['BTC', 'ETH', 'SOL'];
export const SUPPORTED_TIMEFRAMES = ['1m', '3m', '5m', '15m'];

export const TIMEFRAME_MINUTES = {
  '1m': 1,
  '3m': 3,
  '5m': 5,
  '15m': 15
};

export const MAX_1M_CANDLES = 1440;

export const TP_MULTIPLIERS = {
  BTC: 2.5,
  ETH: 3.5,
  SOL: 3.5
};

export const DEFAULT_INDICATOR_CONFIG = {
  evmaLen: 20,
  atrLen: 14,
  baselineVol: 0.005,
  minStopPct: 0.005,
  maxStopPct: 0.0075
};

export const EXCHANGES = {
  bybit: 'bybit',
  hyperliquid: 'hyperliquid'
};
