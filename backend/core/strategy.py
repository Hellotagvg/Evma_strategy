"""
EVMA + Dual Dynamic ATR Stops Strategy Calculation Engine.

Replicates the JavaScript implementation exactly:
  - EMA:  k = 2/(period+1), iterative: prev = values[0], prev = val*k + prev*(1-k)
  - ATR:  true_range = max(H-L, |H-prevClose|, |L-prevClose|), then EMA of TR
  - EVMA: src = (H+L+C)/3 (hlc3), vol_s = volume+1,
          numerator = EMA(src*vol_s, length), denominator = EMA(vol_s, length),
          evma = numerator / denominator
  - Dynamic stops: atrPct = atr/evma, normAtr = clamp(atrPct/baselineVol, 0, 1),
          dist = stopMin + (stopMax - stopMin) * normAtr,
          upperStop = evma*(1+dist), lowerStop = evma*(1-dist)

All numeric operations use Python floats (IEEE 754 double, same as JS Number)
to guarantee bit-identical results with the JavaScript version.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Base directory for strategy config JSON files
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class StrategyConfig(BaseModel):
    """
    Pydantic model for a per-coin strategy configuration.

    Loaded from backend/config/strategy_<coin>.json.
    """

    coin: str = Field(..., description="Coin ticker, e.g. BTC, ETH, SOL")
    symbol: str = Field(..., description="Exchange symbol, e.g. BTCUSDT")
    evma_length: int = Field(20, ge=1, description="EMA period for EVMA numerator/denominator")
    evma_source: str = Field("hlc3", description="Price source for EVMA (only hlc3 supported)")
    atr_length: int = Field(14, ge=1, description="EMA period for ATR smoothing")
    baseline_volatility: float = Field(0.005, gt=0, description="Baseline volatility for normalization")
    min_stop_pct: float = Field(0.005, ge=0, description="Minimum stop distance as fraction of EVMA")
    max_stop_pct: float = Field(0.0075, ge=0, description="Maximum stop distance as fraction of EVMA")
    timeframe: str = Field("5m", description="Candle timeframe string, e.g. 5m, 15m, 1h")
    tp_rr_options: list[float] = Field(
        default_factory=lambda: [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
        description="Available risk:reward ratios for take-profit calculation",
    )


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndicatorLevels:
    """
    Computed indicator values for a single candle.

    Attributes:
        evma:       Exponential Volume-Weighted Moving Average price.
        upper_stop: Upper dynamic stop level = evma * (1 + dist).
        lower_stop: Lower dynamic stop level = evma * (1 - dist).
        dist:       Dynamic stop distance as a fraction of evma.
        atr_pct:    ATR expressed as a percentage of evma (atr / evma).
        norm_atr:   Normalized ATR = clamp(atr_pct / baseline_volatility, 0, 1).
    """

    evma: float
    upper_stop: float
    lower_stop: float
    dist: float
    atr_pct: float
    norm_atr: float


# ---------------------------------------------------------------------------
# Core math helpers  (match JS exactly)
# ---------------------------------------------------------------------------


def _ema(values: list[float], period: int) -> list[float]:
    """
    Compute an Exponential Moving Average series.

    Matches the JS implementation:
        k = 2 / (period + 1)
        result[0] = values[0]
        result[i] = values[i] * k + result[i-1] * (1 - k)

    Args:
        values: Input series (must have len >= 1).
        period: EMA period (>= 1).

    Returns:
        List of EMA values, same length as *values*.
    """
    if not values:
        return []

    k: float = 2.0 / (period + 1)
    one_minus_k: float = 1.0 - k

    result: list[float] = [0.0] * len(values)
    result[0] = values[0]

    for i in range(1, len(values)):
        result[i] = values[i] * k + result[i - 1] * one_minus_k

    return result


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into [lo, hi]. Matches JS Math.min(Math.max(...))."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


# ---------------------------------------------------------------------------
# Candle type hint
# ---------------------------------------------------------------------------

# A candle is a dict (or any mapping) with at least these float-valued keys:
#   open, high, low, close, volume
# Timestamps or other fields are allowed but ignored by the strategy engine.
Candle = dict[str, Any]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def calculate_indicators(
    candles: list[Candle],
    config: StrategyConfig,
) -> list[IndicatorLevels]:
    """
    Compute the full EVMA + Dynamic ATR Stops indicator series.

    The algorithm mirrors the JavaScript implementation step-by-step:

    1. Build the source price series  (hlc3 = (high + low + close) / 3).
    2. Build the volume series        (volume + 1, to avoid division by zero).
    3. Compute ATR via EMA of True Range.
    4. Compute EVMA = EMA(src * volSeries, length) / EMA(volSeries, length).
    5. Derive dynamic stop distances and levels.

    Args:
        candles: Chronologically ordered list of OHLCV candle dicts.
                 Each must contain float-valued keys: high, low, close, volume.
                 The first candle's "previous close" is taken as its own close
                 (matching the JS behaviour where prevClose starts undefined and
                 the first TR uses only high-low).
        config:  Strategy configuration parameters.

    Returns:
        List of ``IndicatorLevels``, one per candle, same length as *candles*.

    Raises:
        ValueError: If *candles* is empty.
    """
    n = len(candles)
    if n == 0:
        raise ValueError("Cannot calculate indicators on an empty candle list")

    logger.debug(
        "calculate_indicators: coin=%s  candles=%d  evma_len=%d  atr_len=%d",
        config.coin, n, config.evma_length, config.atr_length,
    )

    # ------------------------------------------------------------------
    # Step 1 & 2: Build raw series
    # ------------------------------------------------------------------
    src: list[float] = []          # hlc3 = (H + L + C) / 3
    vol_series: list[float] = []   # volume + 1
    true_ranges: list[float] = []  # True Range per candle

    for i, c in enumerate(candles):
        h: float = float(c["high"])
        l: float = float(c["low"])
        cl: float = float(c["close"])
        v: float = float(c["volume"])

        # hlc3 source price
        src.append((h + l + cl) / 3.0)

        # Volume series (add 1 to avoid zero-volume candles causing div/0)
        vol_series.append(v + 1.0)

        # True Range: for the very first candle there is no previous close,
        # so prev_close defaults to the current close (making the abs-diffs
        # zero and TR = high - low).  This matches JS where prevClose is
        # undefined on the first iteration and only high-low contributes.
        if i == 0:
            tr = h - l
        else:
            prev_close: float = float(candles[i - 1]["close"])
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))

        true_ranges.append(tr)

    # ------------------------------------------------------------------
    # Step 3: ATR = EMA(true_range, atr_length)
    # ------------------------------------------------------------------
    atr_series: list[float] = _ema(true_ranges, config.atr_length)

    # ------------------------------------------------------------------
    # Step 4: EVMA = EMA(src * volSeries, evma_length) / EMA(volSeries, evma_length)
    # ------------------------------------------------------------------
    src_x_vol: list[float] = [s * vs for s, vs in zip(src, vol_series)]
    numerator: list[float] = _ema(src_x_vol, config.evma_length)
    denominator: list[float] = _ema(vol_series, config.evma_length)

    # ------------------------------------------------------------------
    # Step 5: Dynamic stop distances & final levels
    # ------------------------------------------------------------------
    results: list[IndicatorLevels] = []

    for i in range(n):
        evma: float = numerator[i] / denominator[i] if denominator[i] != 0.0 else src[i]
        atr_val: float = atr_series[i]

        # ATR as a fraction of the EVMA price
        atr_pct: float = atr_val / evma if evma != 0.0 else 0.0

        # Normalize ATR against baseline volatility, clamped to [0, 1]
        norm_atr: float = _clamp(atr_pct / config.baseline_volatility, 0.0, 1.0)

        # Dynamic stop distance: interpolate between min and max stop %
        dist: float = config.min_stop_pct + (config.max_stop_pct - config.min_stop_pct) * norm_atr

        # Final stop levels
        upper_stop: float = evma * (1.0 + dist)
        lower_stop: float = evma * (1.0 - dist)

        results.append(
            IndicatorLevels(
                evma=evma,
                upper_stop=upper_stop,
                lower_stop=lower_stop,
                dist=dist,
                atr_pct=atr_pct,
                norm_atr=norm_atr,
            )
        )

    logger.debug(
        "calculate_indicators: finished  last_evma=%.6f  last_upper=%.6f  last_lower=%.6f",
        results[-1].evma, results[-1].upper_stop, results[-1].lower_stop,
    )

    return results


def get_latest_levels(
    candles: list[Candle],
    config: StrategyConfig,
) -> IndicatorLevels:
    """
    Convenience wrapper: compute the full series and return only the last value.

    This is the primary entry point for real-time signal evaluation — feed in
    the most recent N candles (where N >= evma_length and N >= atr_length for
    the EMA to have warmed up) and get back the current indicator snapshot.

    Args:
        candles: Chronologically ordered OHLCV candle list.
        config:  Strategy configuration.

    Returns:
        The ``IndicatorLevels`` for the most recent (last) candle.

    Raises:
        ValueError: If *candles* is empty.
    """
    series = calculate_indicators(candles, config)
    latest = series[-1]

    logger.info(
        "get_latest_levels: coin=%s  evma=%.6f  upper=%.6f  lower=%.6f  "
        "dist=%.6f  atr_pct=%.6f  norm_atr=%.4f",
        config.coin,
        latest.evma,
        latest.upper_stop,
        latest.lower_stop,
        latest.dist,
        latest.atr_pct,
        latest.norm_atr,
    )

    return latest


def load_strategy_config(coin: str) -> StrategyConfig:
    """
    Load a ``StrategyConfig`` from the JSON file for the given coin.

    Looks for ``backend/config/strategy_<coin_lower>.json``.

    Args:
        coin: Coin ticker (case-insensitive), e.g. "BTC", "eth".

    Returns:
        Parsed ``StrategyConfig`` instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        pydantic.ValidationError: If the JSON content is invalid.
    """
    coin_lower = coin.strip().lower()
    config_path = _CONFIG_DIR / f"strategy_{coin_lower}.json"

    logger.info("load_strategy_config: loading %s", config_path)

    if not config_path.exists():
        logger.error("load_strategy_config: file not found: %s", config_path)
        raise FileNotFoundError(f"Strategy config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)

    cfg = StrategyConfig(**raw)

    logger.info(
        "load_strategy_config: loaded coin=%s  symbol=%s  evma_len=%d  atr_len=%d  "
        "baseline_vol=%.4f  stop_range=[%.4f, %.4f]  timeframe=%s",
        cfg.coin,
        cfg.symbol,
        cfg.evma_length,
        cfg.atr_length,
        cfg.baseline_volatility,
        cfg.min_stop_pct,
        cfg.max_stop_pct,
        cfg.timeframe,
    )

    return cfg
