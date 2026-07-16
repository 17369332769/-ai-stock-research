/**
 * 纯展示格式化。
 *
 * 只做「数字/时间 → 字符串」的渲染，不做任何推导：
 * 不算收益、不算概率、不算新鲜度（spec §5.1）。
 */

const SHANGHAI_TIME = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

const SHANGHAI_DATE = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

export const EMPTY_PLACEHOLDER = '—';

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY_PLACEHOLDER;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY_PLACEHOLDER;
  return SHANGHAI_TIME.format(date);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return EMPTY_PLACEHOLDER;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY_PLACEHOLDER;
  return SHANGHAI_DATE.format(date);
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** API 的比率字段（change_percent / expected_return / p20 …）均为小数，如 0.0033 = 0.33%。 */
export function formatRatioAsPercent(
  value: number | null | undefined,
  fractionDigits = 2,
  withSign = true,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  const percent = value * 100;
  const sign = withSign && percent > 0 ? '+' : '';
  return `${sign}${percent.toFixed(fractionDigits)}%`;
}

/** 概率字段（probability_up / direction_accuracy）为 0-1 小数，展示为百分比且不带正负号。 */
export function formatProbability(value: number | null | undefined, fractionDigits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

/** 指标（MAE / Brier）为无量纲小数，原样展示。 */
export function formatMetric(value: number | null | undefined, fractionDigits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  return value.toFixed(fractionDigits);
}

/** 成交量/成交额的紧凑展示；只改变单位，不推导新的业务指标。 */
export function formatCompactNumber(
  value: number | null | undefined,
  unit = '',
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿${unit}`;
  if (absolute >= 10_000) return `${(value / 10_000).toFixed(2)}万${unit}`;
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${unit}`;
}

/** age_seconds 由 API 提供，此处只做可读化，不做"是否过期"的判断。 */
export function formatAgeSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_PLACEHOLDER;
  }
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

/** 涨跌方向仅用于着色，输入已是 API 给的收益率字段。 */
export function changeTone(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) {
    return 'flat';
  }
  return value > 0 ? 'up' : 'down';
}
