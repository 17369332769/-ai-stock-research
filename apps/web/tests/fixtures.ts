/**
 * 固定测试夹具（spec §16.1：确定性测试，禁止访问公网）。
 * Vitest 组件测试与 Playwright E2E 共用同一份数据。
 */

import type {
  AnalogDTO,
  AnalysisDTO,
  BarDTO,
  BarsMetaDTO,
  DocumentDTO,
  JobDTO,
  MarketDTO,
  PredictionDTO,
  QuoteDTO,
  ScorecardDTO,
  SnapshotDTO,
  SystemStatusDTO,
  WatchlistItemDTO,
} from '@/lib/api/types';

export const SYMBOL = '600519';
export const NAME = '贵州茅台';

export const FRESH_QUOTE: QuoteDTO = {
  price: 1215.04,
  change_percent: 0.0033,
  observed_at: '2026-07-14T09:50:00+08:00',
  source: 'eastmoney_via_akshare',
  source_url: 'https://quote.eastmoney.com/sh600519.html',
  freshness: 'fresh',
  age_seconds: 12,
  previous_close: 1211.03,
};

export const STALE_QUOTE: QuoteDTO = {
  ...FRESH_QUOTE,
  observed_at: '2026-07-14T09:40:00+08:00',
  freshness: 'stale',
  age_seconds: 640,
};

export const TRADING_MARKET: MarketDTO = {
  phase: 'morning',
  is_trading_day: true,
  latest_trading_day: '2026-07-14',
};

export const CLOSED_MARKET: MarketDTO = {
  phase: 'closed',
  is_trading_day: false,
  latest_trading_day: '2026-07-10',
};

export const RUNNING_JOB: JobDTO = {
  id: 'job-0001',
  status: 'running',
  completed_steps: 1,
  total_steps: 3,
  current_step: 'minute_bars',
  error_code: null,
  warnings: [],
  job_type: 'instrument_backfill',
  symbol: SYMBOL,
};

export const QUEUED_JOB: JobDTO = {
  ...RUNNING_JOB,
  status: 'queued',
  completed_steps: 0,
  current_step: 'daily_bars',
};

export const JOB_WITH_WARNING: JobDTO = {
  ...RUNNING_JOB,
  status: 'succeeded',
  completed_steps: 3,
  current_step: 'documents',
  warnings: ['分钟线不可获得：上游未返回 5m 数据'],
};

export const SNAPSHOT: SnapshotDTO = {
  symbol: SYMBOL,
  name: NAME,
  quote: FRESH_QUOTE,
  relative_strength: {
    benchmark: '000300',
    stock_change_percent: 0.0033,
    benchmark_change_percent: -0.0035,
  },
  latest_anomaly_analysis_id: 'analysis-anomaly-1',
  latest_predictions: ['prediction-1'],
  market: TRADING_MARKET,
  is_current_universe_member: true,
};

export const DAILY_BARS: BarDTO[] = [
  {
    symbol: SYMBOL,
    timeframe: '1d',
    bar_time: '2026-07-10T15:00:00+08:00',
    open: 1198.2,
    high: 1210.5,
    low: 1192.8,
    close: 1207.3,
    volume: 2831040,
    amount: 3408218240,
    adjustment: 'qfq',
    source: 'akshare',
    source_url: null,
    observed_at: '2026-07-10T16:00:00+08:00',
  },
  {
    symbol: SYMBOL,
    timeframe: '1d',
    bar_time: '2026-07-11T15:00:00+08:00',
    open: 1208.1,
    high: 1218.8,
    low: 1201.4,
    close: 1211.03,
    volume: 3012200,
    amount: 3650091200,
    adjustment: 'qfq',
    source: 'akshare',
    source_url: null,
    observed_at: '2026-07-11T16:00:00+08:00',
  },
  {
    symbol: SYMBOL,
    timeframe: '1d',
    bar_time: '2026-07-14T15:00:00+08:00',
    open: 1212.6,
    high: 1222.4,
    low: 1209.5,
    close: 1215.04,
    volume: 3184200,
    amount: 3870125600,
    adjustment: 'qfq',
    source: 'akshare',
    source_url: null,
    observed_at: '2026-07-14T16:00:00+08:00',
  },
];

