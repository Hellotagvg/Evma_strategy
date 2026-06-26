import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CandlestickSeries, LineSeries, createChart } from 'lightweight-charts';
import { EXCHANGES, SUPPORTED_TIMEFRAMES } from '../market/constants';
import { MarketEngine } from '../market/engine';

const SYMBOL_OPTIONS = [
  { label: 'BTCUSDT.P', value: 'BTC' },
  { label: 'ETHUSDT.P', value: 'ETH' },
  { label: 'SOLUSDT.P', value: 'SOL' },
];

const EXCHANGE_OPTIONS = [
  { label: 'HL', value: EXCHANGES.hyperliquid },
  { label: 'BY', value: EXCHANGES.bybit },
];

const chartTheme = {
  layout: {
    background: { color: '#ffffff' },
    textColor: '#1f2937',
    fontSize: 11,
    fontFamily: 'Outfit, sans-serif'
  },
  grid: {
    vertLines: { color: 'rgba(148, 163, 184, 0.2)' },
    horzLines: { color: 'rgba(148, 163, 184, 0.2)' }
  },
  leftPriceScale: {
    visible: true,
    borderColor: '#d1d5db'
  },
  rightPriceScale: {
    visible: false
  },
  timeScale: {
    borderColor: '#d1d5db',
    timeVisible: true,
    secondsVisible: false
  },
  handleScale: {
    axisPressedMouseMove: { price: true, time: true },
    mouseWheel: true,
    pinch: true
  },
  handleScroll: {
    mouseWheel: true,
    pressedMouseMove: true,
    horzTouchDrag: true,
    vertTouchDrag: true
  },
  crosshair: {
    vertLine: { color: 'rgba(17, 24, 39, 0.35)' },
    horzLine: { color: 'rgba(17, 24, 39, 0.35)' }
  }
};

const isFiniteNum = (value) => Number.isFinite(value);

function sanitizeCandles(candles) {
  const clean = candles
    .filter(
      (c) =>
        Number.isInteger(c?.time) &&
        isFiniteNum(c?.open) &&
        isFiniteNum(c?.high) &&
        isFiniteNum(c?.low) &&
        isFiniteNum(c?.close)
    )
    .sort((a, b) => a.time - b.time);

  const deduped = [];
  for (const candle of clean) {
    if (deduped.length && deduped[deduped.length - 1].time === candle.time) {
      deduped[deduped.length - 1] = candle;
    } else {
      deduped.push(candle);
    }
  }
  return deduped;
}

function sanitizeLineSeries(data) {
  const clean = data
    .filter((point) => Number.isInteger(point?.time) && isFiniteNum(point?.value))
    .sort((a, b) => a.time - b.time);
  const deduped = [];
  for (const point of clean) {
    if (deduped.length && deduped[deduped.length - 1].time === point.time) {
      deduped[deduped.length - 1] = point;
    } else {
      deduped.push(point);
    }
  }
  return deduped;
}

