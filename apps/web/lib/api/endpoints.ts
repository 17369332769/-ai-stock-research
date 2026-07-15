/**
 * spec §7 端点。所有请求集中在此，页面不直接拼 URL。
 */

import {
  apiDelete,
  apiGet,
  apiGetItem,
  apiGetList,
  apiGetListWithMeta,
  apiPatch,
  apiPost,
} from './client';
import type {
  AnalogDTO,
  AnalysisDTO,
  AnalysisType,
  BarDTO,
  BarsMetaDTO,
  BarTimeframe,
  DocumentDTO,
  DocumentType,
  InstrumentDTO,
  JobDTO,
  QuoteRefreshDTO,
  PredictionDTO,
  PredictionHorizon,
  ScorecardDTO,
  ScorecardWindow,
  SnapshotDTO,
  SystemStatusDTO,
  WatchlistAddResultDTO,
  WatchlistItemDTO,
} from './types';

// --- 自选股（spec §7.1）--------------------------------------------------

export interface WatchlistQuery {
  cursor?: string;
  limit?: number;
  q?: string;
}

export function getWatchlist(query: WatchlistQuery = {}) {
  return apiGetList<WatchlistItemDTO>('/watchlist', {
    cursor: query.cursor,
    limit: query.limit,
    q: query.q,
  });
}

/** 成绩单选择器需要完整代码集合，因此显式遍历服务端游标。 */
export async function getAllWatchlistItems() {
  const items: WatchlistItemDTO[] = [];
  let cursor: string | undefined;

  do {
    const response = await getWatchlist({ cursor, limit: 100 });
    items.push(...response.items);
    cursor = response.page.has_more ? response.page.next_cursor ?? undefined : undefined;
  } while (cursor);

  return items;
}

/** 首次添加返回 202 + 回补作业；调用方需读 status 判断是否进入"首次回补"状态。 */
export function addWatchlistItem(symbol: string) {
  return apiPost<WatchlistAddResultDTO>('/watchlist', { symbol });
}

export function removeWatchlistItem(symbol: string) {
  return apiDelete<unknown>(`/watchlist/${encodeURIComponent(symbol)}`);
}

export function reorderWatchlist(symbols: string[]) {
  return apiPatch<unknown>('/watchlist/order', { symbols });
}

/** 只搜索查询日沪深300当前成分（spec §7.1）。 */
export function searchInstruments(query: string, limit = 20) {
  return apiGetList<InstrumentDTO>('/instruments/search', {
    universe: 'CSI300',
    q: query,
    limit,
  });
}

export function getUniverseMembers(asOf?: string, cursor?: string, limit = 100) {
  return apiGetList<InstrumentDTO>('/universes/CSI300/instruments', {
    as_of: asOf,
    cursor,
    limit,
  });
}

export function getJob(jobId: string) {
  return apiGet<JobDTO>(`/jobs/${encodeURIComponent(jobId)}`);
}

// --- 快照（spec §7.2）----------------------------------------------------

/** 保留 HTTP 状态：202 = 首次回补进行中。 */
export function getSnapshot(symbol: string) {
  return apiGetItem<SnapshotDTO>(`/stocks/${encodeURIComponent(symbol)}/snapshot`);
}

export function getBars(symbol: string, timeframe: BarTimeframe = '1d', limit = 240) {
  return apiGetListWithMeta<BarDTO, BarsMetaDTO>(
    `/stocks/${encodeURIComponent(symbol)}/bars`,
    {
      timeframe,
      limit,
    },
  );
}

export function refreshQuote(symbol: string) {
  return apiPost<QuoteRefreshDTO>(`/stocks/${encodeURIComponent(symbol)}/quote-refresh`);
}

// --- 文档与解释（spec §7.3）----------------------------------------------

export function getDocuments(symbol: string, type?: DocumentType, limit = 50) {
  return apiGetList<DocumentDTO>(`/stocks/${encodeURIComponent(symbol)}/documents`, {
    type,
    limit,
  });
}

export function getAnalyses(symbol: string, type?: AnalysisType) {
  return apiGetList<AnalysisDTO>(`/stocks/${encodeURIComponent(symbol)}/analyses`, { type });
}

export function refreshAnalyses(symbol: string) {
  return apiPost<AnalysisDTO | { job: JobDTO }>(
    `/stocks/${encodeURIComponent(symbol)}/analyses/refresh`,
  );
}

// --- 预测（spec §7.4）----------------------------------------------------

export function getLatestPrediction(symbol: string, horizon: PredictionHorizon) {
  return apiGetItem<PredictionDTO>(
    `/stocks/${encodeURIComponent(symbol)}/predictions/latest`,
    { horizon },
  );
}

export function getPredictionHistory(
  symbol: string,
  horizon: PredictionHorizon,
  limit = 100,
) {
  return apiGetList<PredictionDTO>(
    `/stocks/${encodeURIComponent(symbol)}/predictions/history`,
    { horizon, limit },
  );
}

export function getScorecard(modelKey: string, window: ScorecardWindow) {
  return apiGet<ScorecardDTO>(`/models/${encodeURIComponent(modelKey)}/scorecard`, {
    window,
  });
}

// --- 历史相似行情（spec §7.5）--------------------------------------------

export function getAnalogs(symbol: string, horizon: PredictionHorizon = 'next_5d', limit = 10) {
  return apiGetList<AnalogDTO>(`/stocks/${encodeURIComponent(symbol)}/analogs`, {
    horizon,
    limit,
  });
}

// --- 系统状态（spec §13.1 数据源页 / §8 降级展示）-------------------------
// 契约缺口：spec §7 未定义该端点。默认 /system/status，可用环境变量覆盖。

export const SYSTEM_STATUS_PATH =
  process.env.NEXT_PUBLIC_SYSTEM_STATUS_PATH ?? '/system/status';

export function getSystemStatus() {
  return apiGet<SystemStatusDTO>(SYSTEM_STATUS_PATH);
}
