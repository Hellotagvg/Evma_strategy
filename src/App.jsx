import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Briefcase,
  Home as HomeIcon,
  TrendingUp,
  Settings as SettingsIcon,
  Wallet,
  X,
  RefreshCw,
  Info,
  Calendar,
  FileText,
  AlertCircle,
  ArrowLeft,
  Terminal as TerminalIcon,
} from 'lucide-react';
import {
  fetchPositions as apiFetchPositions,
  fetchOpenOrders as apiFetchOpenOrders,
  closePosition as apiClosePosition,
  cancelOrder as apiCancelOrder,
  amendOrder as apiAmendOrder,
  createLimitOrder as apiCreateLimitOrder,
  cancelOrdersBatch as apiCancelOrdersBatch,
  setPositionTPSL as apiSetTPSL,
  configureBackendClient,
  refreshExchangeMonitor,
  subscribeBybitPrivateFeed,
  getBybitPrivateSnapshot,
} from './exchangeAPI';
import { 
  BtcIcon, 
  EthIcon, 
  SolIcon, 
  BybitIcon, 
  BybitDemoIcon, 
  HyperliquidIcon 
} from './components/Icons';
import TradingChart from './components/TradingChart';
import {
  DEFAULT_TP_RR_OPTIONS,
  getFixedRRForCoin,
  getIntentRiskSubmitValue,
  getRiskFieldState,
  getTpRrSliderMeta,
  normalizeTpRrOptions,
  resolveInitialIntentRisk,
  snapToTpRrOption,
} from './intentFormHelpers';

// No mock data — positions and limits are fetched live from exchanges

const DEFAULT_BACKEND_URL = 'http://localhost:8000';
const DEFAULT_BACKEND_PASSWORD = '';
const HTTP_TIMEOUT_MS = 8000;
// Dashboard monitoring cadence (positions/limits/session). Exceptions: WS ping 25s,
// Bybit private WS heartbeat 20s — exchange keepalives, not position refresh.
const MONITOR_POLL_MS = 5000;
const POSITIONS_STALE_UI_SEC = 7;
const MONITOR_STALE_FALLBACK_SEC = 10;

function normalizeBackendUrl(url) {
  return (url || DEFAULT_BACKEND_URL).trim().replace(/\/$/, '');
}

function backendHttpToWs(url) {
  const normalized = normalizeBackendUrl(url);
  if (normalized.startsWith('https://')) return `wss://${normalized.slice('https://'.length)}`;
  if (normalized.startsWith('http://')) return `ws://${normalized.slice('http://'.length)}`;
  return normalized;
}