/** A minimalist dropdown with no visible arrow. Opens down on desktop, up on mobile. */
function WidgetDropdown({ options, value, onChange, isMobile }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  const current = options.find((o) => o.value === value);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  return (
    <div className="wdd-root" ref={ref}>
      <button
        className="wdd-trigger"
        type="button"
        onClick={() => setOpen((v) => !v)}
      >
        {current?.label ?? value}
      </button>
      {open && (
        <ul className={`wdd-menu ${isMobile ? 'wdd-menu--up' : 'wdd-menu--down'}`}>
          {options.map((opt) => (
            <li
              key={opt.value}
              className={`wdd-item ${opt.value === value ? 'wdd-item--active' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
            >
              {opt.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function TradingChart({ activeCoin = 'BTC', onActiveCoinChange, cfWorkerUrl = '' }) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});
  const engineRef = useRef(null);
  const [exchange, setExchange] = useState(EXCHANGES.hyperliquid);
  const [timeframe, setTimeframe] = useState('15m');
  const [error, setError] = useState('');
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
  const hasInitialFitRef = useRef(false);
  const shouldResetPriceScaleRef = useRef(false);

  const selectedSymbol = useMemo(
    () => (SYMBOL_OPTIONS.some((o) => o.value === activeCoin) ? activeCoin : 'BTC'),
    [activeCoin]
  );

  // Track mobile breakpoint
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    const handler = (e) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  useEffect(() => {
    engineRef.current = new MarketEngine({ workerUrl: cfWorkerUrl });
    return () => engineRef.current?.disconnect();
  }, []);

  useEffect(() => {
    engineRef.current?.setWorkerUrl(cfWorkerUrl);
  }, [cfWorkerUrl]);

  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return;
    let chart;
    try {
      chart = createChart(chartContainerRef.current, {
        ...chartTheme,
        width: chartContainerRef.current.clientWidth,
        height: chartContainerRef.current.clientHeight
      });

      chartRef.current = chart;
      seriesRef.current.candles = chart.addSeries(CandlestickSeries, {
        upColor: '#ffffff',
        downColor: '#000000',
        borderUpColor: '#000000',
        borderDownColor: '#000000',
        wickUpColor: '#000000',
        wickDownColor: '#000000'
      });
      seriesRef.current.evma = chart.addSeries(LineSeries, { color: '#6b7280', lineWidth: 2 });
      seriesRef.current.upperStop = chart.addSeries(LineSeries, { color: '#00ff85', lineWidth: 1 });
      seriesRef.current.lowerStop = chart.addSeries(LineSeries, { color: '#ff2b6e', lineWidth: 1 });
      seriesRef.current.longTp = chart.addSeries(LineSeries, { color: '#00d26a', lineWidth: 2 });
      seriesRef.current.shortTp = chart.addSeries(LineSeries, { color: '#ff1744', lineWidth: 2 });
    } catch (err) {
      setError(err?.message || 'Failed to initialize chart');
      return;
    }

    const resizeObserver = new ResizeObserver(() => {
      if (!chartContainerRef.current) return;
      chart.applyOptions({
        width: chartContainerRef.current.clientWidth,
        height: chartContainerRef.current.clientHeight
      });
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!engineRef.current) return undefined;
    const stopUpdate = engineRef.current.onUpdate(({ candles, indicator }) => {
      try {
        setError('');
        const safeCandles = sanitizeCandles(candles);
        seriesRef.current.candles?.setData(safeCandles);
        seriesRef.current.evma?.setData(sanitizeLineSeries(indicator.evma));
        seriesRef.current.upperStop?.setData(sanitizeLineSeries(indicator.upperStop));
        seriesRef.current.lowerStop?.setData(sanitizeLineSeries(indicator.lowerStop));
        seriesRef.current.longTp?.setData(sanitizeLineSeries(indicator.longTp));
        seriesRef.current.shortTp?.setData(sanitizeLineSeries(indicator.shortTp));
        // Fit only once on initial load so manual zoom/scale remains persistent.
        if (!hasInitialFitRef.current && safeCandles.length > 0) {
          chartRef.current?.timeScale().fitContent();
          if (shouldResetPriceScaleRef.current) {
            try {
              chartRef.current?.priceScale('left')?.applyOptions({ autoScale: true });
              seriesRef.current.candles?.priceScale()?.applyOptions({ autoScale: true });
            } catch {
              // Ignore scale reset failures to avoid interrupting chart rendering.
            }
            shouldResetPriceScaleRef.current = false;
          }
          hasInitialFitRef.current = true;
        }
      } catch (err) {
        setError(err?.message || 'Chart render error');
      }
    });

    const stopErrors = engineRef.current.onError((err) => {
      setError(err?.message || 'Market feed error');
    });

    return () => {
      stopUpdate();
      stopErrors();
    };
  }, []);

  useEffect(() => {
    if (!engineRef.current) return;
    hasInitialFitRef.current = false;
    shouldResetPriceScaleRef.current = true;
    engineRef.current
      .connect({ exchange, symbol: selectedSymbol, timeframe })
      .catch((err) => setError(err?.message || 'Failed to connect market feed'));
  }, [exchange, selectedSymbol]);

  useEffect(() => {
    engineRef.current?.updateTimeframe(timeframe);
  }, [timeframe]);

  const widgetBar = (
    <div className="tv-widget-bar">
      <WidgetDropdown
        options={SYMBOL_OPTIONS}
        value={selectedSymbol}
        onChange={(val) => onActiveCoinChange?.(val)}
        isMobile={isMobile}
      />
      <WidgetDropdown
        options={SUPPORTED_TIMEFRAMES.map((tf) => ({ label: tf, value: tf }))}
        value={timeframe}
        onChange={setTimeframe}
        isMobile={isMobile}
      />
      <WidgetDropdown
        options={EXCHANGE_OPTIONS}
        value={exchange}
        onChange={setExchange}
        isMobile={isMobile}
      />
    </div>
  );

  if (isMobile) {
    // On mobile: outer flex-column wrapper, chart grows, bar sits below
    return (
      <div className="tv-outer-wrapper">
        <section className="tv-shell tv-shell--mobile">
          {error && <div className="tv-error-banner">{error}</div>}
          <div ref={chartContainerRef} className="tv-chart-area" />
        </section>
        {widgetBar}
      </div>
    );
  }

  return (
    <section className="tv-shell">
      {widgetBar}
      {error && <div className="tv-error-banner">{error}</div>}
      <div ref={chartContainerRef} className="tv-chart-area" />
    </section>
  );
}
