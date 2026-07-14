import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api/client';
import {
  UI_STATES,
  isJobRunning,
  isMarketClosed,
  mapErrorToState,
  resolveJobState,
  resolvePageStates,
  resolveQuoteStatus,
  stateLabel,
} from '@/lib/ui-state';
import {
  CLOSED_MARKET,
  FRESH_QUOTE,
  JOB_WITH_WARNING,
  RUNNING_JOB,
  STALE_QUOTE,
  TRADING_MARKET,
} from './fixtures';

function apiError(code: ApiError['code'], status: number) {
  return new ApiError({ code, message: 'x', status });
}

describe('九种必须状态（spec §13.2）', () => {
  it('恰好定义九种状态且中文标签齐全', () => {
    expect(UI_STATES).toHaveLength(9);
    expect(UI_STATES.map(stateLabel)).toEqual([
      '首次回补',
      '正常',
      '部分数据缺失',
      '行情可能已过期',
      '数据源失败',
      '模型不可用',
      '无文档',
      '无预测',
      '休市',
    ]);
  });
});

describe('错误码 → 状态映射（spec §7）', () => {
  it('PROVIDER_UNAVAILABLE(424) → 数据源失败', () => {
    expect(mapErrorToState(apiError('PROVIDER_UNAVAILABLE', 424))).toBe('provider_failed');
  });

  it('MODEL_UNAVAILABLE(503) → 模型不可用', () => {
    expect(mapErrorToState(apiError('MODEL_UNAVAILABLE', 503))).toBe('model_unavailable');
  });

  it('INSUFFICIENT_DATA(422) → 无预测（样本不足）', () => {
    expect(mapErrorToState(apiError('INSUFFICIENT_DATA', 422))).toBe('no_prediction');
  });

  it('网络错误按数据源失败处理', () => {
    expect(mapErrorToState(apiError('NETWORK_ERROR', 0))).toBe('provider_failed');
  });

  it('409 / 404 不映射为页面状态（由表单错误展示）', () => {
    expect(mapErrorToState(apiError('NOT_CURRENT_UNIVERSE_MEMBER', 409))).toBeNull();
    expect(mapErrorToState(apiError('DUPLICATE_WATCHLIST_ITEM', 409))).toBeNull();
    expect(mapErrorToState(apiError('INSTRUMENT_NOT_FOUND', 404))).toBeNull();
    expect(mapErrorToState(new Error('boom'))).toBeNull();
  });
});

describe('行情状态：新鲜度只读 API（spec §5.1 / §3.2 红线）', () => {
  it('fresh + 连续竞价时段 → 允许标记实时', () => {
    const status = resolveQuoteStatus(FRESH_QUOTE, TRADING_MARKET);
    expect(status.isRealtime).toBe(true);
    expect(status.stale).toBe(false);
    expect(status.states).toEqual([]);
    expect(status.priceLabel).toBe('最新价');
  });

  it('freshness=stale → 行情过期状态，禁止标记实时', () => {
    const status = resolveQuoteStatus(STALE_QUOTE, TRADING_MARKET);
    expect(status.stale).toBe(true);
    expect(status.isRealtime).toBe(false);
    expect(status.states).toContain('quote_stale');
    expect(status.priceLabel).not.toContain('实时');
    expect(status.ageSeconds).toBe(640);
  });

  it('休市 → 休市状态，价格标注为最新收盘价而非实时', () => {
    const status = resolveQuoteStatus(FRESH_QUOTE, CLOSED_MARKET);
    expect(status.closed).toBe(true);
    expect(status.isRealtime).toBe(false);
    expect(status.states).toContain('market_closed');
    expect(status.priceLabel).toBe('最新收盘价');
  });

  it('休市 + 过期同时成立时两条状态都保留，不互相吞掉', () => {
    const status = resolveQuoteStatus(STALE_QUOTE, CLOSED_MARKET);
    expect(status.states).toEqual(['market_closed', 'quote_stale']);
    expect(status.isRealtime).toBe(false);
  });

  it('午间休市 / 盘前不是连续竞价 → 不标实时', () => {
    for (const phase of ['lunch_break', 'pre_open', 'call_auction'] as const) {
      const status = resolveQuoteStatus(FRESH_QUOTE, {
        phase,
        is_trading_day: true,
        latest_trading_day: '2026-07-14',
      });
      expect(status.isRealtime).toBe(false);
      expect(status.priceLabel).toBe('最后成交价');
    }
  });

  it('is_trading_day=false 也判定为休市', () => {
    expect(
      isMarketClosed({ phase: 'morning', is_trading_day: false, latest_trading_day: '2026-07-10' }),
    ).toBe(true);
    expect(isMarketClosed(TRADING_MARKET)).toBe(false);
    expect(isMarketClosed(null)).toBe(false);
  });

  it('缺少行情时不产生任何行情状态', () => {
    const status = resolveQuoteStatus(null, TRADING_MARKET);
    expect(status.states).toEqual([]);
    expect(status.isRealtime).toBe(false);
  });
});

describe('回补作业状态（spec §7.1）', () => {
  it('queued / running → 首次回补', () => {
    expect(resolveJobState(RUNNING_JOB)).toBe('initial_backfill');
    expect(isJobRunning(RUNNING_JOB)).toBe(true);
    expect(resolveJobState({ ...RUNNING_JOB, status: 'queued' })).toBe('initial_backfill');
  });

  it('成功但带 warning（分钟线缺失）→ 部分数据缺失', () => {
    expect(resolveJobState(JOB_WITH_WARNING)).toBe('partial_data');
  });

  it('失败 → 数据源失败', () => {
    expect(resolveJobState({ ...RUNNING_JOB, status: 'failed' })).toBe('provider_failed');
  });

  it('成功且无 warning → 无附加状态', () => {
    expect(resolveJobState({ ...JOB_WITH_WARNING, warnings: [] })).toBeNull();
    expect(resolveJobState(null)).toBeNull();
  });
});

describe('页面状态汇总', () => {
  it('回补中 + 休市 + 过期 + 缺失同时展示', () => {
    const states = resolvePageStates({
      job: RUNNING_JOB,
      quote: STALE_QUOTE,
      market: CLOSED_MARKET,
      missing: ['minute_bars'],
    });
    expect(states).toContain('initial_backfill');
    expect(states).toContain('partial_data');
    expect(states).toContain('market_closed');
    expect(states).toContain('quote_stale');
  });

  it('数据源失败排在最前', () => {
    const states = resolvePageStates({
      error: apiError('PROVIDER_UNAVAILABLE', 424),
      quote: STALE_QUOTE,
      market: TRADING_MARKET,
    });
    expect(states[0]).toBe('provider_failed');
  });

  it('一切正常时不产生状态条', () => {
    expect(resolvePageStates({ quote: FRESH_QUOTE, market: TRADING_MARKET })).toEqual([]);
  });
});
