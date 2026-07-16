'use client';

import { useEffect, useId, useRef } from 'react';
import { LineChart } from 'echarts/charts';
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';

import { formatDate, formatDateTime, formatPrice, formatRatioAsPercent } from '@/lib/format';
import type { BarDTO, BarRangeSummaryDTO, BarsMetaDTO } from '@/lib/api/types';
import { SourceDisplay } from './SourceDisplay';

echarts.use([LineChart, AriaComponent, GridComponent, TooltipComponent, SVGRenderer]);

export interface HistoricalBarsChartProps {
  bars: BarDTO[];
  meta?: BarsMetaDTO | null;
  summary?: BarRangeSummaryDTO | null;
  rangeLabel?: string;
}

/** 历史价格图。该组件只读取 BarDTO，不读取实时 Quote。 */
export function HistoricalBarsChart({
  bars,
  meta,
  summary,
  rangeLabel = '全部',
}: HistoricalBarsChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartDescriptionId = useId();
  const latest = bars[bars.length - 1];
  const isMinute = latest?.timeframe === '5m';

  useEffect(() => {
    const container = chartRef.current;
    // JSDOM 没有实际布局宽度；真实浏览器中只在可见容器完成布局后初始化。
    if (!container || container.clientWidth === 0 || bars.length === 0) return;

    const chart = echarts.init(container, undefined, { renderer: 'svg' });
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const formatMoment = isMinute ? formatDateTime : formatDate;
    chart.setOption({
      animationDuration: reduceMotion ? 0 : 350,
      aria: {
        enabled: true,
        description: `历史${isMinute ? '5 分钟' : '日线'}收盘价，共 ${bars.length} 条。`,
      },
      grid: {
        left: 68,
        right: 28,
        top: 38,
        bottom: 54,
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'line' },
        formatter: (params: unknown) => {
          const item = Array.isArray(params) ? params[0] : params;
          const dataIndex =
            item && typeof item === 'object' && 'dataIndex' in item
              ? (item as { dataIndex?: unknown }).dataIndex
              : null;
          const bar = typeof dataIndex === 'number' ? bars[dataIndex] : null;
          if (!bar) return '';
          const amount =
            bar.change_amount === null || bar.change_amount === undefined
              ? '—'
              : `${bar.change_amount >= 0 ? '+' : ''}${formatPrice(bar.change_amount)} 元`;
          return [
            `<strong>${formatMoment(bar.bar_time)}</strong>`,
            `收盘价：${formatPrice(bar.close)} 元`,
            `较上一点：${amount}`,
            `涨跌幅：${formatRatioAsPercent(bar.change_percent)}`,
          ].join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: bars.map((bar) => bar.bar_time),
        axisLabel: {
          color: '#5b6472',
          hideOverlap: true,
          formatter: (value: string) => formatMoment(value),
        },
        axisLine: { lineStyle: { color: '#cfd6df' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: '价格（元）',
        nameTextStyle: { color: '#5b6472', padding: [0, 0, 8, 0] },
        axisLabel: { color: '#5b6472', formatter: (value: number) => formatPrice(value) },
        splitLine: { lineStyle: { color: '#e5eaf0' } },
      },
      series: [
        {
          name: '收盘价',
          type: 'line',
          data: bars.map((bar) => bar.close),
          showSymbol: false,
          symbol: 'circle',
          symbolSize: 8,
          smooth: 0.08,
          lineStyle: { color: '#2563eb', width: 3 },
          itemStyle: { color: '#2563eb' },
          emphasis: { focus: 'series' },
        },
      ],
    });

    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.dispose();
    };
  }, [bars, isMinute]);

  if (bars.length === 0) {
    return (
      <p className="empty-hint" data-testid="historical-bars-empty">
        暂无历史行情数据。
      </p>
    );
  }

  const currentLatest = latest!;
  const formatMoment = isMinute ? formatDateTime : formatDate;
  const periodLabel = isMinute ? '5 分钟线' : '日线';
  const chartTitle = isMinute
    ? '近 5 个交易日走势（每 5 分钟收盘价）'
    : `${rangeLabel}股价走势（日线收盘价）`;
  const effectiveSummary = summary ?? meta?.summaries.all ?? null;
  const change = effectiveSummary?.change_percent ?? null;
  const trendText =
    change === null
      ? '当前区间不足以计算涨跌。'
      : change > 0
        ? `当前区间整体上涨 ${formatRatioAsPercent(change)}。`
        : change < 0
          ? `当前区间整体下降 ${formatRatioAsPercent(Math.abs(change), 2, false)}。`
          : '当前区间首尾收盘价持平。';
  return (
    <div className="history-chart" data-testid="historical-bars">
      <div className="history-chart__intro">
        <h3>{chartTitle}</h3>
        <p>
          {isMinute
            ? '横轴是交易时间，纵轴是价格（元）。每个点代表一个 5 分钟时段结束时的价格。'
            : '横轴是日期，纵轴是价格（元）。每个点代表一个交易日的收盘价，不是当前最新报价。'}
        </p>
      </div>

      {effectiveSummary ? (
        <div className="history-chart__summary" data-testid="history-summary">
          <div>
            <span>最新历史收盘</span>
            <strong data-testid="historical-latest-close">
              {formatPrice(effectiveSummary.end_close)} 元
            </strong>
          </div>
          <div>
            <span>区间变化</span>
            <strong>{formatRatioAsPercent(effectiveSummary.change_percent)}</strong>
          </div>
          <div>
            <span>区间最高收盘</span>
            <strong>{formatPrice(effectiveSummary.highest_close)} 元</strong>
            <small>{formatMoment(effectiveSummary.highest_close_at)}</small>
          </div>
          <div>
            <span>区间最低收盘</span>
            <strong>{formatPrice(effectiveSummary.lowest_close)} 元</strong>
            <small>{formatMoment(effectiveSummary.lowest_close_at)}</small>
          </div>
        </div>
      ) : (
        <div className="history-chart__summary">
          <div>
            <span>最新历史收盘</span>
            <strong data-testid="historical-latest-close">{formatPrice(currentLatest.close)} 元</strong>
          </div>
        </div>
      )}

      <p className="history-chart__trend" data-testid="history-trend">
        {trendText} 以上仅是历史价格变化，不代表未来表现。
      </p>

      <div
        ref={chartRef}
        className="history-chart__echarts"
        role="img"
        aria-label={`历史${periodLabel}收盘价，共 ${bars.length} 条`}
        aria-describedby={chartDescriptionId}
        data-testid="historical-line-chart"
        data-chart-library="echarts"
      />
      <span id={chartDescriptionId} className="sr-only">
        {rangeLabel}，起点 {formatPrice(bars[0]?.close)} 元，终点 {formatPrice(currentLatest.close)} 元。{trendText}
      </span>

      <div className="history-chart__footer">
        <span>
          共 {effectiveSummary?.count ?? bars.length} 个{isMinute ? '5 分钟时段' : '交易日'}，更新至{' '}
          {formatMoment(effectiveSummary?.end_at ?? currentLatest.bar_time)}
        </span>
        <div className="history-chart__explainers">
          <details>
            <summary>{periodLabel}是什么？</summary>
            <p>
              {isMinute
                ? '每 5 分钟记录一次价格，适合观察最近几个交易日的短期波动。'
                : '每个交易日保留一个收盘价格，适合观察几个月到几年的趋势。'}
            </p>
          </details>
          <details>
            <summary>前复权（便于比较长期涨跌）</summary>
            <p>
              公司分红、送股后，股价会产生机械变化。前复权会调整过去的价格，让不同日期更容易连续比较；
              图中的历史价格经过计算，可能与当时实际成交价不同。
            </p>
          </details>
        </div>
        <SourceDisplay
          source={currentLatest.source}
          sourceUrl={currentLatest.source_url}
          dataType={isMinute ? '5 分钟行情' : '历史日线'}
        />
      </div>
      <details className="history-data-table" data-testid="historical-data-table">
        <summary>查看历史行情数据表</summary>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="历史行情逐点数据，可横向滚动">
          <table className="table">
            <caption className="table__caption">
              {rangeLabel}{periodLabel}数据，价格口径为{currentLatest.adjustment === 'qfq' ? '前复权' : currentLatest.adjustment}
            </caption>
            <thead>
              <tr>
                <th scope="col">{isMinute ? '交易时间' : '日期'}</th>
                <th scope="col">收盘价</th>
                <th scope="col">涨跌额</th>
                <th scope="col">涨跌幅</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((bar) => (
                <tr key={bar.bar_time}>
                  <td>{formatMoment(bar.bar_time)}</td>
                  <td>{formatPrice(bar.close)} 元</td>
                  <td>{bar.change_amount == null ? '—' : `${bar.change_amount > 0 ? '+' : ''}${formatPrice(bar.change_amount)} 元`}</td>
                  <td>{formatRatioAsPercent(bar.change_percent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <span className="sr-only" data-testid="historical-source">
        {currentLatest.source}
      </span>
    </div>
  );
}
