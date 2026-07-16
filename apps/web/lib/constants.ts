/** 固定文案。与后端 apps/api/app/core/enums.py 的常量保持一致。 */

/** spec §13.2：任何预测区域都必须显示。 */
export const RESEARCH_ONLY_DISCLAIMER = '仅供研究，不构成投资建议';

/** spec §7.3 / §12：无证据时的固定文本。 */
export const NO_VERIFIABLE_CAUSE_TEXT = '未找到可验证事件原因';

/** spec §3.2：每条 AI 结论必须有可点击证据；没有证据时显示"原因未知"。 */
export const UNKNOWN_CAUSE_LABEL = '原因未知';

/** spec §9.4：未优于基准时必须显示，且置信度只能为低。 */
export const NOT_BETTER_THAN_BASELINE_LABEL = '未优于基准';
export const BETTER_THAN_BASELINE_LABEL = '优于基准';

/** spec §3.1：调出成分股后的标记。 */
export const UNIVERSE_EXITED_LABEL = '已调出沪深300';

/** 002/003：行情状态阈值仅用于文案；最终判定始终由 API 的 age_status 给出。 */
export const LATEST_QUOTE_THRESHOLD_SECONDS = 45;
export const STALE_THRESHOLD_SECONDS = 120;

export const HORIZON_LABELS = {
  today_close: '今日收盘',
  next_5d: '未来5个交易日',
} as const;

export const CONFIDENCE_LABELS = {
  low: '低',
  medium: '中',
  high: '高',
} as const;

export const DIRECTION_LABELS = {
  positive: '偏正面',
  negative: '偏负面',
  neutral: '中性',
  unknown: '未知',
} as const;

export const EVENT_HORIZON_LABELS = {
  intraday: '日内',
  short: '短期',
  medium: '中期',
  unknown: '未知',
} as const;

export const DOCUMENT_TYPE_LABELS = {
  announcement: '公告',
  news: '新闻',
} as const;

export const BACKFILL_STEP_LABELS = {
  daily_bars: '日线回补',
  minute_bars: '分钟线回补',
  documents: '公告与新闻回补',
} as const;

export const MARKET_PHASE_LABELS = {
  closed: '休市',
  pre_open: '盘前',
  call_auction: '集合竞价',
  morning: '上午交易中',
  lunch_break: '午间休市',
  afternoon: '下午交易中',
} as const;

export const DATA_SOURCE_STATUS_LABELS = {
  ok: '正常',
  pending: '等待运行',
  degraded: '降级',
  failed: '失败',
} as const;

export const MODEL_STATUS_LABELS = {
  active: '已连接',
  degraded: '降级',
  unavailable: '不可用',
} as const;
