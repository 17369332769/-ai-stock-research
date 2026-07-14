/**
 * API DTO 类型 —— 逐字对齐 spec §7 契约。
 *
 * 纪律（spec §5.1）：apps/web 只展示，不自行计算收益、概率或数据新鲜度。
 * 因此凡是"判断"性质的字段（freshness / better_than_baseline / confidence /
 * market.phase / in_current_universe）一律由 API 给出，本文件只描述形状。
 */

// ---------------------------------------------------------------------------
// 信封（spec §7）
// ---------------------------------------------------------------------------

export interface PageInfo {
  next_cursor: string | null;
  has_more: boolean;
}

/** 列表响应：{"data":[], "page":{...}, "request_id":"uuid"} */
export interface ListEnvelope<T> {
  data: T[];
  page: PageInfo;
  request_id: string;
}

/** 单对象响应：{"data":{...}, "request_id":"uuid"} */
export interface ItemEnvelope<T> {
  data: T;
  request_id: string;
}

/** 错误响应：{"error":{"code","message","request_id"}} */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
  };
}

// ---------------------------------------------------------------------------
// 错误码（spec §7 错误码表）
// ---------------------------------------------------------------------------

export const ERROR_CODES = [
  'INVALID_ARGUMENT', // 400
  'INSTRUMENT_NOT_FOUND', // 404
  'NOT_CURRENT_UNIVERSE_MEMBER', // 409
  'DUPLICATE_WATCHLIST_ITEM', // 409
  'INSUFFICIENT_DATA', // 422
  'PROVIDER_UNAVAILABLE', // 424
  'MODEL_UNAVAILABLE', // 503
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

/** 传输层/未知错误统一归一到这两个哨兵码，便于 UI 状态映射穷举。 */
export type ClientErrorCode = ErrorCode | 'NETWORK_ERROR' | 'UNKNOWN';

export function isErrorCode(value: string): value is ErrorCode {
  return (ERROR_CODES as readonly string[]).includes(value);
}

// ---------------------------------------------------------------------------
// 行情 / 市场时段
// ---------------------------------------------------------------------------

/** spec §7：行情过期但仍有最后值时返回 200 + freshness=stale + age_seconds。 */
export type Freshness = 'fresh' | 'stale';

/**
 * 市场时段。取值与后端 apps/api/app/core/trading_calendar.py 的 MarketPhase 对齐。
 * 前端禁止用本地时钟推断休市（spec §5.1），只渲染 API 给出的 phase。
 */
export type MarketPhase =
  | 'closed'
  | 'pre_open'
  | 'call_auction'
  | 'morning'
  | 'lunch_break'
  | 'afternoon';

export interface MarketDTO {
  phase: MarketPhase;
  is_trading_day: boolean;
  /** 最新交易日（休市时必须展示，spec §15）。 */
  latest_trading_day: string;
}

/** spec §7.2 quote 对象。 */
export interface QuoteDTO {
  price: number;
  change_percent: number;
  observed_at: string;
  source: string;
  source_url?: string | null;
  freshness: Freshness;
  /** 仅 stale 时由 API 附带（spec §7）。前端不得自行用本地时钟计算。 */
  age_seconds?: number | null;
  previous_close?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  amount?: number | null;
  volume_ratio?: number | null;
}

export interface RelativeStrengthDTO {
  benchmark: string;
  stock_change_percent: number;
  benchmark_change_percent: number;
}

// ---------------------------------------------------------------------------
// 证券 / 自选股（spec §7.1）
// ---------------------------------------------------------------------------

export interface InstrumentDTO {
  symbol: string;
  name: string;
  exchange?: 'SSE' | 'SZSE';
  industry?: string | null;
  listed_at?: string | null;
}

export interface WatchlistItemDTO {
  symbol: string;
  display_order: number;
  name?: string;
  universe_code?: string;
  /** false = 已调出沪深300：保留页面与既有预测，停止新预测（spec §3.1）。 */
  in_current_universe?: boolean;
  universe_exit_at?: string | null;
  quote?: QuoteDTO | null;
  market?: MarketDTO | null;
  /** 首次回补进行中时随行返回。 */
  backfill_job?: JobDTO | null;
  /** 部分数据缺失时，API 标注缺失的数据域。 */
  missing?: string[];
}

// ---------------------------------------------------------------------------
// 作业（spec §7.1）
// ---------------------------------------------------------------------------

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

/** 回补三步固定顺序（spec §7.1）。 */
export const BACKFILL_STEPS = ['daily_bars', 'minute_bars', 'documents'] as const;
export type BackfillStep = (typeof BACKFILL_STEPS)[number];

export interface JobDTO {
  id: string;
  status: JobStatus;
  completed_steps: number;
  total_steps: number;
  current_step: string | null;
  error_code: string | null;
  error_message?: string | null;
  /** 分钟线不可得时记 warning，不使整项回补失败（spec §7.1）→ 部分数据缺失。 */
  warnings?: string[];
  job_type?: string;
  symbol?: string | null;
}

/** POST /watchlist 首次添加返回 202 的 data 形状（spec §7.1）。 */
export interface WatchlistAddResultDTO {
  watchlist_item: { symbol: string; display_order: number };
  backfill_job: JobDTO | null;
}

// ---------------------------------------------------------------------------
// 快照（spec §7.2）
// ---------------------------------------------------------------------------

export interface SnapshotDTO {
  symbol: string;
  name: string;
  quote: QuoteDTO | null;
  relative_strength: RelativeStrengthDTO | null;
  latest_anomaly_analysis_id: string | null;
  latest_predictions: string[];
  market?: MarketDTO | null;
  in_current_universe?: boolean;
  universe_exit_at?: string | null;
  backfill_job?: JobDTO | null;
  missing?: string[];
}

// ---------------------------------------------------------------------------
// 文档与解释（spec §7.3）
// ---------------------------------------------------------------------------

export type DocumentType = 'announcement' | 'news';

export interface DocumentDTO {
  id: string;
  symbol: string | null;
  document_type: DocumentType;
  title: string;
  source: string;
  source_url: string;
  published_at: string;
  observed_at: string;
  body_text?: string | null;
}

/** 证据项：document_id / title / source_url / published_at / quote 均必填（spec §7.3）。 */
export interface EvidenceDTO {
  document_id: string;
  title: string;
  source_url: string;
  published_at: string;
  /** 原文中连续存在的 1-300 字符。 */
  quote: string;
}

export type AnalysisType = 'document' | 'anomaly' | 'daily_brief';
export type AnalysisDirection = 'positive' | 'negative' | 'neutral' | 'unknown';
export type AnalysisEventHorizon = 'intraday' | 'short' | 'medium' | 'unknown';

export interface AnalysisDTO {
  id: string;
  symbol: string;
  analysis_type: AnalysisType;
  direction: AnalysisDirection | null;
  horizon: AnalysisEventHorizon | null;
  confidence: number | null;
  summary: string;
  evidence: EvidenceDTO[];
  model_provider?: string | null;
  model_name?: string | null;
  data_cutoff: string;
  created_at: string;
  risk_flags?: string[];
  unknowns?: string[];
}

// ---------------------------------------------------------------------------
// 预测（spec §7.4）
// ---------------------------------------------------------------------------

export type PredictionHorizon = 'today_close' | 'next_5d';
export type ConfidenceLabel = 'low' | 'medium' | 'high';

export interface PredictionModelDTO {
  key: string;
  version: string;
  /** false → UI 必须显示"未优于基准"，且置信度只能为 low（spec §9.4）。 */
  better_than_baseline: boolean;
}

/** 概率与区间必须同时出现（spec §13.2）——因此两者都是必填字段。 */
export interface ReturnIntervalDTO {
  p20: number;
  p80: number;
}

export interface PredictionDTO {
  symbol: string;
  horizon: PredictionHorizon;
  as_of: string;
  data_cutoff: string;
  reference_price: number;
  probability_up: number;
  expected_return: number;
  return_interval: ReturnIntervalDTO;
  confidence: ConfidenceLabel;
  model: PredictionModelDTO;
  disclaimer: string;
  id?: string;
  target_at?: string;
  /** 已结算时由 API 附带（spec §3.4）。 */
  outcome?: PredictionOutcomeDTO | null;
}

export interface PredictionOutcomeDTO {
  actual_price: number;
  actual_return: number;
  direction_correct: boolean;
  absolute_error: number;
  settled_at: string;
}

/** spec §7.4 成绩单。所有统计量由 API 计算，前端只渲染。 */
export interface ScorecardDTO {
  model_key: string;
  window: number | 'all';
  eligible_count: number;
  settled_count: number;
  pending_count: number;
  direction_accuracy: number | null;
  mae: number | null;
  brier_score: number | null;
  baseline_direction_accuracy: number | null;
  baseline_mae: number | null;
  baseline_brier_score: number | null;
  better_than_baseline: boolean;
  calculated_at: string;
  /** 股票维度成绩单时附带。 */
  symbol?: string;
  horizon?: PredictionHorizon;
}

export type ScorecardWindow = '20' | '100' | 'all';

// ---------------------------------------------------------------------------
// 历史相似行情（spec §7.5 / §10）
// ---------------------------------------------------------------------------

export interface AnalogForwardReturnsDTO {
  next_1d: number | null;
  next_5d: number | null;
}

export interface AnalogDTO {
  /** 相似日期。 */
  as_of: string;
  /** 距离分数。 */
  distance: number;
  /** 当时可见特征（point-in-time）。 */
  features: Record<string, number>;
  /** 后续真实收益。 */
  forward_returns: AnalogForwardReturnsDTO;
  /** 用于计算距离的特征版本。 */
  feature_set_version: string;
}

// ---------------------------------------------------------------------------
// 系统状态：数据源与模型连接（spec §13.1 /settings/data-sources、§8 降级展示）
//
// 契约缺口说明：spec §7 未给出该页的端点。此处按 §8「界面展示具体失败源和最后
// 成功时间」+ §13.1「模型连接状态」约定 GET /system/status，端点可用
// NEXT_PUBLIC_SYSTEM_STATUS_PATH 覆盖，待与后端最终对齐。
// ---------------------------------------------------------------------------

export type DataSourceStatus = 'ok' | 'degraded' | 'failed';

export interface DataSourceDTO {
  key: string;
  name: string;
  status: DataSourceStatus;
  last_success_at: string | null;
  consecutive_failures: number;
  last_error_code?: string | null;
  last_error_message?: string | null;
}

export type ModelConnectionStatus = 'active' | 'degraded' | 'unavailable';

export interface ModelConnectionDTO {
  model_key: string;
  horizon?: PredictionHorizon;
  status: ModelConnectionStatus;
  active_version: string | null;
  better_than_baseline: boolean | null;
  last_prediction_at: string | null;
  /** 不可用/降级原因，例如特征 PSI 漂移（spec §9.3.1）。 */
  reason?: string | null;
}

export interface AgentConnectionDTO {
  provider: string | null;
  model_name: string | null;
  status: ModelConnectionStatus;
  last_success_at: string | null;
  reason?: string | null;
}

export interface SystemStatusDTO {
  sources: DataSourceDTO[];
  models: ModelConnectionDTO[];
  agent?: AgentConnectionDTO | null;
}
