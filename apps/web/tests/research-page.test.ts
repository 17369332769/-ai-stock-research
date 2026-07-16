import { afterEach, describe, expect, it, vi } from 'vitest';

import { loadResearchPage } from '@/lib/research-page';
import {
  ANALOGS,
  ANOMALY_ANALYSIS,
  DAILY_BARS,
  MINUTE_BARS,
  DOCUMENTS,
  DOCUMENT_ANALYSIS,
  PREDICTION_5D,
  PREDICTION_TODAY,
  RUNNING_JOB,
  SCORECARD_5D,
  SCORECARD_TODAY,
  SNAPSHOT,
} from './fixtures';

type Route = (url: URL) => Response;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorBody(code: string, message: string, status: number): Response {
  return json({ error: { code, message, request_id: 'req-1' } }, status);
}

function list(items: unknown[]): Response {
  return json({ data: items, page: { next_cursor: null, has_more: false }, request_id: 'r' });
}

/** 默认全绿的路由表，测试按需覆盖单个端点。 */
function installRoutes(overrides: Partial<Record<string, Route>> = {}) {
  const routes: Record<string, Route> = {
    snapshot: () => json(SNAPSHOT),
    bars: (url) => list(url.searchParams.get('timeframe') === '5m' ? MINUTE_BARS : DAILY_BARS),
    analysesAnomaly: () => list([ANOMALY_ANALYSIS]),
    analysesDocument: () => list([DOCUMENT_ANALYSIS]),
    documents: () => list(DOCUMENTS),
    analogs: () => list(ANALOGS),
    predictionToday: () => json(PREDICTION_TODAY),
    prediction5d: () => json(PREDICTION_5D),
    scorecardToday: () => json({ data: SCORECARD_TODAY, request_id: 'r' }),
    scorecard5d: () => json({ data: SCORECARD_5D, request_id: 'r' }),
    ...overrides,
  };

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input);
      const path = url.pathname;
      const horizon = url.searchParams.get('horizon');
      const type = url.searchParams.get('type');

      if (path.endsWith('/snapshot')) return routes.snapshot!(url);
      if (path.endsWith('/bars')) return routes.bars!(url);
      if (path.endsWith('/analyses')) {
        return type === 'anomaly' ? routes.analysesAnomaly!(url) : routes.analysesDocument!(url);
      }
      if (path.endsWith('/documents')) return routes.documents!(url);
      if (path.endsWith('/analogs')) return routes.analogs!(url);
      if (path.endsWith('/predictions/latest')) {
        return horizon === 'today_close' ? routes.predictionToday!(url) : routes.prediction5d!(url);
      }
      if (path.includes('/models/a_share_today_lightgbm/')) return routes.scorecardToday!(url);
      if (path.includes('/models/a_share_5d_lightgbm/')) return routes.scorecard5d!(url);

      return json({ error: { code: 'INVALID_ARGUMENT', message: path, request_id: 'r' } }, 400);
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('研究页装配（spec §3.2）', () => {
  it('全部数据可用时装配完整页面', async () => {
    installRoutes();
    const data = await loadResearchPage('600519');

    expect(data.snapshot?.name).toBe('贵州茅台');
    expect(data.snapshotState).toBeNull();
    expect(data.dailyBars).toEqual(DAILY_BARS);
    expect(data.dailyBarsMessage).toBeNull();
    expect(data.minuteBars).toEqual(MINUTE_BARS);
    expect(data.minuteBarsMessage).toBeNull();
    expect(data.anomalies).toHaveLength(1);
    expect(data.documents).toHaveLength(2);
    expect(data.analogs).toHaveLength(2);
    expect(data.predictions.map((slot) => slot.horizon)).toEqual(['today_close', 'next_5d']);
    expect(data.predictions.every((slot) => slot.prediction !== null)).toBe(true);
    expect(data.scorecards.map((card) => card.model_key).sort()).toEqual([
      'a_share_5d_lightgbm',
      'a_share_today_lightgbm',
    ]);
  });

  it('模型不可用（503）只让预测区降级，其余区块照常展示', async () => {
    installRoutes({
      predictionToday: () => errorBody('MODEL_UNAVAILABLE', '没有可用模型版本', 503),
      prediction5d: () => errorBody('MODEL_UNAVAILABLE', '没有可用模型版本', 503),
    });

    const data = await loadResearchPage('600519');
    expect(data.predictions.every((slot) => slot.state === 'model_unavailable')).toBe(true);
    expect(data.predictions.every((slot) => slot.prediction === null)).toBe(true);
    // 行情、文档、相似行情不受影响
    expect(data.snapshot).not.toBeNull();
    expect(data.documents).toHaveLength(2);
    expect(data.analogs).toHaveLength(2);
  });

  it('样本不足（422）→ 无预测；相似行情按样本不足关闭', async () => {
    installRoutes({
      prediction5d: () => errorBody('INSUFFICIENT_DATA', '历史样本不足', 422),
      analogs: () => errorBody('INSUFFICIENT_DATA', '有效候选少于30个', 422),
    });

    const data = await loadResearchPage('600519');
    const slot = data.predictions.find((item) => item.horizon === 'next_5d');
    expect(slot?.state).toBe('no_prediction');
    expect(data.analogsInsufficient).toBe(true);
    expect(data.analogs).toHaveLength(0);
  });

  it('数据源失败（424）时页面不空白：快照降级，其余区块仍可访问（spec §15）', async () => {
    installRoutes({
      snapshot: () => errorBody('PROVIDER_UNAVAILABLE', '上游行情源失败', 424),
    });

    const data = await loadResearchPage('600519');
    expect(data.snapshot).toBeNull();
    expect(data.snapshotState).toBe('provider_failed');
    expect(data.snapshotMessage).toContain('上游行情源失败');
    expect(data.documents).toHaveLength(2);
    expect(data.anomalies).toHaveLength(1);
    expect(data.scorecards.length).toBeGreaterThan(0);
  });

  it('回补进行中（202）→ 首次回补状态，且不产生预测', async () => {
    installRoutes({
      snapshot: () => json({ data: RUNNING_JOB, request_id: 'r' }, 202),
      predictionToday: () => json({ data: { backfill_job: RUNNING_JOB }, request_id: 'r' }, 202),
      prediction5d: () => json({ data: { backfill_job: RUNNING_JOB }, request_id: 'r' }, 202),
    });

    const data = await loadResearchPage('600519');
    expect(data.snapshotState).toBe('initial_backfill');
    expect(data.backfillJob?.id).toBe('job-0001');
    expect(data.predictions.every((slot) => slot.state === 'initial_backfill')).toBe(true);
    expect(data.predictions.every((slot) => slot.prediction === null)).toBe(true);
  });

  it('无文档时文档区为空数组（由组件渲染"无文档"状态）', async () => {
    installRoutes({ documents: () => list([]) });
    const data = await loadResearchPage('600519');
    expect(data.documents).toHaveLength(0);
  });

  it('历史行情不可用时只影响历史行情区', async () => {
    installRoutes({
      bars: (url) =>
        url.searchParams.get('timeframe') === '1d'
          ? errorBody('PROVIDER_UNAVAILABLE', '历史行情暂不可用', 503)
          : list(MINUTE_BARS),
    });
    const data = await loadResearchPage('600519');

    expect(data.dailyBars).toHaveLength(0);
    expect(data.dailyBarsMessage).toContain('历史行情暂不可用');
    expect(data.minuteBars).toEqual(MINUTE_BARS);
    expect(data.snapshot).not.toBeNull();
    expect(data.documents).toHaveLength(2);
  });
});
