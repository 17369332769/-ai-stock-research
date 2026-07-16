/**
 * 九种必须状态（spec §13.2）及其判定。
 *
 * 判定输入一律来自 API 字段（freshness / market.phase / job / 错误码），
 * 前端不自行计算新鲜度、收益或概率（spec §5.1）。
 */

import { ApiError, isApiError } from './api/client';
import type {
  ClientErrorCode,
  JobDTO,
  MarketDTO,
  MarketPhase,
  QuoteDTO,
} from './api/types';

/** spec §13.2 规定的九种状态。 */
export const UI_STATES = [
  'initial_backfill', // 首次回补
  'ok', // 正常
  'partial_data', // 部分数据缺失
  'quote_stale', // 行情过期
  'quote_unavailable', // 暂无实时行情
  'provider_failed', // 数据源失败
  'model_unavailable', // 模型不可用
  'no_documents', // 无文档
  'no_prediction', // 无预测
  'market_closed', // 休市
] as const;

export type UiState = (typeof UI_STATES)[number];

export type StateTone = 'neutral' | 'info' | 'warning' | 'danger';

export interface StateDescriptor {
  label: string;
  tone: StateTone;
  description: string;
}

export const STATE_DESCRIPTORS: Record<UiState, StateDescriptor> = {
  initial_backfill: {
    label: '首次回补',
    tone: 'info',
    description: '正在回补历史数据，完成前不显示预测。',
  },
  ok: {
    label: '正常',
    tone: 'neutral',
    description: '数据与模型均可用。',
  },
  partial_data: {
    label: '部分数据缺失',
    tone: 'warning',
    description: '部分数据未取得，相关内容按缺失展示。',
  },
  quote_stale: {
    label: '行情可能已过期',
    tone: 'warning',
    description: '行情已超过 120 秒未更新，不作为实时行情使用。',
  },
  quote_unavailable: {
    label: '暂无实时行情',
    tone: 'neutral',
    description: '当前没有可展示的实时报价；历史行情与实时行情分别展示。',
  },
  provider_failed: {
    label: '数据源失败',
    tone: 'danger',
    description: '上游数据源不可用，已有历史数据仍可查看。',
  },
  model_unavailable: {
    label: '模型不可用',
    tone: 'danger',
    description: '当前没有可用的模型版本，暂不生成预测。',
  },
  no_documents: {
    label: '无文档',
    tone: 'neutral',
    description: '暂无公告或新闻。',
  },
  no_prediction: {
    label: '无预测',
    tone: 'neutral',
    description: '暂无可用预测。',
  },
  market_closed: {
    label: '休市',
    tone: 'info',
    description: '当前非交易时段，实时行情暂停更新。',
  },
};

export function stateLabel(state: UiState): string {
  return STATE_DESCRIPTORS[state].label;
}

// ---------------------------------------------------------------------------
// 错误码 → 状态
// ---------------------------------------------------------------------------

const ERROR_STATE_MAP: Partial<Record<ClientErrorCode, UiState>> = {
  PROVIDER_UNAVAILABLE: 'provider_failed', // 424
  MODEL_UNAVAILABLE: 'model_unavailable', // 503
  INSUFFICIENT_DATA: 'no_prediction', // 422 样本不足
  NETWORK_ERROR: 'provider_failed',
};

/** 把 API 错误映射到 UI 状态；不认识的错误返回 null（由调用方按通用错误展示）。 */
export function mapErrorToState(error: unknown): UiState | null {
  if (!isApiError(error)) return null;
  return ERROR_STATE_MAP[error.code] ?? null;
}

/** 422 在预测语境下表示样本不足，文案需与"暂无预测"区分。 */
export function isInsufficientData(error: unknown): error is ApiError {
  return isApiError(error) && error.code === 'INSUFFICIENT_DATA';
}

// ---------------------------------------------------------------------------
// 行情状态：休市 / 过期 / 正常
// ---------------------------------------------------------------------------

/** 连续竞价时段。只有这两个时段 + freshness=fresh 才允许称"实时"。 */
const CONTINUOUS_TRADING_PHASES: readonly MarketPhase[] = ['morning', 'afternoon'];

export function isMarketClosed(market: MarketDTO | null | undefined): boolean {
  if (!market) return false;
  return market.is_trading_day === false || market.phase === 'closed';
}

