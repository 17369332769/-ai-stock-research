/**
 * E2E 的 API 夹具（Playwright 路由拦截）。
 *
 * spec §16.1：确定性测试，禁止访问公网、禁止依赖真实后端启动。
 * 所有响应逐字对齐 spec §7 契约形状。
 */

import type { Page, Route } from '@playwright/test';

import type {
  AnalogDTO,
  AnalysisDTO,
  DocumentDTO,
  InstrumentDTO,
  JobDTO,
  PredictionDTO,
  ScorecardDTO,
  SnapshotDTO,
  SystemStatusDTO,
  WatchlistItemDTO,
} from '@/lib/api/types';
import {
  ANALOGS,
  ANOMALY_ANALYSIS,
  DOCUMENTS,
  DOCUMENT_ANALYSIS,
  PREDICTION_5D,
  PREDICTION_TODAY,
  SCORECARD_5D,
  SCORECARD_TODAY,
  SNAPSHOT,
  SYSTEM_STATUS,
  WATCHLIST,
} from '../tests/fixtures';

export const API_GLOB = 'http://127.0.0.1:8000/api/v1/**';

export interface ApiResult {
  status: number;
  body: unknown;
}

function item(body: unknown, status = 200): ApiResult {
  return { status, body };
}

function envelope(body: unknown, status = 200): ApiResult {
  return { status, body: { data: body, request_id: 'req-e2e' } };
}

function list(items: unknown[], status = 200): ApiResult {
  return {
    status,
    body: { data: items, page: { next_cursor: null, has_more: false }, request_id: 'req-e2e' },
  };
}

export function apiError(code: string, message: string, status: number): ApiResult {
  return { status, body: { error: { code, message, request_id: 'req-e2e' } } };
}

export interface MockContext {
  url: URL;
  method: string;
  symbol: string | null;
  horizon: string | null;
  type: string | null;
  window: string | null;
  modelKey: string | null;
  postData: unknown;
}

export type Handler = (ctx: MockContext) => ApiResult;

export interface MockOverrides {
  watchlist?: Handler;
  addWatchlist?: Handler;
  removeWatchlist?: Handler;
  reorderWatchlist?: Handler;
  search?: Handler;
  job?: Handler;
  snapshot?: Handler;
  documents?: Handler;
  analyses?: Handler;
  predictionLatest?: Handler;
  predictionHistory?: Handler;
  analogs?: Handler;
  scorecard?: Handler;
  systemStatus?: Handler;
}

export const CSI300_INSTRUMENTS: InstrumentDTO[] = [
  { symbol: '600519', name: '贵州茅台', exchange: 'SSE', industry: '食品饮料' },
  { symbol: '000001', name: '平安银行', exchange: 'SZSE', industry: '银行' },
];

export const BACKFILL_JOB_QUEUED: JobDTO = {
  id: 'job-e2e-1',
  status: 'queued',
  completed_steps: 0,
  total_steps: 3,
  current_step: 'daily_bars',
  error_code: null,
  warnings: [],
  job_type: 'instrument_backfill',
  symbol: '600519',
};