export const DAILY_BARS_META: BarsMetaDTO = {
  timeframe: '1d',
  total_count: 3,
  updated_at: '2026-07-14T16:00:00+08:00',
  summaries: {
    all: {
      range_key: 'all',
      count: 3,
      start_at: DAILY_BARS[0]!.bar_time,
      end_at: DAILY_BARS[2]!.bar_time,
      start_close: 1207.3,
      end_close: 1215.04,
      change_percent: 0.00641,
      highest_close: 1215.04,
      highest_close_at: DAILY_BARS[2]!.bar_time,
      lowest_close: 1207.3,
      lowest_close_at: DAILY_BARS[0]!.bar_time,
    },
    '1y': {
      range_key: '1y',
      count: 3,
      start_at: DAILY_BARS[0]!.bar_time,
      end_at: DAILY_BARS[2]!.bar_time,
      start_close: 1207.3,
      end_close: 1215.04,
      change_percent: 0.00641,
      highest_close: 1215.04,
      highest_close_at: DAILY_BARS[2]!.bar_time,
      lowest_close: 1207.3,
      lowest_close_at: DAILY_BARS[0]!.bar_time,
    },
  },
};

export const MINUTE_BARS: BarDTO[] = [
  {
    ...DAILY_BARS[0]!,
    timeframe: '5m',
    bar_time: '2026-07-14T09:30:00+08:00',
    open: 1211.03,
    high: 1213.5,
    low: 1210.8,
    close: 1212.4,
    observed_at: '2026-07-14T09:36:00+08:00',
  },
  {
    ...DAILY_BARS[1]!,
    timeframe: '5m',
    bar_time: '2026-07-14T09:35:00+08:00',
    open: 1212.4,
    high: 1215.2,
    low: 1211.9,
    close: 1214.8,
    observed_at: '2026-07-14T09:41:00+08:00',
  },
];

export const ANOMALY_ANALYSIS: AnalysisDTO = {
  id: 'analysis-anomaly-1',
  symbol: SYMBOL,
  analysis_type: 'anomaly',
  direction: 'positive',
  horizon: 'short',
  confidence: 0.62,
  summary: '5分钟收益超过过去60日同时段99百分位，且成交量进度为20日均值的2.4倍。',
  evidence: [
    {
      document_id: 'doc-1',
      title: '贵州茅台：2026年半年度业绩预增公告',
      source_url: 'http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1',
      published_at: '2026-07-14T08:30:00+08:00',
      quote: '预计上半年归属于上市公司股东的净利润同比增长15%到20%。',
    },
  ],
  model_provider: 'ollama',
  model_name: 'qwen2.5:14b-instruct',
  data_cutoff: '2026-07-14T09:50:00+08:00',
  created_at: '2026-07-14T09:51:00+08:00',
  risk_flags: ['业绩预增为区间值，尚未经审计'],
};

export const ANOMALY_WITHOUT_EVIDENCE: AnalysisDTO = {
  ...ANOMALY_ANALYSIS,
  id: 'analysis-anomaly-2',
  direction: 'unknown',
  horizon: 'unknown',
  summary: '当日收益与沪深300收益差超过2个百分点。未找到可验证事件原因。',
  evidence: [],
  risk_flags: [],
};

export const DOCUMENTS: DocumentDTO[] = [
  {
    id: 'doc-1',
    symbol: SYMBOL,
    document_type: 'announcement',
    title: '贵州茅台：2026年半年度业绩预增公告',
    source: 'cninfo',
    source_url: 'http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1',
    published_at: '2026-07-14T08:30:00+08:00',
    observed_at: '2026-07-14T08:35:00+08:00',
  },
  {
    id: 'doc-2',
    symbol: SYMBOL,
    document_type: 'news',
    title: '白酒板块early盘走强，龙头个股领涨',
    source: 'eastmoney_news',
    source_url: 'https://finance.eastmoney.com/a/202607142.html',
    published_at: '2026-07-14T09:20:00+08:00',
    observed_at: '2026-07-14T09:25:00+08:00',
  },
];