export interface QuoteStatus {
  /** 页面级状态：休市时使用最近交易时段语义，不再套用盘中延迟/过期。 */
  states: UiState[];
  /** 价格标签。红线：过期或休市时禁止标"实时"。 */
  priceLabel: string;
  /** 是否允许以实时口径展示。 */
  isRealtime: boolean;
  ageStatus: 'latest' | 'delayed' | 'stale' | 'unavailable';
  delayed: boolean;
  stale: boolean;
  closed: boolean;
  unavailable: boolean;
  availabilityLabel: string;
  ageSeconds: number | null;
}

/**
 * 行情状态判定。
 * 只读 API 的 quote.age_status / quote.data_age_seconds / market.phase，不比较本地时钟。
 * 老接口没有 age_status 时才回退到 freshness，避免同一报价在列表与详情出现不同口径。
 */
export function resolveQuoteStatus(
  quote: QuoteDTO | null | undefined,
  market: MarketDTO | null | undefined,
): QuoteStatus {
  const closed = isMarketClosed(market);
  const unavailable = quote == null;
  const ageStatus = quote == null
    ? 'unavailable'
    : quote.age_status ?? (quote.freshness === 'stale' ? 'stale' : 'latest');
  const stale = !closed && ageStatus === 'stale';
  const delayed = !closed && ageStatus === 'delayed';
  const states: UiState[] = [];

  if (closed) states.push('market_closed');
  if (unavailable) states.push('quote_unavailable');
  if (stale) states.push('quote_stale');

  const inContinuousTrading =
    market != null && CONTINUOUS_TRADING_PHASES.includes(market.phase);

  // 只有 API 明确标记 latest 才敢称实时；delayed 即使兼容字段仍为 fresh 也不能称实时。
  const isRealtime = ageStatus === 'latest' && !closed && (market == null || inContinuousTrading);

  let priceLabel: string;
  if (closed) {
    priceLabel = '最近行情';
  } else if (stale) {
    priceLabel = '最近行情（可能已过期）';
  } else if (isRealtime) {
    priceLabel = '最新价';
  } else {
    priceLabel = '最近行情';
  }

  const availabilityLabel =
    market?.phase === 'pre_open' || market?.phase === 'call_auction'
      ? '盘前，等待首次行情'
      : closed
        ? '非交易时段，暂无实时行情'
        : '暂无实时行情';

  return {
    states,
    priceLabel,
    isRealtime,
    ageStatus,
    delayed,
    stale,
    closed,
    unavailable,
    availabilityLabel,
    ageSeconds: quote?.data_age_seconds ?? quote?.age_seconds ?? null,
  };
}

// ---------------------------------------------------------------------------
// 回补作业状态
// ---------------------------------------------------------------------------

export function isJobRunning(job: JobDTO | null | undefined): boolean {
  if (!job) return false;
  return job.status === 'queued' || job.status === 'running';
}

/**
 * 回补作业 → 状态：
 *  - 进行中 → 首次回补
 *  - 成功但有 warning（如分钟线不可得，spec §7.1）→ 部分数据缺失
 *  - 失败 → 数据源失败
 */
export function resolveJobState(job: JobDTO | null | undefined): UiState | null {
  if (!job) return null;
  if (isJobRunning(job)) return 'initial_backfill';
  if (job.status === 'failed') return 'provider_failed';
  if (job.status === 'succeeded' && (job.warnings?.length ?? 0) > 0) return 'partial_data';
  return null;
}

// ---------------------------------------------------------------------------
// 页面级状态汇总
// ---------------------------------------------------------------------------

export interface PageStateInput {
  job?: JobDTO | null;
  quote?: QuoteDTO | null;
  market?: MarketDTO | null;
  /** API 标注的缺失数据域（spec：部分数据缺失）。 */
  missing?: string[] | null;
  error?: unknown;
}

/**
 * 汇总页面顶部需要展示的状态条。顺序即展示优先级。
 * 休市时行情年龄仍可作为获取时间展示，但不用盘中“延迟/过期”语义。
 */
export function resolvePageStates(input: PageStateInput): UiState[] {
  const states: UiState[] = [];

  const errorState = mapErrorToState(input.error);
  if (errorState) states.push(errorState);

  const jobState = resolveJobState(input.job);
  if (jobState && !states.includes(jobState)) states.push(jobState);

  if ((input.missing?.length ?? 0) > 0 && !states.includes('partial_data')) {
    states.push('partial_data');
  }

  const quoteStatus = resolveQuoteStatus(input.quote, input.market);
  for (const state of quoteStatus.states) {
    if (!states.includes(state)) states.push(state);
  }

  return states;
}