/** 默认全绿场景。各用例只覆盖需要的端点。 */
function defaults(): Required<MockOverrides> {
  return {
    watchlist: () => list(WATCHLIST as WatchlistItemDTO[]),
    addWatchlist: (ctx) => {
      const symbol = (ctx.postData as { symbol?: string } | null)?.symbol ?? '600519';
      return envelope(
        {
          watchlist_item: { symbol, display_order: 0 },
          backfill_job: { ...BACKFILL_JOB_QUEUED, symbol },
        },
        202,
      );
    },
    removeWatchlist: () => item(null, 204),
    reorderWatchlist: () => envelope({ ok: true }),
    search: (ctx) => {
      const q = (ctx.url.searchParams.get('q') ?? '').toLowerCase();
      const matched = CSI300_INSTRUMENTS.filter(
        (instrument) =>
          instrument.symbol.includes(q) || instrument.name.toLowerCase().includes(q),
      );
      return list(matched);
    },
    job: () => envelope({ ...BACKFILL_JOB_QUEUED, status: 'running', completed_steps: 1, current_step: 'minute_bars' }),
    snapshot: () => item(SNAPSHOT as SnapshotDTO),
    documents: () => list(DOCUMENTS as DocumentDTO[]),
    analyses: (ctx) =>
      list(
        ctx.type === 'anomaly'
          ? ([ANOMALY_ANALYSIS] as AnalysisDTO[])
          : ([DOCUMENT_ANALYSIS] as AnalysisDTO[]),
      ),
    predictionLatest: (ctx) =>
      item(
        (ctx.horizon === 'today_close' ? PREDICTION_TODAY : PREDICTION_5D) as PredictionDTO,
      ),
    predictionHistory: () => list([PREDICTION_5D] as PredictionDTO[]),
    analogs: () => list(ANALOGS as AnalogDTO[]),
    scorecard: (ctx) =>
      envelope(
        (ctx.modelKey === 'a_share_today_lightgbm'
          ? SCORECARD_TODAY
          : SCORECARD_5D) as ScorecardDTO,
      ),
    systemStatus: () => item(SYSTEM_STATUS as SystemStatusDTO),
  };
}

function buildContext(route: Route): MockContext {
  const request = route.request();
  const url = new URL(request.url());
  const parts = url.pathname.split('/').filter(Boolean); // api, v1, ...

  const stockIndex = parts.indexOf('stocks');
  const modelIndex = parts.indexOf('models');

  let postData: unknown = null;
  try {
    postData = request.postDataJSON();
  } catch {
    postData = null;
  }

  return {
    url,
    method: request.method(),
    symbol: stockIndex >= 0 ? parts[stockIndex + 1] ?? null : null,
    horizon: url.searchParams.get('horizon'),
    type: url.searchParams.get('type'),
    window: url.searchParams.get('window'),
    modelKey: modelIndex >= 0 ? parts[modelIndex + 1] ?? null : null,
    postData,
  };
}

function resolve(handlers: Required<MockOverrides>, ctx: MockContext): ApiResult {
  const path = ctx.url.pathname;
  const { method } = ctx;

  if (path.endsWith('/watchlist') && method === 'GET') return handlers.watchlist(ctx);
  if (path.endsWith('/watchlist') && method === 'POST') return handlers.addWatchlist(ctx);
  if (path.endsWith('/watchlist/order') && method === 'PATCH') return handlers.reorderWatchlist(ctx);
  if (path.includes('/watchlist/') && method === 'DELETE') return handlers.removeWatchlist(ctx);
  if (path.endsWith('/instruments/search')) return handlers.search(ctx);
  if (path.includes('/jobs/')) return handlers.job(ctx);
  if (path.endsWith('/snapshot')) return handlers.snapshot(ctx);
  if (path.endsWith('/documents')) return handlers.documents(ctx);
  if (path.endsWith('/analyses')) return handlers.analyses(ctx);
  if (path.endsWith('/predictions/latest')) return handlers.predictionLatest(ctx);
  if (path.endsWith('/predictions/history')) return handlers.predictionHistory(ctx);
  if (path.endsWith('/analogs')) return handlers.analogs(ctx);
  if (path.includes('/scorecard')) return handlers.scorecard(ctx);
  if (path.endsWith('/system/status')) return handlers.systemStatus(ctx);

  return apiError('INVALID_ARGUMENT', `未预期的请求：${method} ${path}`, 400);
}

/** 安装 API 夹具。返回请求日志，便于断言"前端没有多余/缺失的调用"。 */
export async function installApi(page: Page, overrides: MockOverrides = {}) {
  const handlers = { ...defaults(), ...overrides } as Required<MockOverrides>;
  const requests: string[] = [];

  await page.route(API_GLOB, async (route) => {
    const ctx = buildContext(route);
    requests.push(`${ctx.method} ${ctx.url.pathname}${ctx.url.search}`);
    const result = resolve(handlers, ctx);

    if (result.status === 204 || result.body === null) {
      await route.fulfill({ status: result.status, contentType: 'application/json', body: '' });
      return;
    }

    await route.fulfill({
      status: result.status,
      contentType: 'application/json',
      body: JSON.stringify(result.body),
    });
  });

  return requests;
}
