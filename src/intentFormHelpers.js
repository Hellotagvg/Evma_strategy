/** Pure helpers for account-driven intent form (risk + R:R). */

export const DEFAULT_TP_RR_OPTIONS = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0];

export const getAccountRiskConfig = (account) => {
  if (!account) return null;
  const raw = account.risk_config || account.risk;
  if (!raw) return null;
  const type = raw.type || raw.mode;
  if (!type) return null;
  return { ...raw, type };
};

export const getFixedRRForCoin = (account, coin) => {
  const fixedMap = account?.fixed_tp_rr_by_coin || {};
  const value = fixedMap[coin];
  return Number.isFinite(Number(value)) ? Number(value) : null;
};

/**
 * @returns {{
 *   mode: 'fixed' | 'adjustable' | 'unknown',
 *   type?: string,
 *   unit?: string,
 *   isPct?: boolean,
 *   value?: number,
 *   min?: number,
 *   max?: number,
 *   step?: number,
 *   defaultValue?: number,
 *   submitValue?: number,
 *   message?: string,
 *   labelHint?: string,
 * }}
 */
export const getRiskFieldState = (account) => {
  const cfg = getAccountRiskConfig(account);
  if (!cfg) return { mode: 'unknown' };

  const type = cfg.type;
  const isPct = type.includes('pct');
  const unit = isPct ? '%' : ' USD';

  if (type === 'fixed_pct' || type === 'fixed_usd') {
    const value = Number(cfg.value ?? 0);
    const message = isPct
      ? `Risk is fixed at ${value}% for this account.`
      : `Risk is fixed at $${value} for this account.`;
    return {
      mode: 'fixed',
      type,
      unit,
      isPct,
      value,
      submitValue: value,
      message,
      min: value,
      max: value,
    };
  }

  if (type === 'adjustable_pct' || type === 'adjustable_usd') {
    const min = Number(cfg.min ?? (isPct ? 0.5 : 10));
    const max = Number(cfg.max ?? (isPct ? 3.0 : 200));
    const step = Number(cfg.step ?? (isPct ? 0.5 : 10));
    const defaultValue = Number(cfg.default ?? min);
    const labelHint = isPct ? 'percentage of balance' : 'USD amount at risk';
    return {
      mode: 'adjustable',
      type,
      unit,
      isPct,
      min,
      max,
      step,
      defaultValue,
      labelHint,
    };
  }

  return { mode: 'unknown', type };
};

export const getIntentRiskSubmitValue = (account, intentRisk) => {
  const state = getRiskFieldState(account);
  if (state.mode === 'fixed') return state.submitValue;
  if (state.mode === 'adjustable') return parseFloat(intentRisk);
  return parseFloat(intentRisk);
};

export const resolveInitialIntentRisk = (account) => {
  const state = getRiskFieldState(account);
  if (state.mode === 'fixed') return state.submitValue;
  if (state.mode === 'adjustable') return state.defaultValue;
  const raw = getAccountRiskConfig(account);
  return raw?.default ?? raw?.value ?? 1.0;
};

export const normalizeTpRrOptions = (options) => {
  const list = Array.isArray(options) ? options.map(Number).filter(Number.isFinite) : [];
  const sorted = [...new Set(list.length ? list : DEFAULT_TP_RR_OPTIONS)].sort((a, b) => a - b);
  return sorted;
};

export const getTpRrSliderMeta = (options) => {
  const sorted = normalizeTpRrOptions(options);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const step = sorted.length > 1 ? sorted[1] - sorted[0] : 0.5;
  return { min, max, step, options: sorted };
};

export const snapToTpRrOption = (value, options) => {
  const sorted = normalizeTpRrOptions(options);
  return sorted.reduce(
    (best, opt) => (Math.abs(opt - value) < Math.abs(best - value) ? opt : best),
    sorted[0]
  );
};