export default function App() {
  const [activeTab, setActiveTab] = useState('positions');
  const [activePanel, setActivePanel] = useState(null);
  const [strategySubView, setStrategySubView] = useState('intent'); // 'intent' | 'monitoring'
  const [exchangeFilter, setExchangeFilter] = useState('All');
  const [coinFilter, setCoinFilter] = useState('All');
  const [sideFilter, setSideFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  
  // App States
  const [activeOrders, setActiveOrders] = useState([]);
  const [positions, setPositions] = useState([]);
  const [limits, setLimits] = useState([]);
  const [activeCoin, setActiveCoin] = useState('BTC');
  const [positionsLoading, setPositionsLoading] = useState(false);
  const [limitsLoading, setLimitsLoading] = useState(false);
  const [positionsError, setPositionsError] = useState(null);
  const [limitsError, setLimitsError] = useState(null);
  const [lastPositionsRefreshAt, setLastPositionsRefreshAt] = useState(null);
  const [lastLimitsRefreshAt, setLastLimitsRefreshAt] = useState(null);
  const [lastLimitsRestRefreshAt, setLastLimitsRestRefreshAt] = useState(null);
  
  // Modals state
  const [showTpSlModal, setShowTpSlModal] = useState(null); // position object or null
  const [showModifyLimitModal, setShowModifyLimitModal] = useState(null); // limit object or null
  
  // Modals Form States
  const [tpVal, setTpVal] = useState('');
  const [slVal, setSlVal] = useState('');
  const [limitModifyPrice, setLimitModifyPrice] = useState('');
  const [limitModifyQty, setLimitModifyQty] = useState('');

  // Accounts state
  const [accounts, setAccounts] = useState([]);
  const [selectedHomeAccounts, setSelectedHomeAccounts] = useState([]);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 769);
  
  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 769);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Fallback: if activeTab was stored as 'charts' from a previous session, reset to 'home'
  useEffect(() => {
    if (activeTab === 'charts') {
      setActiveTab('home');
    }
  }, []);
  const [cfWorkerUrl, setCfWorkerUrl] = useState('');
  const [backendUrl, setBackendUrl] = useState(() => (
    normalizeBackendUrl(localStorage.getItem('kinetic_backend_url') || DEFAULT_BACKEND_URL)
  ));
  const [backendPassword, setBackendPassword] = useState(() => (
    localStorage.getItem('kinetic_backend_password') || DEFAULT_BACKEND_PASSWORD
  ));
  const [accountsConfigDraft, setAccountsConfigDraft] = useState('');
  const [accountsConfigStatus, setAccountsConfigStatus] = useState(null);
  const [accountsConfigBusy, setAccountsConfigBusy] = useState(false);

  // Custom added states for new integrations
  const [backendConnected, setBackendConnected] = useState(false);
  const [sessionStatus, setSessionStatus] = useState(null);
  const [indicators, setIndicators] = useState({
    BTC: { price: 0, evma: 0, upper_stop: 0, lower_stop: 0, dist_pct: 0, atr_pct: 0, norm_atr: 0 },
    ETH: { price: 0, evma: 0, upper_stop: 0, lower_stop: 0, dist_pct: 0, atr_pct: 0, norm_atr: 0 },
    SOL: { price: 0, evma: 0, upper_stop: 0, lower_stop: 0, dist_pct: 0, atr_pct: 0, norm_atr: 0 },
  });
  const [lastIndicatorUpdate, setLastIndicatorUpdate] = useState({});
  const [timeTick, setTimeTick] = useState(Date.now());
  const bybitFeedSyncInFlightRef = useRef(false);
  const bybitFeedSyncQueuedRef = useRef(false);

  useEffect(() => {
    const timer = setInterval(() => setTimeTick(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);
  
  // Strategy Tab States
  const [selectedStrategyAccount, setSelectedStrategyAccount] = useState(null);
  const [intentCoin, setIntentCoin] = useState('BTC');
  const [intentDirection, setIntentDirection] = useState('LONG');
  const [intentRR, setIntentRR] = useState(2.0);
  const [intentRisk, setIntentRisk] = useState(1.0);
  const [tpRrOptionsByCoin, setTpRrOptionsByCoin] = useState({});
  const [logsDate, setLogsDate] = useState(new Date().toISOString().split('T')[0]);
  const [systemLogs, setSystemLogs] = useState([]);
  
  // Journal Filtering States
  const [journalTrades, setJournalTrades] = useState([]);
  const [journalCoinFilter, setJournalCoinFilter] = useState('All');
  const [journalDirFilter, setJournalDirFilter] = useState('All');
  const [journalStatusFilter, setJournalStatusFilter] = useState('All');

  // Sub-view toggle for unified Positions & Limits screen
  const [plSubView, setPlSubView] = useState('positions'); // 'positions' | 'limits'

  // Hub screen state: 'positions' = positions/limits, 'blank' = slide-out screen
  const [hubScreen, setHubScreen] = useState('blank'); // 'positions' | 'blank'

  const wsRef = useRef(null);
  const wsReconnectDelayRef = useRef(2000);
  const wsPingTimerRef = useRef(null);

  const backendHeaders = useCallback((extra = {}) => ({
    ...extra,
    'X-Kinetic-Password': backendPassword,
  }), [backendPassword]);

  const fetchWithTimeout = async (url, options = {}, timeoutMs = HTTP_TIMEOUT_MS) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };

  const backendFetch = useCallback((path, options = {}, timeoutMs = HTTP_TIMEOUT_MS) => {
    const headers = backendHeaders(options.headers || {});
    return fetchWithTimeout(`${normalizeBackendUrl(backendUrl)}${path}`, { ...options, headers }, timeoutMs);
  }, [backendHeaders, backendUrl]);

  useEffect(() => {
    configureBackendClient(normalizeBackendUrl(backendUrl), backendPassword);
    localStorage.setItem('kinetic_backend_url', normalizeBackendUrl(backendUrl));
    localStorage.setItem('kinetic_backend_password', backendPassword);
  }, [backendUrl, backendPassword]);

  const applyExchangeMonitorSnapshot = useCallback((mon) => {
    if (!mon) return;

    const fetchedAt = mon.fetched_at ? Date.parse(mon.fetched_at) : Date.now();
    const refreshAt = Number.isFinite(fetchedAt) ? fetchedAt : Date.now();

    if (Array.isArray(mon.positions)) {
      setPositions(mon.positions);
      setLastPositionsRefreshAt(refreshAt);
    }

    if (Array.isArray(mon.open_orders)) {
      setLimits(mon.open_orders.filter((order) => !order.reduceOnly));
      setLastLimitsRefreshAt(refreshAt);
      setLastLimitsRestRefreshAt(refreshAt);
    }

    const posFailed = Object.entries(mon.position_errors || {})
      .filter(([, message]) => message)
      .map(([accountId, message]) => `${accountId}: ${message}`);
    const ordFailed = Object.entries(mon.order_errors || {})
      .filter(([, message]) => message)
      .map(([accountId, message]) => `${accountId}: ${message}`);

    setPositionsError(posFailed.length ? posFailed.join(' | ') : null);
    setLimitsError(ordFailed.length ? ordFailed.join(' | ') : null);
    setPositionsLoading(false);
    setLimitsLoading(false);
  }, []);

  // WebSocket Connection
  useEffect(() => {
    let reconnectTimer = null;
    let shouldReconnect = true;
    const connectWS = () => {
      console.log('Connecting to WebSocket...');
      const wsBase = backendHttpToWs(normalizeBackendUrl(backendUrl));
      const ws = new WebSocket(`${wsBase}/ws?password=${encodeURIComponent(backendPassword)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setBackendConnected(true);
        setPositionsLoading(true);
        setLimitsLoading(true);
        wsReconnectDelayRef.current = 2000;
        if (wsPingTimerRef.current) clearInterval(wsPingTimerRef.current);
        wsPingTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try {
              ws.send('ping');
            } catch (error) {
              console.warn('WebSocket ping failed:', error);
            }
          }
        }, 25000);
        console.log('WebSocket Connected.');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'update') {
            if (data.session_status) setSessionStatus(data.session_status);
            if (data.indicators) {
              setIndicators(data.indicators);
              setLastIndicatorUpdate(prev => {
                const now = Date.now();
                const next = { ...prev };
                for (const coin in data.indicators) {
                  if (data.indicators[coin] && data.indicators[coin].price > 0) {
                    next[coin] = now;
                  }
                }
                return next;
              });
            }
            if (data.active_orders) setActiveOrders(data.active_orders);
            if (data.exchange_monitor) applyExchangeMonitorSnapshot(data.exchange_monitor);
          }
        } catch (e) {
          console.error('Error parsing WS message:', e);
        }
      };

      ws.onclose = () => {
        setBackendConnected(false);
        if (wsPingTimerRef.current) {
          clearInterval(wsPingTimerRef.current);
          wsPingTimerRef.current = null;
        }
        if (shouldReconnect) {
          const delay = wsReconnectDelayRef.current;
          wsReconnectDelayRef.current = Math.min(delay * 2, 30000);
          console.log(`WebSocket disconnected. Reconnecting in ${Math.round(delay / 1000)}s...`);
          reconnectTimer = setTimeout(connectWS, delay);
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket Error:', err);
      };
    };

    connectWS();

    return () => {
      shouldReconnect = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (wsPingTimerRef.current) {
        clearInterval(wsPingTimerRef.current);
        wsPingTimerRef.current = null;
      }
      if (wsRef.current) wsRef.current.close();
    };
  }, [backendUrl, backendPassword, applyExchangeMonitorSnapshot]);

  // REST APIs Fetching
  const fetchAccountsFromBackend = async () => {
    try {
      const resp = await backendFetch('/api/accounts');
      if (resp.ok) {
        const data = await resp.json();
        setAccounts(data);
        return data;
      } else if (resp.status === 401) {
        setAccounts([]);
        setBackendConnected(false);
        console.warn('Backend authentication failed.');
      }
    } catch (e) {
      console.warn('Backend server not responding.', e);
    }
    return null;
  };

  const loadAccountsConfigDraft = async () => {
    setAccountsConfigStatus(null);
    const data = await fetchAccountsFromBackend();
    if (!data) {
      setAccountsConfigStatus({ type: 'error', message: 'Could not load accounts from backend.' });
      return;
    }
    setAccountsConfigDraft(JSON.stringify({ accounts: data }, null, 2));
    setAccountsConfigStatus({ type: 'info', message: `Loaded ${data.length} account(s) from backend.` });
  };

  const applyAccountsConfigDraft = async () => {
    setAccountsConfigStatus(null);
    let parsed;
    try {
      parsed = JSON.parse(accountsConfigDraft);
    } catch (e) {
      setAccountsConfigStatus({ type: 'error', message: `Invalid JSON: ${e.message}` });
      return;
    }
    if (!parsed || !Array.isArray(parsed.accounts)) {
      setAccountsConfigStatus({ type: 'error', message: 'JSON must be an object with an "accounts" array.' });
      return;
    }

    setAccountsConfigBusy(true);
    try {
      const resp = await backendFetch('/api/accounts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accounts: parsed.accounts }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = body.detail;
        const message = Array.isArray(detail?.errors)
          ? detail.errors.join(' ')
          : (typeof detail === 'string' ? detail : 'Failed to apply account configuration.');
        setAccountsConfigStatus({ type: 'error', message });
        return;
      }
      await fetchAccountsFromBackend();
      setAccountsConfigStatus({
        type: 'success',
        message: `Applied ${body.accounts_count ?? parsed.accounts.length} account(s) without restart.`,
      });
    } catch (e) {
      setAccountsConfigStatus({ type: 'error', message: e.message || 'Request failed.' });
    } finally {
      setAccountsConfigBusy(false);
    }
  };

  const reloadAccountsConfigFromDisk = async () => {
    setAccountsConfigStatus(null);
    setAccountsConfigBusy(true);
    try {
      const resp = await backendFetch('/api/accounts/reload', { method: 'POST' });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = body.detail;
        const message = Array.isArray(detail?.errors)
          ? detail.errors.join(' ')
          : (typeof detail === 'string' ? detail : 'Reload failed.');
        setAccountsConfigStatus({ type: 'error', message });
        return;
      }
      await loadAccountsConfigDraft();
      setAccountsConfigStatus({
        type: 'success',
        message: `Reloaded ${body.accounts_count ?? 0} account(s) from disk.`,
      });
    } catch (e) {
      setAccountsConfigStatus({ type: 'error', message: e.message || 'Request failed.' });
    } finally {
      setAccountsConfigBusy(false);
    }
  };

  const fetchSessionStatus = async () => {
    try {
      const resp = await backendFetch('/api/session');
      if (resp.ok) {
        const data = await resp.json();
        setSessionStatus(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchSystemLogs = async () => {
    try {
      const resp = await backendFetch(`/api/logs?date=${encodeURIComponent(logsDate)}`);
      if (resp.ok) {
        const data = await resp.json();
        setSystemLogs(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchJournalTrades = async () => {
    try {
      const resp = await backendFetch('/api/journal/query');
      if (resp.ok) {
        const data = await resp.json();
        setJournalTrades(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchAccountsFromBackend();
    fetchSessionStatus();
  }, [backendFetch]);

  useEffect(() => {
    const interval = setInterval(() => {
      fetchAccountsFromBackend();
      fetchSessionStatus();
    }, MONITOR_POLL_MS);
    return () => clearInterval(interval);
  }, [backendFetch]);

  useEffect(() => {
    if (!accounts.length) {
      setSelectedStrategyAccount(null);
      return;
    }
    if (!selectedStrategyAccount) return;
    const stillExists = accounts.some((acc) => acc.id === selectedStrategyAccount.id);
    if (!stillExists) {
      setSelectedStrategyAccount(null);
    }
  }, [accounts, selectedStrategyAccount]);



  useEffect(() => {
    if (activePanel === 'logs') {
      fetchSystemLogs();
    }
  }, [activePanel, logsDate, backendFetch]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await backendFetch('/api/dashboard/config');
        if (!resp.ok || cancelled) return;
        const data = await resp.json();
        setCfWorkerUrl(data.cloudflare_worker_url || '');
      } catch (e) {
        console.warn('Dashboard config fetch failed:', e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backendFetch]);

  // Submit Trade Intent to Backend
  const handlePostIntent = async (e) => {
    e.preventDefault();
    if (!selectedStrategyAccount) return;
    if (!selectedStrategyAccount.enabled) {
      alert('This account is disabled. Enable it in accounts.json before placing intents.');
      return;
    }

    try {
      const payload = {
        account_id: selectedStrategyAccount.id,
        coin: intentCoin,
        direction: intentDirection,
        rr: getFixedRRForCoin(selectedStrategyAccount, intentCoin) ?? parseFloat(intentRR),
        risk_value: getIntentRiskSubmitValue(selectedStrategyAccount, intentRisk)
      };

      const resp = await backendFetch('/api/intent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }, 12000);

      if (resp.ok) {
        alert('Trading intent sent successfully! Limits dragging on EVMA line.');
        setActiveTab('limits');
      } else {
        const errorData = await resp.json();
        alert(`Failed to place intent: ${errorData.detail}`);
      }
    } catch (e) {
      alert(`Connection failed: Could not reach backend server at ${normalizeBackendUrl(backendUrl)}`);
    }
  };

  // Switch Strategy Accounts Config
  const handleSelectAccount = (acc) => {
    setSelectedStrategyAccount(acc);
    setIntentRisk(resolveInitialIntentRisk(acc));
    const fixedRR = getFixedRRForCoin(acc, intentCoin);
    if (fixedRR != null) setIntentRR(fixedRR);
  };

  // Toggle selected accounts on home page
  const handleToggleHomeAccount = (accId) => {
    setSelectedHomeAccounts((prev) =>
      prev.includes(accId) ? prev.filter((id) => id !== accId) : [...prev, accId]
    );
  };

  useEffect(() => {
    if (!selectedStrategyAccount) return;
    const fixedRR = getFixedRRForCoin(selectedStrategyAccount, intentCoin);
    if (fixedRR != null) {
      setIntentRR(fixedRR);
      return;
    }
    const cached = tpRrOptionsByCoin[intentCoin];
    const options = normalizeTpRrOptions(cached || DEFAULT_TP_RR_OPTIONS);
    setIntentRR((prev) => snapToTpRrOption(prev, options));
  }, [selectedStrategyAccount, intentCoin, tpRrOptionsByCoin]);

  useEffect(() => {
    if (!selectedStrategyAccount) return;
    const riskState = getRiskFieldState(selectedStrategyAccount);
    if (riskState.mode === 'fixed') {
      setIntentRisk(riskState.submitValue);
    }
  }, [selectedStrategyAccount]);

  useEffect(() => {
    if (!selectedStrategyAccount) return;
    const coin = intentCoin;
    let cancelled = false;
    (async () => {
      try {
        const resp = await backendFetch(`/api/strategy/config?coin=${encodeURIComponent(coin)}`);
        if (!resp.ok || cancelled) return;
        const data = await resp.json();
        const options = normalizeTpRrOptions(data.tp_rr_options);
        setTpRrOptionsByCoin((prev) => ({ ...prev, [coin]: options }));
        if (!getFixedRRForCoin(selectedStrategyAccount, coin)) {
          setIntentRR((prev) => snapToTpRrOption(prev, options));
        }
      } catch (e) {
        console.warn(`Strategy config fetch failed for ${coin}:`, e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedStrategyAccount, intentCoin, backendFetch]);

  // Live exchange sync — prefer Bybit private WS snapshots and fall back to REST when needed
  const refreshExchangeData = useCallback(async (options = {}) => {
    const { silent = false } = options;
    const enabledAccounts = accounts.filter((acc) => acc.enabled);
    if (enabledAccounts.length === 0) return;

    if (bybitFeedSyncInFlightRef.current) {
      bybitFeedSyncQueuedRef.current = true;
      return;
    }

    bybitFeedSyncInFlightRef.current = true;
    const now = Date.now();

    try {
      const positionRows = [];
      const orderRows = [];
      const positionFetchAccounts = [];
      const orderFetchAccounts = [];
      const positionAccountNames = [];
      const orderAccountNames = [];

      enabledAccounts.forEach((acc) => {
        const exchange = String(acc.exchange || '').toLowerCase();
        if (exchange === 'bybit') {
          const snapshot = getBybitPrivateSnapshot(acc);
          if (snapshot?.hasPositionSnapshot) {
            positionRows.push(...(Array.isArray(snapshot.positions) ? snapshot.positions : []));
          } else {
            positionFetchAccounts.push(acc);
            positionAccountNames.push(acc.name || acc.id || 'Unknown account');
          }

          if (snapshot?.hasOrderSnapshot) {
            orderRows.push(...(Array.isArray(snapshot.orders) ? snapshot.orders : []));
          } else {
            orderFetchAccounts.push(acc);
            orderAccountNames.push(acc.name || acc.id || 'Unknown account');
          }
          return;
        }

        positionFetchAccounts.push(acc);
        orderFetchAccounts.push(acc);
        positionAccountNames.push(acc.name || acc.id || 'Unknown account');
        orderAccountNames.push(acc.name || acc.id || 'Unknown account');
      });

      const positionFetchPromise = positionFetchAccounts.length
        ? Promise.allSettled(positionFetchAccounts.map((acc) => apiFetchPositions(acc, cfWorkerUrl)))
        : Promise.resolve([]);
      const orderFetchPromise = orderFetchAccounts.length
        ? Promise.allSettled(orderFetchAccounts.map((acc) => apiFetchOpenOrders(acc, cfWorkerUrl)))
        : Promise.resolve([]);

      if (!silent) {
        setPositionsLoading(positionFetchAccounts.length > 0);
        setLimitsLoading(orderFetchAccounts.length > 0);
      }

      if (!silent) {
        setPositionsError(null);
        setLimitsError(null);
      }

      const [settledPositions, settledOrders] = await Promise.all([positionFetchPromise, orderFetchPromise]);

      const positionErrors = [];
      settledPositions.forEach((result, idx) => {
        const accountName = positionAccountNames[idx] || 'Unknown account';
        if (result.status === 'fulfilled') {
          positionRows.push(...(Array.isArray(result.value) ? result.value : []));
        } else {
          const msg = result.reason?.message || String(result.reason || 'Unknown error');
          positionErrors.push(`${accountName}: ${msg}`);
          console.warn(`Positions fetch failed for ${accountName}:`, msg);
        }
      });

      const orderErrors = [];
      let restOrderFetchedAt = null;
      settledOrders.forEach((result, idx) => {
        const accountName = orderAccountNames[idx] || 'Unknown account';
        if (result.status === 'fulfilled') {
          orderRows.push(...(Array.isArray(result.value) ? result.value : []));
          restOrderFetchedAt = restOrderFetchedAt || now;
        } else {
          const msg = result.reason?.message || String(result.reason || 'Unknown error');
          orderErrors.push(`${accountName}: ${msg}`);
          console.warn(`Orders fetch failed for ${accountName}:`, msg);
        }
      });

      if (positionRows.length > 0 || positionErrors.length === 0) {
        setPositions(positionRows);
        setLastPositionsRefreshAt(now);
      }
      setPositionsError(positionErrors.length ? positionErrors.join(' | ') : null);

      const visibleOrders = orderRows.filter((order) => !order.reduceOnly);
      if (visibleOrders.length > 0 || orderErrors.length === 0) {
        setLimits(visibleOrders);
        setLastLimitsRefreshAt(now);
      }
      if (restOrderFetchedAt) {
        setLastLimitsRestRefreshAt(restOrderFetchedAt);
      }
      setLimitsError(orderErrors.length ? orderErrors.join(' | ') : null);
    } catch (e) {
      const message = e?.message || String(e);
      if (!silent) {
        setPositionsError(message);
        setLimitsError(message);
      }
    } finally {
      if (!silent) {
        setPositionsLoading(false);
        setLimitsLoading(false);
      }
      bybitFeedSyncInFlightRef.current = false;
      if (bybitFeedSyncQueuedRef.current) {
        bybitFeedSyncQueuedRef.current = false;
        void refreshExchangeData({ silent: true });
      }
    }
  }, [accounts, cfWorkerUrl]);

  const refreshMonitoring = useCallback(async (options = {}) => {
    const { silent = false } = options;
    if (backendConnected) {
      if (!silent) {
        setPositionsLoading(true);
        setLimitsLoading(true);
      }
      try {
        const snapshot = await refreshExchangeMonitor();
        applyExchangeMonitorSnapshot(snapshot);
      } catch (error) {
        const message = error?.message || String(error);
        if (!silent) {
          setPositionsError(message);
          setLimitsError(message);
        }
      } finally {
        if (!silent) {
          setPositionsLoading(false);
          setLimitsLoading(false);
        }
      }
      return;
    }
    await refreshExchangeData(options);
  }, [backendConnected, applyExchangeMonitorSnapshot, refreshExchangeData]);

  // Browser-side polling fallback when backend WebSocket is offline
  useEffect(() => {
    if (accounts.length === 0 || backendConnected) return;
    refreshExchangeData();
    const interval = setInterval(refreshExchangeData, MONITOR_POLL_MS);
    return () => clearInterval(interval);
  }, [refreshExchangeData, backendConnected, accounts.length]);

  // REST fallback when WS is connected but position/limit snapshots stop updating
  useEffect(() => {
    if (!backendConnected || accounts.length === 0) return;

    const checkStale = () => {
      const now = Date.now();
      const posAgeSec = lastPositionsRefreshAt
        ? (now - lastPositionsRefreshAt) / 1000
        : Infinity;
      const limitsTs = lastLimitsRestRefreshAt || lastLimitsRefreshAt;
      const limAgeSec = limitsTs ? (now - limitsTs) / 1000 : Infinity;
      if (posAgeSec > MONITOR_STALE_FALLBACK_SEC || limAgeSec > MONITOR_STALE_FALLBACK_SEC) {
        void refreshMonitoring({ silent: true });
      }
    };

    const interval = setInterval(checkStale, MONITOR_POLL_MS);
    return () => clearInterval(interval);
  }, [
    backendConnected,
    accounts.length,
    lastPositionsRefreshAt,
    lastLimitsRefreshAt,
    lastLimitsRestRefreshAt,
    refreshMonitoring,
  ]);

  useEffect(() => {
    const bybitAccounts = accounts.filter(
      (acc) => acc.enabled && String(acc.exchange || '').toLowerCase() === 'bybit'
    );
    if (bybitAccounts.length === 0 || backendConnected) return;

    const unsubscribers = bybitAccounts.map((account) => subscribeBybitPrivateFeed(account, {
      onUpdate: () => {
        void refreshExchangeData({ silent: true });
      },
      onError: (err) => {
        console.warn(`Bybit private feed error for ${account.name || account.id}:`, err);
      },
    }));

    return () => {
      unsubscribers.forEach((unsubscribe) => {
        try {
          unsubscribe();
        } catch (err) {
          console.warn('Bybit private feed unsubscribe failed:', err);
        }
      });
    };
  }, [accounts, refreshExchangeData, backendConnected]);

  // Close a position via exchange API
  const handleClosePosition = async (pos) => {
    if (!window.confirm(`Market-close ${pos.symbol} ${pos.side} (${pos.size})?`)) return;
    const account = accounts.find(a => a.id === pos.exchange);
    if (!account) return alert('Account not found for this position.');
    try {
      await apiClosePosition(account, pos.rawSymbol, pos.side, pos.sizeVal, cfWorkerUrl);
      await refreshMonitoring();
    } catch (e) {
      alert(`Close failed: ${e.message}`);
    }
  };

  // Open TP/SL modification modal
  const openTpSl = (pos) => {
    setShowTpSlModal(pos);
    setTpVal(pos.tp || '');
    setSlVal(pos.sl || '');
  };

  // Save TP/SL via exchange API
  const saveTpSl = async () => {
    const pos = showTpSlModal;
    if (!pos) return;
    const account = accounts.find(a => a.id === pos.exchange);
    if (!account) return alert('Account not found.');
    try {
      await apiSetTPSL(
        account,
        pos.rawSymbol,
        pos.side,
        tpVal ? parseFloat(tpVal) : null,
        slVal ? parseFloat(slVal) : null,
        cfWorkerUrl
      );
      setShowTpSlModal(null);
      await refreshMonitoring();
    } catch (e) {
      alert(`Set TP/SL failed: ${e.message}`);
    }
  };

  // Cancel limit order via exchange API
  const handleCancelLimit = async (lim) => {
    if (!window.confirm(`Cancel order: ${lim.symbol} ${lim.side} @ ${lim.price}?`)) return;
    const account = accounts.find(a => a.id === lim.exchange);
    if (!account) return alert('Account not found for this order.');
    try {
      await apiCancelOrder(account, lim.rawSymbol, lim.orderId, cfWorkerUrl);
      await refreshMonitoring();
    } catch (e) {
      alert(`Cancel failed: ${e.message}`);
    }
  };

  // Cancel all limit orders matching filters (exchange API)
  const handleCancelAll = async () => {
    const toCancel = limits.filter((lim) => {
      const matchExchange = exchangeFilter === 'All' || lim.exchange === exchangeFilter;
      const matchCoin = coinFilter === 'All' || lim.baseSymbol === coinFilter;
      const matchSide = sideFilter === 'All' || lim.side === sideFilter;
      const matchSearch =
        searchQuery === '' || lim.symbol.toLowerCase().includes(searchQuery.toLowerCase());
      return !lim.reduceOnly && matchExchange && matchCoin && matchSide && matchSearch;
    });
    if (toCancel.length === 0) {
      alert('No orders match the current filters.');
      return;
    }
    if (!window.confirm(`Cancel ${toCancel.length} order(s) on the exchange?`)) return;
    try {
      const result = await apiCancelOrdersBatch(
        toCancel.map((lim) => ({
          account_id: lim.exchange,
          symbol: lim.rawSymbol,
          order_id: String(lim.orderId),
        }))
      );
      if (result.errors?.length) {
        alert(`Cancelled ${result.cancelled ?? 0}. Some failed:\n${result.errors.join('\n')}`);
      }
      await refreshMonitoring();
    } catch (e) {
      alert(`Cancel all failed: ${e.message}`);
    }
  };

  // Open limit modification modal
  const openModifyLimit = (lim) => {
    setShowModifyLimitModal(lim);
    setLimitModifyPrice(lim.price);
    setLimitModifyQty(lim.amount);
  };

  // Save limit modification (exchange API)
  const saveLimitModify = async () => {
    const lim = showModifyLimitModal;
    if (!lim) return;
    const price = parseFloat(limitModifyPrice);
    const qty = parseFloat(limitModifyQty);
    if (!Number.isFinite(price) || !Number.isFinite(qty) || price <= 0 || qty <= 0) {
      alert('Please enter valid price and quantity values.');
      return;
    }
    const account = accounts.find((a) => a.id === lim.exchange);
    if (!account) return alert('Account not found for this order.');
    try {
      await apiAmendOrder(account, lim.rawSymbol, lim.orderId, lim.side, price, qty, cfWorkerUrl);
      setShowModifyLimitModal(null);
      await refreshMonitoring();
    } catch (e) {
      alert(`Modify failed: ${e.message}`);
    }
  };



  const renderCoinIcon = (symbol, size = 20) => {
    switch (symbol) {
      case 'BTC': return <BtcIcon size={size} />;
      case 'ETH': return <EthIcon size={size} />;
      case 'SOL': return <SolIcon size={size} />;
      default: return null;
    }
  };

  const renderStatusLight = (coin, size = '8px') => {
    const lastUpdate = lastIndicatorUpdate[coin];
    let status = 'red';
    let title = 'Feed disconnected';
    
    if (backendConnected && lastUpdate) {
      const elapsed = Date.now() - lastUpdate;
      if (elapsed < 120000) { // 2 minutes
        status = 'green';
        title = `Feed active (last: ${Math.round(elapsed / 1000)}s ago)`;
      } else {
        status = 'orange';
        title = `Feed stale (last: ${Math.round(elapsed / 60000)}m ago)`;
      }
    }
    
    const colorMap = {
      green: '#00ff66',
      orange: '#ffaa00',
      red: '#ff3333',
    };
    
    return (
      <span 
        style={{
          display: 'inline-block',
          width: size,
          height: size,
          borderRadius: '50%',
          backgroundColor: colorMap[status],
          boxShadow: `0 0 6px ${colorMap[status]}`,
          marginLeft: '6px',
          verticalAlign: 'middle',
          transition: 'all 0.3s ease'
        }}
        title={title}
      />
    );
  };

  const renderExchangeIcon = (exchange, size = 20) => {
    const exStr = String(exchange).toLowerCase();
    if (exStr.includes('bybit-demo') || exStr.includes('demo')) return <BybitDemoIcon size={size} />;
    if (exStr.includes('bybit')) return <BybitIcon size={size} />;
    if (exStr.includes('hyperliquid') || exStr.includes('hl')) return <HyperliquidIcon size={size} />;
    return null;
  };

  const formatExchangeName = (exchange) => {
    const exStr = String(exchange).toLowerCase();
    if (exStr.includes('bybit-live')) return 'Bybit Live';
    if (exStr.includes('bybit-demo')) return 'Bybit Demo';
    if (exStr.includes('hyperliquid')) return 'Hyperliquid';
    return exchange;
  };

  /** Strategy account widget — customize placeholder rows here. */
  const getAccountRiskSummary = (acc) => {
    const cfg = acc?.risk_config || acc?.risk;
    if (!cfg) return 'Not configured';
    const mode = (cfg.type || cfg.mode || '').replace(/_/g, ' ');
    const val = cfg.value ?? cfg.default;
    if (val == null) return mode || '—';
    const suffix = String(cfg.type || cfg.mode || '').includes('pct') ? '%' : '';
    return `${mode} · ${val}${suffix}`;
  };

  const getAccountMarginSummary = (acc) => {
    const rawMarginPct = acc?.margin_pct ?? acc?.margin?.pct ?? acc?.margin?.percent ?? acc?.margin?.value;
    const marginPct = Number(rawMarginPct);
    if (!Number.isFinite(marginPct) || marginPct <= 0) return 'Not configured';
    const balance = Number(acc?.risk_config?.default_balance || 10000.0);
    const marginBudget = balance * (marginPct / 100);
    return `${marginPct}% · $${marginBudget.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  };

  const getAccountMarginBudget = (acc) => {
    const rawMarginPct = acc?.margin_pct ?? acc?.margin?.pct ?? acc?.margin?.percent ?? acc?.margin?.value;
    const marginPct = Number(rawMarginPct);
    if (!Number.isFinite(marginPct) || marginPct <= 0) return 'Not configured';
    const balance = Number(acc?.risk_config?.default_balance || 10000.0);
    const marginBudget = balance * (marginPct / 100);
    return `$${marginBudget.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  };

  const renderStrategyAccountWidget = (acc) => {
    const isSelected = selectedStrategyAccount?.id === acc.id;
    const venueLabel = formatExchangeName(acc.exchange || acc.id);
    return (
      <div
        key={acc.id}
        role="button"
        tabIndex={0}
        className={`trading-card strategy-account-card${isSelected ? ' selected' : ''}${acc.enabled ? '' : ' disabled-account'}`}
        onClick={() => handleSelectAccount(acc)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleSelectAccount(acc);
          }
        }}
      >
        <div className="card-header">
          <div className="card-title-sec">
            {renderExchangeIcon(acc.id, 18)}
            <span className="token-symbol" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '2px' }}>
              <span>{acc.name}</span>
              <span style={{ fontSize: '11px', fontWeight: 500, color: 'var(--text-secondary)' }}>
                {acc.id}
              </span>
            </span>
          </div>
          <span className={`tag ${acc.enabled ? 'tag-long' : 'tag-demo'}`}>
            {acc.enabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>

        <div className="card-body-row">
          <div className="data-item">
            <span className="data-label">Exchange</span>
            <span className="data-value">{venueLabel}</span>
          </div>
          <div className="data-item" style={{ textAlign: 'right' }}>
            <span className="data-label">Risk mode</span>
            <span className="data-value" style={{ color: 'var(--accent-cyan)', fontSize: '12px' }}>
              {getAccountRiskSummary(acc)}
            </span>
          </div>
        </div>

        <div className="card-body-row">
          <div className="data-item">
            <span className="data-label">Max position</span>
            <span className="data-value">
              ${Number(acc.max_position_size_usd || 0).toLocaleString()}
            </span>
          </div>
          <div className="data-item" style={{ textAlign: 'right' }}>
            <span className="data-label">Max leverage</span>
            <span className="data-value">{acc.max_leverage ?? '—'}x</span>
          </div>
        </div>

        <div className="card-body-row">
          <div className="data-item">
            <span className="data-label">Margin pct</span>
            <span className="data-value" style={{ color: 'var(--accent-cyan)', fontSize: '12px' }}>
              {acc.margin_pct ?? acc.margin?.pct ?? '—'}%
            </span>
          </div>
          <div className="data-item" style={{ textAlign: 'right' }}>
            <span className="data-label">Margin budget</span>
            <span className="data-value" style={{ color: 'var(--accent-cyan)', fontSize: '12px' }}>
              {getAccountMarginBudget(acc)}
            </span>
          </div>
        </div>

        <div className="card-body-row">
          <div className="data-item">
            <span className="data-label">Max positions</span>
            <span className="data-value" style={{ color: 'var(--accent-cyan)', fontSize: '12px' }}>
              {acc.max_positions ?? 3}
            </span>
          </div>
          <div className="data-item" style={{ textAlign: 'right' }}>
            {/* Empty right cell */}
          </div>
        </div>

        {isSelected && (
          <div className="strategy-account-selected-hint">
            Selected — intent form below
          </div>
        )}
      </div>
    );
  };

  // Filtered lists
  const filteredPositions = positions.filter(pos => {
    const matchExchange = exchangeFilter === 'All' || pos.exchange === exchangeFilter;
    const matchCoin = coinFilter === 'All' || pos.baseSymbol === coinFilter;
    const matchSide = sideFilter === 'All' || pos.side === sideFilter;
    return matchExchange && matchCoin && matchSide;
  });

  const filteredLimits = limits.filter(lim => {
    const matchExchange = exchangeFilter === 'All' || lim.exchange === exchangeFilter;
    const matchCoin = coinFilter === 'All' || lim.baseSymbol === coinFilter;
    const matchSide = sideFilter === 'All' || lim.side === sideFilter;
    const matchSearch = searchQuery === '' || lim.symbol.toLowerCase().includes(searchQuery.toLowerCase());
    return !lim.reduceOnly && matchExchange && matchCoin && matchSide && matchSearch;
  });

  // Calculate selected balance on the Home page
  const selectedBalance = selectedHomeAccounts.reduce((sum, accId) => {
    const acc = accounts.find((a) => a.id === accId);
    return sum + (acc?.risk_config?.default_balance || 10000.0);
  }, 0);
  const activePositionsCount = filteredPositions.length;
  const activeLimitsCount = filteredLimits.length;
  const netUnrealizedPnl = positions.reduce((acc, curr) => acc + curr.pnl, 0);

  const getRefreshState = (timestamp, { freshSec = 10, staleSec = 30 } = {}) => {
    if (!timestamp) {
      return { label: 'No successful refresh yet', tone: 'red' };
    }

    const ageSec = Math.max(0, Math.floor((timeTick - timestamp) / 1000));
    if (ageSec < freshSec) {
      return { label: `Fresh • ${ageSec}s ago`, tone: 'green' };
    }
    if (ageSec < staleSec) {
      return { label: `Stale • ${ageSec}s ago`, tone: 'amber' };
    }

    const mins = Math.floor(ageSec / 60);
    const secs = ageSec % 60;
    return { label: `Old • ${mins}m ${secs}s ago`, tone: 'red' };
  };

  const positionsAgeSec = lastPositionsRefreshAt
    ? Math.max(0, Math.floor((timeTick - lastPositionsRefreshAt) / 1000))
    : null;
  const positionsRefreshState = positionsAgeSec === null
    ? { label: 'No successful refresh yet', tone: 'red' }
    : positionsAgeSec < POSITIONS_STALE_UI_SEC
      ? null
      : getRefreshState(lastPositionsRefreshAt);
  const limitsAgeSec = (lastLimitsRestRefreshAt || lastLimitsRefreshAt)
    ? Math.max(0, Math.floor((timeTick - (lastLimitsRestRefreshAt || lastLimitsRefreshAt)) / 1000))
    : null;
  const limitsRefreshState = limitsAgeSec === null
    ? { label: 'No successful refresh yet', tone: 'red' }
    : limitsAgeSec < POSITIONS_STALE_UI_SEC
      ? null
      : getRefreshState(lastLimitsRestRefreshAt || lastLimitsRefreshAt);

  // Journal filtering logic
  const filteredJournalTrades = journalTrades.filter(t => {
    const mCoin = journalCoinFilter === 'All' || t.coin === journalCoinFilter;
    const mDir = journalDirFilter === 'All' || t.direction === journalDirFilter;
    const mStatus = journalStatusFilter === 'All' || t.status === journalStatusFilter;
    return mCoin && mDir && mStatus;
  });

  const fixedRRForIntent = selectedStrategyAccount
    ? getFixedRRForCoin(selectedStrategyAccount, intentCoin)
    : null;
  const effectiveIntentRR = fixedRRForIntent ?? intentRR;
  const intentRiskState = selectedStrategyAccount
    ? getRiskFieldState(selectedStrategyAccount)
    : null;
  const intentTpRrOptions = normalizeTpRrOptions(
    tpRrOptionsByCoin[intentCoin] || DEFAULT_TP_RR_OPTIONS
  );
  const intentTpRrMeta = getTpRrSliderMeta(intentTpRrOptions);

  const formatRiskDisplay = (value, isPct) => (isPct ? `${value}%` : `$${value}`);

  const renderIntentRiskBlock = () => {
    if (!intentRiskState || intentRiskState.mode === 'unknown') {
      return (
        <div className="form-group intent-risk intent-risk--unknown">
          <label className="form-label">Risk</label>
          <div className="intent-risk-panel">
            <p className="intent-risk-message">Risk is not configured for this account.</p>
          </div>
        </div>
      );
    }

    if (intentRiskState.mode === 'fixed') {
      return (
        <div className="form-group intent-risk intent-risk--fixed">
          <div className="intent-risk-header">
            <label className="form-label">Risk</label>
            <span className="intent-risk-value-badge">
              {formatRiskDisplay(intentRiskState.value, intentRiskState.isPct)}
            </span>
          </div>
          <div className="intent-risk-panel" aria-disabled="true">
            <p className="intent-risk-message">{intentRiskState.message}</p>
            <input
              type="range"
              className="form-slider intent-risk-slider"
              disabled
              readOnly
              min={intentRiskState.min}
              max={intentRiskState.max}
              step={intentRiskState.isPct ? 0.1 : 1}
              value={intentRiskState.value}
              tabIndex={-1}
            />
          </div>
        </div>
      );
    }

    return (
      <div className="form-group intent-risk intent-risk--adjustable">
        <div className="intent-risk-header">
          <label className="form-label">
            Risk <span className="intent-risk-hint">({intentRiskState.labelHint})</span>
          </label>
          <span className="intent-risk-value-badge">
            {intentRisk}
            {intentRiskState.unit}
          </span>
        </div>
        <div className="intent-risk-panel">
          <input
            type="range"
            min={intentRiskState.min}
            max={intentRiskState.max}
            step={intentRiskState.step}
            className="form-slider intent-risk-slider"
            value={intentRisk}
            onChange={(ev) => setIntentRisk(parseFloat(ev.target.value))}
          />
          <div className="intent-risk-range-labels">
            <span>
              Min: {intentRiskState.min}
              {intentRiskState.isPct ? '%' : ' USD'}
            </span>
            <span>
              Max: {intentRiskState.max}
              {intentRiskState.isPct ? '%' : ' USD'}
            </span>
          </div>
        </div>
      </div>
    );
  };

  const renderIntentRRBlock = () => (
    <div className={`form-group intent-rr${fixedRRForIntent != null ? ' intent-rr--fixed' : ''}`}>
      <div className="intent-risk-header">
        <label className="form-label">Target Reward Ratio (R:R)</label>
        <span className="intent-risk-value-badge intent-rr-value-badge">{effectiveIntentRR}x</span>
      </div>
      {fixedRRForIntent != null ? (
        <div className="intent-rr-panel">
          <p className="intent-rr-message">
            Take profit R:R is fixed at <strong>{fixedRRForIntent}R</strong> for {intentCoin} on this
            account.
          </p>
        </div>
      ) : (
        <>
          <input
            type="range"
            min={intentTpRrMeta.min}
            max={intentTpRrMeta.max}
            step={intentTpRrMeta.step}
            className="form-slider intent-rr-slider"
            value={intentRR}
            onChange={(ev) =>
              setIntentRR(snapToTpRrOption(parseFloat(ev.target.value), intentTpRrOptions))
            }
          />
          <div className="intent-risk-range-labels">
            <span>{intentTpRrMeta.min}x</span>
            <span>{intentTpRrMeta.max}x</span>
          </div>
          <p className="intent-rr-options-hint">
            Strategy options: {intentTpRrOptions.map((v) => `${v}x`).join(' · ')}
          </p>
        </>
      )}
    </div>
  );

  // Calculate concurrent positions limits for selected strategy account
  const activeAccountOrders = selectedStrategyAccount 
    ? activeOrders.filter(o => o.account_id === selectedStrategyAccount.id)
    : [];
  const hasActiveForCoin = selectedStrategyAccount
    ? activeAccountOrders.some(o => o.coin === intentCoin)
    : false;
  const maxPositionsCap = selectedStrategyAccount?.max_positions ?? 3;
  const limitReached = selectedStrategyAccount
    ? activeAccountOrders.length >= maxPositionsCap
    : false;

  return (
    <div className={`app-container ${activeTab === 'positions' ? 'is-hub' : ''}`}>
      {/* Desktop Header */}
      <header className="desktop-header">
        <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          KINETIC<span className="brand-accent">_TERMINAL</span>
          <span className={`status-dot-badge ${backendConnected ? 'green' : 'red'}`} title={backendConnected ? "Connected to backend core" : "Backend offline / mock active"}>
            {backendConnected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
        <nav className="desktop-nav">
          <div 
            className={`nav-link ${activeTab === 'positions' ? 'active' : ''}`}
            onClick={() => setActiveTab('positions')}
          >
            Hub
          </div>
          <div 
            className={`nav-link ${activeTab === 'home' ? 'active' : ''}`}
            onClick={() => setActiveTab('home')}
          >
            Home
          </div>
          <div 
            className={`nav-link ${activeTab === 'strategy' ? 'active' : ''}`}
            onClick={() => setActiveTab('strategy')}
          >
            Strategy
          </div>
        </nav>
        <div className="header-actions">
          <button
            className={`header-btn ${activePanel === 'journal' ? 'active' : ''}`}
            onClick={() => {
              if (activePanel === 'journal') {
                setActivePanel(null);
              } else {
                fetchJournalTrades();
                setActivePanel('journal');
              }
            }}
            title="Trade Journal"
          >
            <FileText size={18} />
          </button>
          <button
            className={`header-btn ${activePanel === 'logs' ? 'active' : ''}`}
            onClick={() => {
              if (activePanel === 'logs') {
                setActivePanel(null);
              } else {
                fetchSystemLogs();
                setActivePanel('logs');
              }
            }}
            title="Console Logs"
          >
            <TerminalIcon size={18} />
          </button>
          <button
            className={`header-btn ${activePanel === 'settings' ? 'active' : ''}`}
            onClick={() => setActivePanel(prev => prev === 'settings' ? null : 'settings')}
            title="Settings"
          >
            <SettingsIcon size={18} />
          </button>
        </div>
      </header>

      {/* Mobile Header */}
      <header className="mobile-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="mobile-brand">KINETIC</span>
          <span className={`status-dot-badge ${backendConnected ? 'green' : 'red'}`} />
        </div>
        <div className="mobile-header-actions">
          <button 
            className={`mobile-header-btn ${activePanel === 'journal' ? 'active' : ''}`}
            onClick={() => {
              if (activePanel === 'journal') {
                setActivePanel(null);
              } else {
                fetchJournalTrades();
                setActivePanel('journal');
              }
            }}
            title="Trade Journal"
          >
            <FileText size={16} />
          </button>
          <button 
            className={`mobile-header-btn ${activePanel === 'logs' ? 'active' : ''}`}
            onClick={() => {
              if (activePanel === 'logs') {
                setActivePanel(null);
              } else {
                fetchSystemLogs();
                setActivePanel('logs');
              }
            }}
            title="Console Logs"
          >
            <TerminalIcon size={16} />
          </button>
          <button 
            className={`mobile-header-btn ${activePanel === 'settings' ? 'active' : ''}`}
            onClick={() => setActivePanel(prev => prev === 'settings' ? null : 'settings')}
            title="Settings"
          >
            <SettingsIcon size={16} />
          </button>
        </div>
      </header>

      {/* Mobile Bottom Navigation */}
      <nav className="mobile-nav">
        <button 
          className={`mobile-nav-item ${activeTab === 'positions' ? 'active' : ''}`}
          onClick={() => setActiveTab('positions')}
        >
          <Briefcase size={18} />
        </button>
        <button 
          className={`mobile-nav-item ${activeTab === 'home' ? 'active' : ''}`}
          onClick={() => setActiveTab('home')}
        >
          <HomeIcon size={18} />
        </button>
        <button 
          className={`mobile-nav-item ${activeTab === 'strategy' ? 'active' : ''}`}
          onClick={() => setActiveTab('strategy')}
        >
          <TrendingUp size={18} />
        </button>
      </nav>

      {/* ================= RIGHT PANEL DRAWER ================= */}
      {(activePanel === 'journal' || activePanel === 'logs' || activePanel === 'settings') && (
        <div className="right-panel-drawer">
          <div className="right-panel-inner">

            {/* JOURNAL PANEL */}
            {activePanel === 'journal' && (
              <>
                <div className="right-panel-header">
                  <span className="right-panel-title">
                    <FileText size={16} className="brand-accent" /> Trade Journal
                  </span>
                  <button className="header-btn" onClick={() => setActivePanel(null)} title="Close">
                    <X size={16} />
                  </button>
                </div>

                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', padding: '12px 0', borderBottom: '1px solid var(--border-color)', alignItems: 'center' }}>
                  <div className="filter-select-wrapper" style={{ flex: '1 1 auto', minWidth: '100px' }}>
                    <select className="filter-select" style={{ width: '100%' }} value={journalCoinFilter} onChange={(e) => setJournalCoinFilter(e.target.value)}>
                      <option value="All">All Coins</option>
                      <option value="BTC">BTC</option>
                      <option value="ETH">ETH</option>
                      <option value="SOL">SOL</option>
                    </select>
                  </div>
                  <div className="filter-select-wrapper" style={{ flex: '1 1 auto', minWidth: '100px' }}>
                    <select className="filter-select" style={{ width: '100%' }} value={journalDirFilter} onChange={(e) => setJournalDirFilter(e.target.value)}>
                      <option value="All">All Dirs</option>
                      <option value="LONG">LONG</option>
                      <option value="SHORT">SHORT</option>
                    </select>
                  </div>
                  <div className="filter-select-wrapper" style={{ flex: '1 1 auto', minWidth: '100px' }}>
                    <select className="filter-select" style={{ width: '100%' }} value={journalStatusFilter} onChange={(e) => setJournalStatusFilter(e.target.value)}>
                      <option value="All">All Statuses</option>
                      <option value="open">Open</option>
                      <option value="closed">Closed</option>
                      <option value="cancelled">Cancelled</option>
                    </select>
                  </div>
                  <button className="btn" style={{ padding: '5px 10px', fontSize: '11px', flexShrink: 0 }} onClick={fetchJournalTrades}>
                    <RefreshCw size={11} /> Sync
                  </button>
                </div>

                <div style={{ flex: 1, overflowY: 'auto', padding: '12px 0' }}>
                  {filteredJournalTrades.length === 0 ? (
                    <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
                      No journal records found. Drag and fill orders in Strategy Hub to populate journal database.
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {filteredJournalTrades.map((t, idx) => {
                        const isProfit = t.realized_pnl_usd >= 0;
                        return (
                          <div key={idx} style={{
                            background: 'rgba(255,255,255,0.02)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '8px',
                            padding: '10px 12px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px',
                            fontSize: '12px'
                          }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: '700' }}>
                                {renderCoinIcon(t.coin, 12)} {t.coin}
                                <span className={`tag ${t.direction === 'LONG' ? 'tag-long' : 'tag-short'}`} style={{ padding: '2px 4px', fontSize: '9px' }}>
                                  {t.direction}
                                </span>
                              </div>
                              <span style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>{t.session_type}</span>
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                              <div><span style={{ color: 'var(--text-secondary)', fontSize: '10px' }}>Entry</span><br />${t.entry_price?.toLocaleString()}</div>
                              <div style={{ textAlign: 'right' }}><span style={{ color: 'var(--text-secondary)', fontSize: '10px' }}>R:R</span><br />{t.planned_rr}x</div>
                              <div><span style={{ color: 'var(--text-secondary)', fontSize: '10px' }}>Size</span><br />${t.position_size_usd?.toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
                              <div style={{ textAlign: 'right' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '10px' }}>PNL</span><br />
                                <span style={{ fontWeight: '700', color: t.status === 'open' ? 'inherit' : (isProfit ? 'var(--accent-green)' : 'var(--accent-red)') }}>
                                  {t.status === 'open' ? <span style={{ color: 'var(--accent-cyan)' }}>OPEN</span> : `${isProfit ? '+' : ''}$${t.realized_pnl_usd?.toFixed(2)}`}
                                </span>
                              </div>
                            </div>
                            {t.open_time && (
                              <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                                {new Date(t.open_time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </>
            )}

            {/* LOGS PANEL */}
            {activePanel === 'logs' && (
              <>
                <div className="right-panel-header">
                  <span className="right-panel-title">
                    <TerminalIcon size={16} className="brand-accent" /> Backend Logs
                  </span>
                  <button className="header-btn" onClick={() => setActivePanel(null)} title="Close">
                    <X size={16} />
                  </button>
                </div>

                {/* Feed Status Row */}
                <div style={{ display: 'flex', gap: '6px', padding: '10px 0', borderBottom: '1px solid var(--border-color)', alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '10px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Feed Status:</span>
                  {['BTC', 'ETH', 'SOL'].map(coin => {
                    const lastUpdate = lastIndicatorUpdate[coin];
                    let status = 'red';
                    if (backendConnected && lastUpdate) {
                      const elapsed = Date.now() - lastUpdate;
                      status = elapsed < 120000 ? 'green' : 'orange';
                    }
                    const colorMap = { green: '#00ff66', orange: '#ffaa00', red: '#ff3333' };
                    return (
                      <div key={coin} style={{
                        display: 'flex', alignItems: 'center', gap: '5px',
                        background: 'rgba(255,255,255,0.03)', padding: '3px 8px',
                        borderRadius: '4px', border: '1px solid var(--border-color)'
                      }}>
                        <span style={{
                          width: '6px', height: '6px', borderRadius: '50%',
                          backgroundColor: colorMap[status],
                          boxShadow: `0 0 5px ${colorMap[status]}`,
                          flexShrink: 0
                        }} />
                        <span style={{ fontSize: '10px', fontWeight: '700', color: 'var(--text-primary)' }}>{coin}</span>
                      </div>
                    );
                  })}
                </div>

                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', padding: '10px 0', borderBottom: '1px solid var(--border-color)', alignItems: 'center' }}>
                  <Calendar size={13} className="brand-accent" style={{ flexShrink: 0 }} />
                  <input
                    type="date"
                    className="form-input"
                    style={{ padding: '4px 8px', fontSize: '12px', flex: 1, minWidth: '120px' }}
                    value={logsDate}
                    onChange={(e) => setLogsDate(e.target.value)}
                  />
                  <button className="btn" style={{ padding: '4px 10px', fontSize: '11px', flexShrink: 0 }} onClick={fetchSystemLogs}>
                    <RefreshCw size={11} /> Refresh
                  </button>
                </div>

                <div style={{ flex: 1, overflowY: 'auto', padding: '12px 0' }}>
                  {systemLogs.length === 0 ? (
                    <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'center' }}>
                      <AlertCircle size={24} className="brand-accent" />
                      <span style={{ fontSize: '13px' }}>No logs for {logsDate}.</span>
                    </div>
                  ) : (
                    <div style={{
                      backgroundColor: 'rgba(0,0,0,0.2)',
                      borderRadius: '8px',
                      border: '1px solid var(--border-color)',
                      padding: '10px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '4px',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '10px'
                    }}>
                      {systemLogs.map((lg, i) => {
                        let color = 'var(--text-primary)';
                        if (lg.level === 'WARN') color = '#ffa500';
                        if (lg.level === 'ERROR') color = '#ff3333';
                        return (
                          <div key={i} style={{ display: 'flex', gap: '6px', borderBottom: '1px solid rgba(255,255,255,0.02)', padding: '3px 0', flexWrap: 'wrap' }}>
                            <span style={{ color: 'var(--accent-cyan)', flexShrink: 0 }}>[{lg.uk_time}]</span>
                            <span style={{ color, fontWeight: '700', flexShrink: 0 }}>[{lg.level}]</span>
                            <span style={{ color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{lg.message}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </>
            )}

            {/* SETTINGS PANEL */}
            {activePanel === 'settings' && (
              <>
                <div className="right-panel-header">
                  <span className="right-panel-title">
                    <SettingsIcon size={16} className="brand-accent" /> Settings
                  </span>
                  <button className="header-btn" onClick={() => setActivePanel(null)} title="Close">
                    <X size={16} />
                  </button>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, overflowY: 'auto', padding: '12px 0' }}>
                  <div className="form-group">
                    <label className="form-label">Backend Endpoint URL</label>
                    <input
                      type="text"
                      placeholder="http://localhost:8000"
                      className="form-input"
                      value={backendUrl}
                      onChange={(e) => setBackendUrl(e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Backend Password</label>
                    <input
                      type="password"
                      placeholder="yellow"
                      className="form-input"
                      value={backendPassword}
                      onChange={(e) => setBackendPassword(e.target.value)}
                    />
                  </div>

                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    The endpoint and password are saved in localStorage. Accounts and Cloudflare Worker URL are loaded from the authenticated backend.
                  </div>

                  <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    <div>
                      <div className="form-label" style={{ marginBottom: '6px' }}>Account configuration</div>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                        Edit accounts on the backend without SSH or restart. Changes are validated and hot-reloaded into the execution engine. Do not remove accounts that have active managed orders.
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      <button className="btn" style={{ padding: '4px 10px', fontSize: '11px' }} onClick={loadAccountsConfigDraft} disabled={accountsConfigBusy}>
                        Load from backend
                      </button>
                      <button className="btn" style={{ padding: '4px 10px', fontSize: '11px' }} onClick={reloadAccountsConfigFromDisk} disabled={accountsConfigBusy}>
                        Reload from disk
                      </button>
                    </div>

                    <textarea
                      className="form-input"
                      style={{ minHeight: '220px', fontFamily: 'var(--font-mono)', fontSize: '11px', resize: 'vertical' }}
                      placeholder='{"accounts":[...]}'
                      value={accountsConfigDraft}
                      onChange={(e) => setAccountsConfigDraft(e.target.value)}
                      spellCheck={false}
                    />

                    <button
                      className="btn btn-cyan"
                      onClick={applyAccountsConfigDraft}
                      disabled={accountsConfigBusy || !accountsConfigDraft.trim()}
                    >
                      {accountsConfigBusy ? 'Applying...' : 'Apply to backend'}
                    </button>

                    {accountsConfigStatus && (
                      <div style={{
                        fontSize: '12px',
                        lineHeight: 1.5,
                        color: accountsConfigStatus.type === 'error'
                          ? '#ff6666'
                          : accountsConfigStatus.type === 'success'
                            ? '#66ff99'
                            : 'var(--text-secondary)',
                      }}>
                        {accountsConfigStatus.message}
                      </div>
                    )}
                  </div>

                  <button className="btn btn-cyan" onClick={() => setActivePanel(null)}>
                     Save &amp; Close
                   </button>
                </div>
              </>
            )}

          </div>
        </div>
      )}

      {/* Main View Container */}
      <main className={`main-content${(activePanel === 'journal' || activePanel === 'logs' || activePanel === 'settings') ? ' main-content--panel-open' : ''}`}>
        
        {/* Dashboard Stat Overview (Always Visible at Top of main screens) */}
        {activeTab === 'home' && (
          <div className="dashboard-summary">
            <div className="stat-card">
              <span className="stat-label">Selected Balance</span>
              <span className={`stat-value ${selectedBalance > 0 ? 'cyan' : ''}`}>
                ${selectedBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Net Unr. PNL</span>
              <span className={`stat-value ${netUnrealizedPnl >= 0 ? 'green' : 'red'}`}>
                {netUnrealizedPnl >= 0 ? '+' : ''}{netUnrealizedPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Open Positions</span>
              <span className="stat-value cyan">{activePositionsCount}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Active Orders</span>
              <span className="stat-value cyan">{activeLimitsCount}</span>
            </div>
          </div>
        )}

        {/* 1. HUB VIEW — slide container */}
        {activeTab === 'positions' && (
          <div className="hub-slide-container">
            {/* Sliding track — blank page first, positions & limits second */}
            <div className={`hub-slide-track ${hubScreen === 'positions' ? 'hub-slided' : ''}`}>
              {/* Blank screen (first page — left) — full-screen chart */}
              <div className="hub-slide-page hub-blank-page">
                <div className="hub-blank-content hub-chart-content">
                  {/* Chart fills all available space */}
                  <TradingChart
                    activeCoin={activeCoin}
                    onActiveCoinChange={setActiveCoin}
                    cfWorkerUrl={cfWorkerUrl}
                  />
                  {/* Full-height right sidebar strip — navigates to positions page */}
                  <div className="pl-vertical-toggle hub-right-toggle">
                    <button
                      className="pl-toggle-btn hub-arrow-btn"
                      onClick={() => setHubScreen('positions')}
                      title="Show positions and limits"
                    >
                      ›
                    </button>
                  </div>
                </div>
              </div>

              {/* Main screen (positions & limits) — second page (right) */}
              <div className="pl-unified-layout hub-slide-page">
                {/* Left arrow button — on the far left of positions page */}
                <div className="pl-vertical-toggle hub-left-toggle">
                  <button
                    className="pl-toggle-btn hub-arrow-btn"
                    onClick={() => setHubScreen('blank')}
                    title="Back to blank screen"
                  >
                    ‹
                  </button>
                </div>

                <div className="pl-main-content">
                  {/* Filters Bar */}
                  <div className="filter-bar">
                    <div className="filter-select-wrapper">
                      <select
                        className="filter-select"
                        value={exchangeFilter}
                        onChange={(e) => setExchangeFilter(e.target.value)}
                      >
                        <option value="All">All Venues</option>
                        {accounts.map(acc => (
                          <option key={acc.id} value={acc.id}>{acc.name}</option>
                        ))}
                      </select>
                    </div>

                    <div className="filter-select-wrapper">
                      <select
                        className="filter-select"
                        value={coinFilter}
                        onChange={(e) => setCoinFilter(e.target.value)}
                      >
                        <option value="All">All</option>
                        <option value="BTC">BTC</option>
                        <option value="ETH">ETH</option>
                        <option value="SOL">SOL</option>
                      </select>
                    </div>

                    <div className="filter-group">
                      <button
                        className={`filter-btn ${sideFilter === 'All' ? 'active' : ''}`}
                        onClick={() => setSideFilter('All')}
                      >
                        All
                      </button>
                      {plSubView === 'positions' ? (
                        <>
                          <button
                            className={`filter-btn ${sideFilter === 'LONG' ? 'active' : ''}`}
                            onClick={() => setSideFilter('LONG')}
                          >
                            Long
                          </button>
                          <button
                            className={`filter-btn ${sideFilter === 'SHORT' ? 'active' : ''}`}
                            onClick={() => setSideFilter('SHORT')}
                          >
                            Short
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            className={`filter-btn ${sideFilter === 'LONG' ? 'active' : ''}`}
                            onClick={() => setSideFilter('LONG')}
                          >
                            Buy
                          </button>
                          <button
                            className={`filter-btn ${sideFilter === 'SHORT' ? 'active' : ''}`}
                            onClick={() => setSideFilter('SHORT')}
                          >
                            Sell
                          </button>
                        </>
                      )}
                    </div>

                    {plSubView === 'limits' && filteredLimits.length > 0 && (
                      <button
                        className="cancel-all-btn"
                        onClick={handleCancelAll}
                      >
                        Cancel All
                      </button>
                    )}
                  </div>

                  {/* POSITIONS SUB-VIEW */}
                  {plSubView === 'positions' && (
                    <>
                      {positionsRefreshState && (
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          gap: '12px',
                          marginBottom: '12px',
                          padding: '10px 14px',
                          borderRadius: '10px',
                          border: '1px solid var(--border-color)',
                          background: 'rgba(255,255,255,0.02)',
                          fontSize: '12px'
                        }}>
                          <span style={{ color: 'var(--text-secondary)' }}>Positions refresh</span>
                          <span className={`status-dot-badge ${positionsRefreshState.tone}`}>
                            {positionsRefreshState.label}
                          </span>
                        </div>
                      )}
                      {positionsError && (
                        <div style={{
                          marginBottom: '12px',
                          padding: '12px 14px',
                          borderRadius: '10px',
                          border: '1px solid rgba(255, 87, 87, 0.35)',
                          background: 'rgba(255, 87, 87, 0.08)',
                          color: 'var(--text-primary)',
                          fontSize: '12px',
                          lineHeight: '1.5'
                        }}>
                          <strong style={{ color: 'var(--accent-red)' }}>Positions fetch issue:</strong> {positionsError}
                        </div>
                      )}
                      {filteredPositions.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '48px', color: 'var(--text-secondary)' }}>
                          {positionsLoading ? 'Loading positions...' : 'No active positions match your criteria.'}
                        </div>
                      ) : (
                        <div className="cards-grid">
                          {filteredPositions.map(pos => (
                            <div
                              key={pos.id}
                              className={`trading-card ${pos.side.toLowerCase()}`}
                            >
                              <div className="card-header">
                                <div className="card-title-sec">
                                  {renderCoinIcon(pos.baseSymbol)}
                                  <span className="token-symbol">
                                    {pos.symbol}
                                    <span className={`tag ${pos.side === 'LONG' ? 'tag-long' : 'tag-short'}`}>
                                      {pos.side}
                                    </span>
                                    {pos.isDemo && <span className="tag tag-demo">DEMO</span>}
                                  </span>
                                </div>
                                <div className="card-pnl">
                                  <div className={`pnl-val ${pos.pnl >= 0 ? 'green' : 'red'}`}>
                                    {pos.pnl >= 0 ? '+' : ''}{pos.pnl.toFixed(2)}
                                  </div>
                                  <div className={`pnl-pct ${pos.pnl >= 0 ? 'green' : 'red'}`}>
                                    {pos.pnl >= 0 ? '+' : ''}{pos.pnlPct.toFixed(1)}%
                                  </div>
                                </div>
                              </div>

                              <div className="card-body-row">
                                <div className="data-item">
                                  <span className="data-label">Size</span>
                                  <span className="data-value">{pos.size}</span>
                                </div>
                                <div className="data-item" style={{ textAlign: 'right' }}>
                                  <span className="data-label">Value</span>
                                  <span className="data-value">{pos.value}</span>
                                </div>
                              </div>

                              <div className="card-body-row">
                                <div className="data-item">
                                  <span className="data-label">Entry</span>
                                  <span className="data-value">{pos.entry.toLocaleString()}</span>
                                </div>
                                <div className="data-item" style={{ textAlign: 'right' }}>
                                  <span className="data-label">Mark</span>
                                  <span className="data-value">{pos.mark.toLocaleString()}</span>
                                </div>
                              </div>

                              <div className="card-body-row">
                                <div className="data-item">
                                  <span className="data-label">Leverage</span>
                                  <span className="data-value" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    {renderExchangeIcon(pos.exchange, 14)}
                                    {pos.leverage}
                                  </span>
                                </div>
                                <div className="data-item" style={{ textAlign: 'right' }}>
                                  <span className="data-label">TP/SL</span>
                                  <span className="data-value" style={{ fontSize: '11px', color: 'var(--accent-cyan)' }}>
                                    {pos.tp || pos.sl ? (
                                      <span>
                                        {pos.tp ? `TP: ${pos.tp}` : ''} {pos.sl ? `SL: ${pos.sl}` : ''}
                                      </span>
                                    ) : (
                                      'None Set'
                                    )}
                                  </span>
                                </div>
                              </div>

                              <div className="card-actions">
                                <button className="btn" onClick={() => openTpSl(pos)}>
                                  TP/SL
                                </button>
                                <button
                                  className="btn btn-red"
                                  onClick={() => handleClosePosition(pos)}
                                >
                                  Close
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="end-label">End of Positions</div>
                    </>
                  )}

                  {/* LIMITS SUB-VIEW */}
                  {plSubView === 'limits' && (
                    <>
                      {limitsRefreshState && (
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          gap: '12px',
                          marginBottom: '12px',
                          padding: '10px 14px',
                          borderRadius: '10px',
                          border: '1px solid var(--border-color)',
                          background: 'rgba(255,255,255,0.02)',
                          fontSize: '12px'
                        }}>
                          <span style={{ color: 'var(--text-secondary)' }}>Orders refresh</span>
                          <span className={`status-dot-badge ${limitsRefreshState.tone}`}>
                            {limitsRefreshState.label}
                          </span>
                        </div>
                      )}
                      {limitsError && (
                        <div style={{
                          marginBottom: '12px',
                          padding: '12px 14px',
                          borderRadius: '10px',
                          border: '1px solid rgba(255, 87, 87, 0.35)',
                          background: 'rgba(255, 87, 87, 0.08)',
                          color: 'var(--text-primary)',
                          fontSize: '12px',
                          lineHeight: '1.5'
                        }}>
                          <strong style={{ color: 'var(--accent-red)' }}>Orders fetch issue:</strong> {limitsError}
                        </div>
                      )}
                      {filteredLimits.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '48px', color: 'var(--text-secondary)' }}>
                          {limitsLoading ? 'Loading orders...' : 'No active orders dragging or standing.'}
                        </div>
                      ) : (
                        <div className="cards-grid">
                          {filteredLimits.map(lim => (
                            <div
                              key={lim.id}
                              className={`trading-card ${lim.side.toLowerCase()}`}
                            >
                              <div className="card-header">
                                <div className="card-title-sec">
                                  {renderCoinIcon(lim.baseSymbol)}
                                  <span className="token-symbol">
                                    {lim.symbol}
                                    <span className={`tag ${lim.side === 'LONG' ? 'tag-long' : 'tag-short'}`}>
                                      {lim.side === 'LONG' ? 'LONG / BUY' : 'SHORT / SELL'}
                                    </span>
                                  </span>
                                </div>
                                <div className="card-pnl" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                  {renderExchangeIcon(lim.exchange, 14)}
                                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                                    {formatExchangeName(lim.exchange)}
                                  </span>
                                </div>
                              </div>

                              <div className="card-body-row">
                                <div className="data-item">
                                  <span className="data-label">Price</span>
                                  <span
                                    className="data-value"
                                    style={{
                                      fontSize: '15px',
                                      color: lim.side === 'LONG' ? 'var(--accent-green)' : 'var(--accent-red)'
                                    }}
                                  >
                                    {lim.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
                                  </span>
                                </div>
                                <div className="data-item" style={{ textAlign: 'right' }}>
                                  <span className="data-label">Total Value</span>
                                  <span className="data-value" style={{ fontSize: '14px' }}>
                                    {lim.totalValue.toLocaleString(undefined, { minimumFractionDigits: 2 })} USDT
                                  </span>
                                </div>
                              </div>

                              <div className="card-body-row">
                                <div className="data-item">
                                  <span className="data-label">Amount</span>
                                  <span className="data-value">{lim.amount.toFixed(4)} {lim.baseSymbol}</span>
                                </div>
                                <div className="data-item" style={{ textAlign: 'right' }}>
                                  <span className="data-label">Order Type</span>
                                  <span className="data-value" style={{ fontSize: '11px' }}>{lim.type}</span>
                                </div>
                              </div>

                              {/* Progress Fill Bar */}
                              <div className="progress-container">
                                <div className="progress-header">
                                  <span className="data-label">Status</span>
                                  <span className="data-value" style={{ fontSize: '11px', color: 'var(--accent-cyan)' }}>
                                    {lim.statusText}
                                  </span>
                                </div>
                                <div className="progress-bar-bg">
                                  <div
                                    className={`progress-bar-fill ${lim.side.toLowerCase()}`}
                                    style={{ width: `${lim.filled}%` }}
                                  />
                                </div>
                              </div>

                              <div className="card-actions">
                                <button className="btn" onClick={() => openModifyLimit(lim)}>
                                  Modify
                                </button>
                                <button
                                  className="btn btn-red"
                                  onClick={() => handleCancelLimit(lim)}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="end-label">End of Limits</div>
                    </>
                  )}
                </div>

                {/* Vertical P/L toggle bar on the right */}
                {hubScreen === 'positions' && (
                  <div className="pl-vertical-toggle">
                    <button
                      className={`pl-toggle-btn ${plSubView === 'positions' ? 'active' : ''}`}
                      onClick={() => setPlSubView('positions')}
                      title="Positions"
                    >
                      P
                    </button>
                    <button
                      className={`pl-toggle-btn ${plSubView === 'limits' ? 'active' : ''}`}
                      onClick={() => setPlSubView('limits')}
                      title="Limits"
                    >
                      L
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* 3. HOME VIEW */}
        {activeTab === 'home' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <div className="home-local-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="mobile-page-title">Home</span>
            </div>
            
            {/* Backend account status */}
            <div className="trading-card" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div className="card-header" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '10px' }}>
                <span className="token-symbol" style={{ fontSize: '16px' }}>
                  <Wallet size={18} className="brand-accent" /> Backend Accounts
                </span>
              </div>

              {accounts.length === 0 ? (
                <div style={{ padding: '16px', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '12px', alignItems: 'center' }}>
                  <Info size={28} style={{ color: 'var(--text-muted)' }} />
                  <p style={{ color: 'var(--text-secondary)', maxWidth: '350px', fontSize: '13px' }}>
                    No accounts loaded from the backend. Check the endpoint/password in Settings and confirm backend/config/accounts.json exists.
                  </p>
                  <button className="btn btn-cyan" style={{ padding: '8px 16px' }} onClick={() => setActivePanel('settings')}>
                     Configure backend
                    </button>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                    Currently loaded accounts ({accounts.length}):
                  </p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {accounts.map((acc, idx) => {
                      const isSelected = selectedHomeAccounts.includes(acc.id);
                      return (
                        <div 
                          key={idx}
                          role="button"
                          tabIndex={0}
                          onClick={() => handleToggleHomeAccount(acc.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              handleToggleHomeAccount(acc.id);
                            }
                          }}
                          style={{ 
                            padding: '10px 12px', 
                            borderRadius: '8px', 
                            border: isSelected ? '1px solid var(--accent-cyan)' : '1px solid var(--border-color)',
                            backgroundColor: isSelected ? 'rgba(0, 229, 255, 0.04)' : 'var(--bg-main)',
                            boxShadow: isSelected ? '0 0 12px rgba(0, 229, 255, 0.12)' : 'none',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            cursor: 'pointer',
                            transition: 'all 0.2s ease',
                          }}
                        >
                          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            {renderExchangeIcon(acc.exchange)}
                            <div>
                              <div style={{ fontWeight: '600', fontSize: '13px' }}>{acc.name}</div>
                              <div style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>
                                Risk: {acc.risk?.mode || acc.risk_config?.type} | Margin: {acc.margin_pct ?? acc.margin?.pct ?? '—'}% | Balance: ${acc.risk_config?.default_balance || 10000.0}
                              </div>
                            </div>
                          </div>
                          <span 
                            className={`tag ${isSelected ? 'tag-long' : 'tag-demo'}`} 
                            style={{ 
                              fontSize: '8px', 
                              color: isSelected ? '#00e5ff' : '#00ff66',
                              border: isSelected ? '1px solid rgba(0, 229, 255, 0.3)' : 'none',
                              padding: '2px 6px',
                              borderRadius: '4px'
                            }}
                          >
                            {isSelected ? 'Selected' : 'Active'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}



        {/* 5. STRATEGY HUB VIEW */}
        {activeTab === 'strategy' && (
          selectedStrategyAccount ? (
            <div className="pl-unified-layout" style={{ minHeight: 'auto', height: '100%' }}>
              <div className="pl-main-content" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', padding: '4px 0' }}>
                  <button 
                    type="button"
                    className="btn"
                    onClick={() => setSelectedStrategyAccount(null)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '8px 12px',
                      textTransform: 'uppercase',
                      fontSize: '11px',
                      letterSpacing: '0.5px'
                    }}
                  >
                    <ArrowLeft size={14} /> Back
                  </button>
                </div>

                {strategySubView === 'intent' ? (
                  <form onSubmit={handlePostIntent} className="trading-card strategy-intent-form" style={{ margin: '0', maxWidth: '100%', padding: '24px', gap: '20px' }}>
                    <div style={{ fontWeight: '700', fontSize: '16px', color: 'var(--accent-cyan)', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span>Intent Control: {selectedStrategyAccount.name}</span>
                      <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--text-secondary)' }}>{selectedStrategyAccount.id}</span>
                    </div>

                    {/* Select Asset */}
                    <div className="form-group" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <label className="form-label" style={{ fontSize: '11px', fontWeight: '600', textTransform: 'uppercase', color: 'var(--text-secondary)' }}>Asset</label>
                      <div style={{ display: 'flex', gap: '12px' }}>
                        {['BTC', 'ETH', 'SOL'].map(coin => (
                          <button
                            type="button"
                            key={coin}
                            className={`filter-btn ${intentCoin === coin ? 'active' : ''}`}
                            onClick={() => setIntentCoin(coin)}
                            style={{
                              flex: 1,
                              padding: '10px 0',
                              fontSize: '13px',
                              borderRadius: '6px',
                              border: intentCoin === coin ? '1px solid var(--accent-cyan)' : '1px solid var(--border-color)',
                              background: intentCoin === coin ? 'rgba(0, 229, 255, 0.08)' : 'transparent',
                              color: intentCoin === coin ? 'var(--accent-cyan)' : 'var(--text-secondary)',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              gap: '6px',
                              transition: 'all 0.2s ease'
                            }}
                          >
                            {renderCoinIcon(coin, 14)} {coin}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Select Direction */}
                    <div className="form-group" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <label className="form-label" style={{ fontSize: '11px', fontWeight: '600', textTransform: 'uppercase', color: 'var(--text-secondary)' }}>Direction</label>
                      <div style={{ display: 'flex', gap: '12px' }}>
                        <button
                          type="button"
                          className={`btn ${intentDirection === 'LONG' ? 'btn-cyan' : ''}`}
                          onClick={() => setIntentDirection('LONG')}
                          style={{
                            flex: 1,
                            padding: '10px 0',
                            borderRadius: '6px',
                            border: intentDirection === 'LONG' ? '1px solid var(--accent-cyan)' : '1px solid var(--border-color)',
                            background: intentDirection === 'LONG' ? 'rgba(0, 229, 255, 0.12)' : 'transparent',
                            color: intentDirection === 'LONG' ? 'var(--accent-cyan)' : 'var(--text-secondary)',
                            fontWeight: '600',
                            transition: 'all 0.2s ease'
                          }}
                        >
                          Buy / LONG
                        </button>
                        <button
                          type="button"
                          className={`btn ${intentDirection === 'SHORT' ? 'btn-red' : ''}`}
                          onClick={() => setIntentDirection('SHORT')}
                          style={{
                            flex: 1,
                            padding: '10px 0',
                            borderRadius: '6px',
                            border: intentDirection === 'SHORT' ? '1px solid var(--accent-red)' : '1px solid var(--border-color)',
                            background: intentDirection === 'SHORT' ? 'rgba(255, 51, 51, 0.12)' : 'transparent',
                            color: intentDirection === 'SHORT' ? 'var(--accent-red)' : 'var(--text-secondary)',
                            fontWeight: '600',
                            transition: 'all 0.2s ease'
                          }}
                        >
                          Sell / SHORT
                        </button>
                      </div>
                    </div>

                    {renderIntentRRBlock()}
                    {renderIntentRiskBlock()}

                    {/* Live calculation previews */}
                    {indicators[intentCoin] && indicators[intentCoin].price > 0 && (
                      <div className="execution-preview-card" style={{
                        padding: '14px',
                        borderRadius: '8px',
                        backgroundColor: 'rgba(255, 255, 255, 0.01)',
                        border: '1px solid var(--border-color)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px',
                        fontSize: '12px'
                      }}>
                        <div style={{ fontWeight: '700', fontSize: '11px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>
                          Calculated Trailing Execution Levels:
                        </div>
                        <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>Drag Limit Price (EVMA):</span>
                          <span style={{ fontWeight: '700', color: 'var(--text-primary)' }}>${indicators[intentCoin].evma.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                        </div>
                        <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>Trailing Dynamic Stop Loss:</span>
                          <span style={{ fontWeight: '700', color: 'var(--accent-red)' }}>
                            ${(intentDirection === 'LONG' ? indicators[intentCoin].lower_stop : indicators[intentCoin].upper_stop).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                        </div>
                        <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>Take Profit (R:R {effectiveIntentRR}x):</span>
                          <span style={{ fontWeight: '700', color: 'var(--accent-green)' }}>
                            ${(intentDirection === 'LONG' 
                              ? indicators[intentCoin].evma + (Math.abs(indicators[intentCoin].evma - indicators[intentCoin].lower_stop) * effectiveIntentRR)
                              : indicators[intentCoin].evma - (Math.abs(indicators[intentCoin].evma - indicators[intentCoin].upper_stop) * effectiveIntentRR)
                            ).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                        </div>
                      </div>
                    )}

                    <button 
                      type="submit" 
                      className="btn btn-cyan" 
                      style={{ padding: '12px 24px', fontSize: '14px', fontWeight: '700', borderRadius: '6px', cursor: 'pointer' }}
                      disabled={
                        !selectedStrategyAccount.enabled
                        || (sessionStatus && !sessionStatus.is_accepting_intents)
                        || limitReached
                        || hasActiveForCoin
                      }
                    >
                      Place Limit Drag Order
                    </button>

                    {!selectedStrategyAccount.enabled && (
                      <div className="intent-form-notice intent-form-notice--disabled" style={{ marginTop: '4px' }}>
                        This account is disabled in configuration. Enable it before submitting intents.
                      </div>
                    )}
                    
                    {sessionStatus && !sessionStatus.is_accepting_intents && (
                      <div className="intent-form-notice intent-form-notice--session" style={{ marginTop: '4px' }}>
                        Intents closed: placement locked outside UK session boundaries.
                      </div>
                    )}

                    {limitReached && (
                      <div className="intent-form-notice intent-form-notice--disabled" style={{ marginTop: '4px' }}>
                        Limit reached: This account has reached its limit of {maxPositionsCap} active position(s).
                      </div>
                    )}

                    {hasActiveForCoin && !limitReached && (
                      <div className="intent-form-notice intent-form-notice--disabled" style={{ marginTop: '4px' }}>
                        Asset active: Already have an active trade/position for {intentCoin}.
                      </div>
                    )}
                  </form>
                ) : strategySubView === 'open' ? (
                  <div className="trading-card strategy-monitoring-panel" style={{ margin: '0', maxWidth: '100%', padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    <div style={{ fontWeight: '700', fontSize: '16px', color: 'var(--accent-cyan)', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span>Open Executions: {selectedStrategyAccount.name}</span>
                      <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--text-secondary)' }}>{selectedStrategyAccount.id}</span>
                    </div>

                    {activeOrders.filter(o => o.account_id === selectedStrategyAccount.id).length === 0 ? (
                      <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                        No open executions for this account.
                      </div>
                    ) : (
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '16px' }}>
                        {activeOrders.filter(o => o.account_id === selectedStrategyAccount.id).map(o => (
                          <div key={o.order_id} style={{
                            padding: '16px',
                            borderRadius: '8px',
                            border: '1px solid var(--border-color)',
                            background: 'rgba(255,255,255,0.02)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '12px'
                          }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.04)', paddingBottom: '10px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span style={{ fontWeight: '700', fontSize: '15px' }}>{renderCoinIcon(o.coin, 16)} {o.coin}</span>
                                <span style={{ 
                                  fontSize: '11px', 
                                  fontWeight: '700', 
                                  padding: '2px 6px', 
                                  borderRadius: '4px',
                                  background: o.direction === 'LONG' ? 'rgba(0, 229, 255, 0.1)' : 'rgba(255, 51, 51, 0.1)',
                                  color: o.direction === 'LONG' ? 'var(--accent-cyan)' : 'var(--accent-red)'
                                }}>
                                  {o.direction}
                                </span>
                              </div>
                              <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', background: 'rgba(255,255,255,0.05)', padding: '4px 8px', borderRadius: '4px' }}>
                                {o.status.replace('_', ' ')}
                              </div>
                            </div>
                            
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '12px', fontSize: '12px' }}>
                              <div>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '11px', textTransform: 'uppercase' }}>Entry Price</span>
                                <div style={{ fontWeight: '600', marginTop: '4px' }}>${Number(o.current_entry).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                              </div>
                              <div>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '11px', textTransform: 'uppercase' }}>Quantity</span>
                                <div style={{ fontWeight: '600', marginTop: '4px' }}>{Number(o.current_qty).toLocaleString()}</div>
                              </div>
                              <div>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '11px', textTransform: 'uppercase' }}>Stop Loss</span>
                                <div style={{ fontWeight: '600', color: 'var(--accent-red)', marginTop: '4px' }}>${Number(o.current_sl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                              </div>
                              <div>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '11px', textTransform: 'uppercase' }}>Take Profit</span>
                                <div style={{ fontWeight: '600', color: 'var(--accent-green)', marginTop: '4px' }}>${Number(o.current_tp).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                              </div>
                            </div>

                            {o.status === 'filled' && (
                              <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between' }}>
                                <span>Filled At: ${Number(o.fill_price).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                                <span>{new Date(o.fill_time).toLocaleTimeString()}</span>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="trading-card strategy-monitoring-panel" style={{ margin: '0', maxWidth: '100%', padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    <div style={{ fontWeight: '700', fontSize: '16px', color: 'var(--accent-cyan)', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span>Strategy Monitoring: {selectedStrategyAccount.name}</span>
                      <span style={{ fontSize: '11px', fontWeight: '500', color: 'var(--text-secondary)' }}>{selectedStrategyAccount.id}</span>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                      <div style={{ padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'rgba(255,255,255,0.01)' }}>
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '4px' }}>Venue / Exchange</div>
                        <div style={{ fontSize: '14px', fontWeight: '600' }}>{formatExchangeName(selectedStrategyAccount.exchange)}</div>
                      </div>
                      <div style={{ padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'rgba(255,255,255,0.01)' }}>
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '4px' }}>Risk Mode</div>
                        <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--accent-cyan)' }}>{getAccountRiskSummary(selectedStrategyAccount)}</div>
                      </div>
                      <div style={{ padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'rgba(255,255,255,0.01)' }}>
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '4px' }}>Margin Budget</div>
                        <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--accent-cyan)' }}>{getAccountMarginSummary(selectedStrategyAccount)}</div>
                      </div>
                      <div style={{ padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'rgba(255,255,255,0.01)' }}>
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '4px' }}>Max Position Size</div>
                        <div style={{ fontSize: '14px', fontWeight: '600' }}>${Number(selectedStrategyAccount.max_position_size_usd || 0).toLocaleString()}</div>
                      </div>
                      <div style={{ padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'rgba(255,255,255,0.01)' }}>
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '4px' }}>Max Positions</div>
                        <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--accent-cyan)' }}>{selectedStrategyAccount.max_positions ?? 3}</div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '4px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Live Strategy Signal Levels</div>
                      
                      {['BTC', 'ETH', 'SOL'].map(coin => {
                        const ind = indicators[coin] || {};
                        const hasInd = ind.price > 0;
                        const coinFixedRR = getFixedRRForCoin(selectedStrategyAccount, coin) || 2.0;

                        return (
                          <div key={coin} style={{
                            padding: '14px',
                            borderRadius: '8px',
                            border: '1px solid var(--border-color)',
                            background: 'rgba(255,255,255,0.02)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '10px'
                          }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: '700', fontSize: '14px' }}>
                                {renderCoinIcon(coin, 16)} {coin}
                                {renderStatusLight(coin)}
                              </div>
                              {hasInd ? (
                                <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>
                                  ${ind.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                </div>
                              ) : (
                                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Waiting for tick...</div>
                              )}
                            </div>

                            {hasInd && (
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '10px', fontSize: '11px', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '10px' }}>
                                <div>
                                  <span style={{ color: 'var(--text-secondary)' }}>EVMA (12):</span>
                                  <div style={{ fontWeight: '600', fontSize: '12px', marginTop: '2px' }}>
                                    ${ind.evma.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                  </div>
                                </div>
                                <div>
                                  <span style={{ color: 'var(--text-secondary)' }}>Long SL (ATR):</span>
                                  <div style={{ fontWeight: '600', fontSize: '12px', color: 'var(--accent-red)', marginTop: '2px' }}>
                                    ${ind.lower_stop.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                  </div>
                                </div>
                                <div>
                                  <span style={{ color: 'var(--text-secondary)' }}>Short SL (ATR):</span>
                                  <div style={{ fontWeight: '600', fontSize: '12px', color: 'var(--accent-red)', marginTop: '2px' }}>
                                    ${ind.upper_stop.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                  </div>
                                </div>
                                <div>
                                  <span style={{ color: 'var(--text-secondary)' }}>TP R:R:</span>
                                  <div style={{ fontWeight: '600', fontSize: '12px', color: 'var(--accent-green)', marginTop: '2px' }}>
                                    {coinFixedRR}x Target
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* Vertical toggle bar on the right */}
              <div className="pl-vertical-toggle">
                <button
                  className={`pl-toggle-btn ${strategySubView === 'intent' ? 'active' : ''}`}
                  onClick={() => setStrategySubView('intent')}
                  title="Intent Form"
                >
                  I
                </button>
                <button
                  className={`pl-toggle-btn ${strategySubView === 'monitoring' ? 'active' : ''}`}
                  onClick={() => setStrategySubView('monitoring')}
                  title="Monitoring"
                >
                  M
                </button>
                <button
                  className={`pl-toggle-btn ${strategySubView === 'open' ? 'active' : ''}`}
                  onClick={() => setStrategySubView('open')}
                  title="Open Executions"
                >
                  O
                </button>
              </div>
            </div>
          ) : (
            <div className="strategy-hub" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {/* Session Indicator Banner */}
              {sessionStatus && (
                <div className="session-banner" style={{
                  padding: '12px 16px',
                  borderRadius: '8px',
                  border: '1px solid var(--border-color)',
                  backgroundColor: 'rgba(0, 229, 255, 0.03)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '8px'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <TrendingUp size={16} className="brand-accent" />
                    <span style={{ fontSize: '13px', fontWeight: '600' }}>
                      {sessionStatus.active_session ? `Active Session: ${sessionStatus.active_session.type.toUpperCase()}` : 'No Active Session'}
                    </span>
                    <span className={`tag ${sessionStatus.is_accepting_intents ? 'tag-long' : 'tag-short'}`} style={{ fontSize: '10px' }}>
                      {sessionStatus.is_accepting_intents ? 'Accepting Intents' : 'Intents Closed'}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                    Next Session starts: {sessionStatus.next_session ? new Date(sessionStatus.next_session.start_time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : 'N/A'} (UK)
                  </div>
                </div>
              )}

              <div className="strategy-accounts-view">
                {accounts.length === 0 ? (
                  <div className="strategy-empty-state">
                    No accounts loaded. Configure the backend endpoint/password in Settings and ensure{' '}
                    <code>backend/config/accounts.json</code>.
                  </div>
                ) : (
                  <>
                    <div className="cards-grid">
                      {accounts.map((acc) => renderStrategyAccountWidget(acc))}
                    </div>

                    {!selectedStrategyAccount && (
                      <p className="strategy-select-hint">
                        Select an account card to open the intent / decision form.
                      </p>
                    )}

                    {selectedStrategyAccount && (
                      <form onSubmit={handlePostIntent} className="trading-card strategy-intent-form">
                        <div style={{ fontWeight: '700', fontSize: '15px', color: 'var(--accent-cyan)', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px' }}>
                          Intent Execution Control for: {selectedStrategyAccount.name}
                        </div>

                        {/* Select Asset */}
                        <div className="form-group">
                          <label className="form-label">Asset</label>
                          <div style={{ display: 'flex', gap: '8px' }}>
                            {['BTC', 'ETH', 'SOL'].map(coin => (
                              <button
                                type="button"
                                key={coin}
                                className={`filter-btn ${intentCoin === coin ? 'active' : ''}`}
                                onClick={() => setIntentCoin(coin)}
                                style={{ flex: 1, padding: '8px 0', fontSize: '13px' }}
                              >
                                {renderCoinIcon(coin, 14)} {coin}
                              </button>
                            ))}
                          </div>
                        </div>

                        {/* Select Direction */}
                        <div className="form-group">
                          <label className="form-label">Direction</label>
                          <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                              type="button"
                              className={`btn ${intentDirection === 'LONG' ? 'btn-cyan' : ''}`}
                              onClick={() => setIntentDirection('LONG')}
                              style={{ flex: 1, padding: '8px 0' }}
                            >
                              Buy / LONG
                            </button>
                            <button
                              type="button"
                              className={`btn ${intentDirection === 'SHORT' ? 'btn-red' : ''}`}
                              onClick={() => setIntentDirection('SHORT')}
                              style={{ flex: 1, padding: '8px 0' }}
                            >
                              Sell / SHORT
                            </button>
                          </div>
                        </div>

                        {renderIntentRRBlock()}
                        {renderIntentRiskBlock()}

                        {/* Live calculation previews */}
                        {indicators[intentCoin] && indicators[intentCoin].price > 0 && (
                          <div className="execution-preview-card" style={{
                            padding: '12px',
                            borderRadius: '8px',
                            backgroundColor: 'rgba(0, 0, 0, 0.15)',
                            border: '1px solid var(--border-color)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px',
                            fontSize: '12px'
                          }}>
                            <div style={{ fontWeight: '700', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                              Calculated Trailing Execution levels:
                            </div>
                            <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span>Drag Limit Price (EVMA):</span>
                              <span style={{ fontWeight: '700' }}>${indicators[intentCoin].evma.toFixed(2)}</span>
                            </div>
                            <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span>Trailing Dynamic Stop Loss:</span>
                              <span style={{ fontWeight: '700', color: 'var(--accent-red)' }}>
                                ${(intentDirection === 'LONG' ? indicators[intentCoin].lower_stop : indicators[intentCoin].upper_stop).toFixed(2)}
                              </span>
                            </div>
                            <div className="preview-row" style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span>Take Profit (R:R {effectiveIntentRR}x):</span>
                              <span style={{ fontWeight: '700', color: 'var(--accent-green)' }}>
                                ${(intentDirection === 'LONG' 
                                  ? indicators[intentCoin].evma + (Math.abs(indicators[intentCoin].evma - indicators[intentCoin].lower_stop) * effectiveIntentRR)
                                  : indicators[intentCoin].evma - (Math.abs(indicators[intentCoin].evma - indicators[intentCoin].upper_stop) * effectiveIntentRR)
                                ).toFixed(2)}
                              </span>
                            </div>
                          </div>
                        )}

                        <button 
                          type="submit" 
                          className="btn btn-cyan" 
                          style={{ padding: '10px 0', fontSize: '14px', fontWeight: '700' }}
                          disabled={
                            !selectedStrategyAccount.enabled
                            || (sessionStatus && !sessionStatus.is_accepting_intents)
                            || limitReached
                            || hasActiveForCoin
                          }
                        >
                          Place Limit Drag Order
                        </button>

                        {!selectedStrategyAccount.enabled && (
                          <div className="intent-form-notice intent-form-notice--disabled">
                            This account is disabled in configuration. Enable it before submitting intents.
                          </div>
                        )}
                        
                        {sessionStatus && !sessionStatus.is_accepting_intents && (
                          <div className="intent-form-notice intent-form-notice--session">
                            Intents closed: placement locked outside UK session boundaries.
                          </div>
                        )}

                        {limitReached && (
                          <div className="intent-form-notice intent-form-notice--disabled" style={{ marginTop: '4px' }}>
                            Limit reached: This account has reached its limit of {maxPositionsCap} active position(s).
                          </div>
                        )}

                        {hasActiveForCoin && !limitReached && (
                          <div className="intent-form-notice intent-form-notice--disabled" style={{ marginTop: '4px' }}>
                            Asset active: Already have an active trade/position for {intentCoin}.
                          </div>
                        )}
                      </form>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        )}

      </main>

      {/* ================= MODALS SECTION ================= */}

      {/* 1. TP / SL MODIFICATION MODAL */}
      {showTpSlModal && (
        <div className="modal-overlay">
          <div className="modal-content" style={{ maxWidth: '350px' }}>
            <div className="modal-header">
              <span className="modal-title">Modify TP/SL — {showTpSlModal.symbol}</span>
              <button className="header-btn" onClick={() => setShowTpSlModal(null)}>
                <X size={18} />
              </button>
            </div>
            
            <div className="modal-body" style={{ marginTop: '12px' }}>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px' }}>
                Current Mark: {showTpSlModal.mark.toLocaleString()}
              </div>

              <div className="form-group">
                <label className="form-label">Take Profit (TP) Price</label>
                <input 
                  type="number" 
                  step="any"
                  placeholder="No Take Profit Set" 
                  className="form-input"
                  value={tpVal}
                  onChange={(e) => setTpVal(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Stop Loss (SL) Price</label>
                <input 
                  type="number" 
                  step="any"
                  placeholder="No Stop Loss Set" 
                  className="form-input"
                  value={slVal}
                  onChange={(e) => setSlVal(e.target.value)}
                />
              </div>
            </div>

            <div className="card-actions" style={{ marginTop: '16px' }}>
              <button className="btn" onClick={() => setShowTpSlModal(null)}>
                Cancel
              </button>
              <button className="btn btn-cyan" onClick={saveTpSl}>
                Confirm limits
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 3. MODIFY LIMIT ORDER MODAL */}
      {showModifyLimitModal && (
        <div className="modal-overlay">
          <div className="modal-content" style={{ maxWidth: '350px' }}>
            <div className="modal-header">
              <span className="modal-title">Modify Limit — {showModifyLimitModal.symbol}</span>
              <button className="header-btn" onClick={() => setShowModifyLimitModal(null)}>
                <X size={18} />
              </button>
            </div>
            
            <div className="modal-body" style={{ marginTop: '12px' }}>
              <div className="form-group">
                <label className="form-label">Price (USDT)</label>
                <input 
                  type="number" 
                  step="any"
                  className="form-input"
                  value={limitModifyPrice}
                  onChange={(e) => setLimitModifyPrice(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Quantity ({showModifyLimitModal.baseSymbol})</label>
                <input 
                  type="number" 
                  step="any"
                  className="form-input"
                  value={limitModifyQty}
                  onChange={(e) => setLimitModifyQty(e.target.value)}
                />
              </div>
            </div>

            <div className="card-actions" style={{ marginTop: '16px' }}>
              <button className="btn" onClick={() => setShowModifyLimitModal(null)}>
                Cancel
              </button>
              <button className="btn btn-cyan" onClick={saveLimitModify}>
                Modify Order
              </button>
            </div>
          </div>
        </div>
      )}



    </div>
  );
}
