import { describe, expect, it } from 'vitest';

import { matchesResearchPoolFilters, sortResearchPoolItems } from '@/lib/research-pool';
import type { WatchlistItemDTO } from '@/lib/api/types';
import { CLOSED_MARKET, FRESH_QUOTE, STALE_QUOTE, TRADING_MARKET, WATCHLIST } from './fixtures';

function row(overrides: Partial<WatchlistItemDTO> = {}): WatchlistItemDTO {
  return { ...WATCHLIST[0]!, ...overrides };
}

const baseFilters = {
  query: '',
  direction: 'all' as const,
  quote: 'all' as const,
  signal: 'all' as const,
  industry: '',
};

describe('研究池结构化筛选与排序', () => {
  it('行情异常同时覆盖延迟、过期和无行情，但不把休市最近行情算异常', () => {
    const delayed = row({ quote: { ...FRESH_QUOTE, age_status: 'delayed', data_age_seconds: 68 } });
    const stale = row({ quote: STALE_QUOTE });
    const unavailable = row({ quote: null });
    const closed = row({ quote: STALE_QUOTE, market: CLOSED_MARKET });

    expect(matchesResearchPoolFilters(delayed, { ...baseFilters, quote: 'abnormal' })).toBe(true);
    expect(matchesResearchPoolFilters(stale, { ...baseFilters, quote: 'abnormal' })).toBe(true);
    expect(matchesResearchPoolFilters(unavailable, { ...baseFilters, quote: 'abnormal' })).toBe(true);
    expect(matchesResearchPoolFilters(closed, { ...baseFilters, quote: 'abnormal' })).toBe(false);
  });

  it('支持方向、行业、异动、文档、预测和等待分析筛选', () => {
    const item = row({
      industry: '食品饮料',
      has_anomaly: true,
      has_documents: true,
      has_prediction: true,
      analysis_status: 'queued',
      market: TRADING_MARKET,
    });
    expect(matchesResearchPoolFilters(item, { ...baseFilters, direction: 'up' })).toBe(true);
    expect(matchesResearchPoolFilters(item, { ...baseFilters, industry: '食品饮料' })).toBe(true);
    for (const signal of ['anomaly', 'documents', 'prediction', 'waiting', 'events'] as const) {
      expect(matchesResearchPoolFilters(item, { ...baseFilters, signal })).toBe(true);
    }
  });

  it('数据年龄排序使用精确秒数，不只按状态档位', () => {
    const rows = [
      row({ symbol: '000003', quote: { ...FRESH_QUOTE, data_age_seconds: 44 } }),
      row({ symbol: '000001', quote: { ...FRESH_QUOTE, data_age_seconds: 2 } }),
      row({ symbol: '000002', quote: { ...FRESH_QUOTE, data_age_seconds: 31 } }),
    ];
    expect(sortResearchPoolItems(rows, 'freshness', 'asc').map((item) => item.symbol)).toEqual([
      '000001',
      '000002',
      '000003',
    ]);
  });
});