export const DOCUMENT_ANALYSIS: AnalysisDTO = {
  id: 'analysis-doc-1',
  symbol: SYMBOL,
  analysis_type: 'document',
  direction: 'positive',
  horizon: 'medium',
  confidence: 0.55,
  summary: '公司披露半年度业绩预增，净利润同比增长区间为15%-20%。',
  evidence: [
    {
      document_id: 'doc-1',
      title: '贵州茅台：2026年半年度业绩预增公告',
      source_url: 'http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1',
      published_at: '2026-07-14T08:30:00+08:00',
      quote: '预计上半年归属于上市公司股东的净利润同比增长15%到20%。',
    },
  ],
  model_provider: 'ollama',
  model_name: 'qwen2.5:14b-instruct',
  data_cutoff: '2026-07-14T09:50:00+08:00',
  created_at: '2026-07-14T09:52:00+08:00',
};

/** 未优于基准 → 置信度必须为 low（spec §9.4）。 */
export const PREDICTION_5D: PredictionDTO = {
  symbol: SYMBOL,
  horizon: 'next_5d',
  as_of: '2026-07-14T09:50:00+08:00',
  data_cutoff: '2026-07-14T09:50:00+08:00',
  reference_price: 1215.04,
  probability_up: 0.38,
  expected_return: -0.011,
  return_interval: { p20: -0.041, p80: 0.019 },
  confidence: 'low',
  model: {
    key: 'a_share_5d_lightgbm',
    version: '2026.07.14.1',
    better_than_baseline: false,
  },
  disclaimer: '仅供研究，不构成投资建议',
  id: 'prediction-1',
  target_at: '2026-07-21T15:00:00+08:00',
};

export const PREDICTION_TODAY: PredictionDTO = {
  symbol: SYMBOL,
  horizon: 'today_close',
  as_of: '2026-07-14T09:50:00+08:00',
  data_cutoff: '2026-07-14T09:50:00+08:00',
  reference_price: 1211.03,
  probability_up: 0.61,
  expected_return: 0.004,
  return_interval: { p20: -0.008, p80: 0.017 },
  confidence: 'medium',
  model: {
    key: 'a_share_today_lightgbm',
    version: '2026.07.14.2',
    better_than_baseline: true,
  },
  disclaimer: '仅供研究，不构成投资建议',
  id: 'prediction-2',
  target_at: '2026-07-14T15:00:00+08:00',
};

export const SETTLED_PREDICTION: PredictionDTO = {
  ...PREDICTION_5D,
  id: 'prediction-0',
  as_of: '2026-07-07T09:45:00+08:00',
  target_at: '2026-07-14T15:00:00+08:00',
  outcome: {
    actual_price: 1230.5,
    actual_return: 0.0128,
    direction_correct: false,
    absolute_error: 0.0238,
    settled_at: '2026-07-14T15:20:00+08:00',
  },
};

export const SCORECARD_5D: ScorecardDTO = {
  model_key: 'a_share_5d_lightgbm',
  window: 100,
  eligible_count: 100,
  settled_count: 98,
  pending_count: 2,
  direction_accuracy: 0.54,
  mae: 0.018,
  brier_score: 0.247,
  baseline_direction_accuracy: 0.52,
  baseline_mae: 0.019,
  baseline_brier_score: 0.25,
  better_than_baseline: false,
  calculated_at: '2026-07-14T15:30:00+08:00',
};

export const SCORECARD_TODAY: ScorecardDTO = {
  model_key: 'a_share_today_lightgbm',
  window: 100,
  eligible_count: 100,
  settled_count: 100,
  pending_count: 0,
  direction_accuracy: 0.57,
  mae: 0.0091,
  brier_score: 0.238,
  baseline_direction_accuracy: 0.52,
  baseline_mae: 0.0104,
  baseline_brier_score: 0.249,
  better_than_baseline: true,
  calculated_at: '2026-07-14T15:30:00+08:00',
};

export const ANALOGS: AnalogDTO[] = [
  {
    as_of: '2024-03-12',
    distance: 0.184,
    features: { momentum_5d: 0.021, volatility_20d: 0.014, volume_ratio: 1.82 },
    forward_returns: { next_1d: 0.006, next_5d: 0.031 },
    feature_set_version: 'v1.2.0',
  },
  {
    as_of: '2023-09-05',
    distance: 0.203,
    features: { momentum_5d: 0.018, volatility_20d: 0.016, volume_ratio: 1.77 },
    forward_returns: { next_1d: -0.004, next_5d: -0.012 },
    feature_set_version: 'v1.2.0',
  },
];

