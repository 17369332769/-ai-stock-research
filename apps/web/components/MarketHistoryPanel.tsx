'use client';

import { useState } from 'react';

import type { BarDTO, BarRangeKey, BarsMetaDTO, BarTimeframe } from '@/lib/api/types';
import { HistoricalBarsChart } from './HistoricalBarsChart';
import { StateNotice } from './StateNotice';

export interface MarketHistoryPanelProps {
  dailyBars: BarDTO[];
  dailyMeta?: BarsMetaDTO | null;
  dailyMessage: string | null;
  minuteBars: BarDTO[];
  minuteMeta?: BarsMetaDTO | null;
  minuteMessage: string | null;
}

const PERIODS: Array<{ key: BarTimeframe; label: string }> = [
  { key: '1d', label: '日线' },
  { key: '5m', label: '5 分钟线' },
];

const DAILY_RANGES: Array<{ key: Exclude<BarRangeKey, 'all'>; label: string }> = [
  { key: '1m', label: '1 个月' },
  { key: '3m', label: '3 个月' },
  { key: '6m', label: '6 个月' },
  { key: '1y', label: '1 年' },
  { key: '3y', label: '3 年' },
];

export function MarketHistoryPanel({
  dailyBars,
  dailyMeta = null,
  dailyMessage,
  minuteBars,
  minuteMeta = null,
  minuteMessage,
}: MarketHistoryPanelProps) {
  const [period, setPeriod] = useState<BarTimeframe>('1d');
  const [range, setRange] = useState<Exclude<BarRangeKey, 'all'>>('1y');
  const bars = period === '1d' ? dailyBars : minuteBars;
  const meta = period === '1d' ? dailyMeta : minuteMeta;
  const message = period === '1d' ? dailyMessage : minuteMessage;
  const summary = period === '1d' ? meta?.summaries[range] : meta?.summaries.all;
  const visibleBars = summary
    ? bars.filter(
        (bar) => bar.bar_time >= summary.start_at && bar.bar_time <= summary.end_at,
      )
    : bars;
  const rangeLabel =
    period === '5m'
      ? '近 5 个交易日'
      : `近 ${DAILY_RANGES.find((item) => item.key === range)?.label ?? '一段时间'}`;

  return (
    <div data-testid="market-history-panel">
      <div className="filter-row" role="group" aria-label="历史行情周期">
        {PERIODS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`filter-chip ${period === item.key ? 'filter-chip--active' : ''}`}
            aria-pressed={period === item.key}
            onClick={() => setPeriod(item.key)}
            data-testid={`history-period-${item.key}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {period === '1d' ? (
        <div className="filter-row history-range" role="group" aria-label="历史行情时间范围">
          {DAILY_RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`filter-chip ${range === item.key ? 'filter-chip--active' : ''}`}
              aria-pressed={range === item.key}
              onClick={() => setRange(item.key)}
              data-testid={`history-range-${item.key}`}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
      {message ? (
        <StateNotice state="partial_data" detail={message} />
      ) : (
        <HistoricalBarsChart
          bars={visibleBars}
          meta={meta}
          summary={summary}
          rangeLabel={rangeLabel}
        />
      )}
    </div>
  );
}