export const WATCHLIST: WatchlistItemDTO[] = [
  {
    symbol: SYMBOL,
    name: NAME,
    display_order: 0,
    universe_code: 'CSI300',
    is_current_universe_member: true,
    quote: FRESH_QUOTE,
    market: TRADING_MARKET,
  },
  {
    symbol: '000001',
    name: '平安银行',
    display_order: 1,
    universe_code: 'CSI300',
    is_current_universe_member: true,
    quote: {
      price: 11.62,
      change_percent: -0.0121,
      observed_at: '2026-07-14T09:44:00+08:00',
      source: 'eastmoney_via_akshare',
      freshness: 'stale',
      age_seconds: 380,
    },
    market: TRADING_MARKET,
  },
];

export const SYSTEM_STATUS: SystemStatusDTO = {
  sources: [
    {
      key: 'akshare',
      name: 'AKShare 行情',
      status: 'ok',
      active_source: 'eastmoney_via_akshare',
      last_success_at: '2026-07-14T09:50:00+08:00',
      consecutive_failures: 0,
      next_run_at: '2026-07-14T09:51:00+08:00',
      coverage: 300,
      total: 300,
      job_count: 3,
      failing_jobs: [],
    },
    {
      key: 'cn_disclosure',
      name: '巨潮/交易所公告',
      status: 'ok',
      active_source: 'cninfo',
      last_success_at: '2026-07-14T09:45:00+08:00',
      consecutive_failures: 0,
      next_run_at: '2026-07-14T09:55:00+08:00',
      coverage: 300,
      total: 300,
      job_count: 1,
      failing_jobs: [],
    },
    {
      key: 'csi300',
      name: '中证指数成分',
      status: 'ok',
      active_source: 'csindex',
      last_success_at: '2026-07-14T07:30:00+08:00',
      consecutive_failures: 0,
      next_run_at: '2026-07-14T18:30:00+08:00',
      coverage: 300,
      total: 300,
      job_count: 1,
      failing_jobs: [],
    },
  ],
  models: [
    {
      model_key: 'a_share_today_lightgbm',
      horizon: 'today_close',
      status: 'active',
      active_version: '2026.07.14.2',
      better_than_baseline: true,
      last_prediction_at: '2026-07-14T09:50:00+08:00',
    },
    {
      model_key: 'a_share_5d_lightgbm',
      horizon: 'next_5d',
      status: 'active',
      active_version: '2026.07.14.1',
      better_than_baseline: false,
      last_prediction_at: '2026-07-14T09:50:00+08:00',
    },
  ],
  agent: {
    provider: 'ollama',
    model_name: 'qwen2.5:14b-instruct',
    status: 'active',
    last_success_at: '2026-07-14T09:52:00+08:00',
  },
};

/** 数据源失败场景（spec §8 / §15：展示失败源与最后成功时间）。 */
export const SYSTEM_STATUS_DEGRADED: SystemStatusDTO = {
  ...SYSTEM_STATUS,
  sources: [
    {
      key: 'akshare',
      name: 'AKShare 行情',
      status: 'failed',
      active_source: 'eastmoney_via_akshare',
      last_success_at: '2026-07-14T09:20:00+08:00',
      consecutive_failures: 5,
      next_run_at: '2026-07-14T09:51:00+08:00',
      coverage: 300,
      total: 300,
      job_count: 3,
      failing_jobs: ['自选股报价'],
      last_error_code: 'PROVIDER_UNAVAILABLE',
      last_error_message: '上游连续超时',
    },
    ...SYSTEM_STATUS.sources.slice(1),
  ],
  models: [
    {
      model_key: 'a_share_today_lightgbm',
      horizon: 'today_close',
      status: 'unavailable',
      active_version: null,
      better_than_baseline: null,
      last_prediction_at: null,
      reason: '关键特征 PSI 超过 0.30，已停止生成新预测',
    },
    ...SYSTEM_STATUS.models.slice(1),
  ],
};
