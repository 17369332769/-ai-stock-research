import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AnalogsPanel } from '@/components/AnalogsPanel';
import { BackfillProgress } from '@/components/BackfillProgress';
import { DocumentsPanel } from '@/components/DocumentsPanel';
import { HistoricalBarsChart } from '@/components/HistoricalBarsChart';
import { MarketHistoryPanel } from '@/components/MarketHistoryPanel';
import { PredictionPanel } from '@/components/PredictionPanel';
import { QuoteHeader } from '@/components/QuoteHeader';
import { SourceDisplay } from '@/components/SourceDisplay';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { WatchlistTable } from '@/components/WatchlistTable';
import { UI_STATES, stateLabel } from '@/lib/ui-state';
import {
  CLOSED_MARKET,
  DAILY_BARS,
  DAILY_BARS_META,
  DOCUMENTS,
  FRESH_QUOTE,
  JOB_WITH_WARNING,
  MINUTE_BARS,
  NAME,
  QUEUED_JOB,
  RUNNING_JOB,
  STALE_QUOTE,
  SYMBOL,
  TRADING_MARKET,
  WATCHLIST,
} from './fixtures';

describe('九种状态都能在 UI 上看到（spec §13.2）', () => {
  it.each(UI_STATES)('状态 %s 渲染出中文标签与状态节点', (state) => {
    render(<StateNotice state={state} />);
    const node = screen.getByTestId(`state-${state}`);
    expect(node).toBeInTheDocument();
    expect(node).toHaveAttribute('data-state', state);
    expect(node).toHaveTextContent(stateLabel(state));
  });

  it('多状态并存时全部渲染（休市 + 行情过期 + 部分数据缺失）', () => {
    render(<StateNoticeList states={['market_closed', 'quote_stale', 'partial_data']} />);
    expect(screen.getByTestId('state-market_closed')).toBeInTheDocument();
    expect(screen.getByTestId('state-quote_stale')).toBeInTheDocument();
    expect(screen.getByTestId('state-partial_data')).toBeInTheDocument();
  });
});

describe('QuoteHeader：行情过期与休市红线', () => {
  it('新鲜行情标记「实时」', () => {
    render(
      <QuoteHeader symbol={SYMBOL} name={NAME} quote={FRESH_QUOTE} market={TRADING_MARKET} />,
    );
    expect(screen.getByTestId('badge-realtime')).toHaveTextContent('实时');
    expect(screen.getByTestId('price-label')).toHaveTextContent('最新价');
    expect(screen.getByTestId('quote-freshness')).toHaveTextContent('新鲜');
    expect(screen.getByTestId('quote-source')).toHaveTextContent('东方财富行情');
    expect(screen.getByTestId('quote-source')).toHaveTextContent('通过 AKShare 采集');
    expect(screen.getByTestId('quote-market-time')).toHaveTextContent('上游未提供');
    expect(screen.getByTestId('quote-observed-at')).toBeInTheDocument();
  });

  it('行情过期时不得出现「实时」，并展示过期状态与 age_seconds', () => {
    render(
      <QuoteHeader symbol={SYMBOL} name={NAME} quote={STALE_QUOTE} market={TRADING_MARKET} />,
    );
    expect(screen.queryByTestId('badge-realtime')).not.toBeInTheDocument();
    expect(screen.getByTestId('badge-quote_stale')).toHaveTextContent('行情可能已过期');
    expect(screen.getByTestId('quote-freshness')).toHaveTextContent('已过期');
    expect(screen.getByTestId('quote-freshness')).toHaveTextContent('10 分 40 秒');
    expect(screen.getByTestId('price-label')).not.toHaveTextContent('实时');
  });

  it('休市时显示「休市」与最新交易日，最近行情不标成实时', () => {
    render(<QuoteHeader symbol={SYMBOL} name={NAME} quote={FRESH_QUOTE} market={CLOSED_MARKET} />);
    expect(screen.getByTestId('badge-market_closed')).toHaveTextContent('休市');
    expect(screen.getByTestId('latest-trading-day')).toHaveTextContent('2026');
    expect(screen.getByTestId('price-label')).toHaveTextContent('最近行情');
    expect(screen.queryByTestId('badge-realtime')).not.toBeInTheDocument();
  });

  it('已调出沪深300时展示标记', () => {
    render(
      <QuoteHeader symbol={SYMBOL} name={NAME} quote={FRESH_QUOTE} market={TRADING_MARKET} exited />,
    );
    expect(screen.getByTestId('badge-universe-exited')).toHaveTextContent('已调出沪深300');
  });
});

describe('BackfillProgress：首次回补三步（spec §7.1）', () => {
  it('展示 daily_bars / minute_bars / documents 三步与进度', () => {
    render(<BackfillProgress job={RUNNING_JOB} />);
    expect(screen.getByTestId('backfill-step-counter')).toHaveTextContent('回补进度 1/3');
    expect(screen.getByTestId('backfill-current-step')).toHaveTextContent('分钟线回补');

    const steps = document.querySelectorAll('[data-step]');
    expect([...steps].map((node) => node.getAttribute('data-step'))).toEqual([
      'daily_bars',
      'minute_bars',
      'documents',
    ]);
    expect(document.querySelector('[data-step="daily_bars"]')).toHaveAttribute(
      'data-step-state',
      'done',
    );
    expect(document.querySelector('[data-step="minute_bars"]')).toHaveAttribute(
      'data-step-state',
      'active',
    );
  });

  it('排队中作业进度为 0', () => {
    render(<BackfillProgress job={QUEUED_JOB} />);
    expect(screen.getByTestId('backfill-step-counter')).toHaveTextContent('回补进度 0/3');
  });

  it('分钟线缺失记 warning，不使回补失败（部分数据缺失）', () => {
    render(<BackfillProgress job={JOB_WITH_WARNING} />);
    expect(screen.getByTestId('backfill-warnings')).toHaveTextContent('分钟线不可获得');
    expect(screen.getByTestId('backfill-progress')).toHaveAttribute('data-job-status', 'succeeded');
  });
});

describe('DocumentsPanel / AnalogsPanel 的空状态', () => {
  it('无文档 → 展示「无文档」状态', () => {
    render(<DocumentsPanel documents={[]} analyses={[]} />);
    expect(screen.getByTestId('state-no_documents')).toHaveTextContent('无文档');
  });

  it('历史候选不足 → 关闭历史相似行情并说明样本不足（spec §10）', () => {
    render(<AnalogsPanel analogs={[]} insufficient insufficientMessage="历史样本不足" />);
    expect(screen.getByTestId('analogs-insufficient')).toHaveTextContent('历史样本不足');
  });

  it('公告新闻每页展示 20 条，并可切换下一页', () => {
    const documents = Array.from({ length: 45 }, (_, index) => ({
      ...DOCUMENTS[index % DOCUMENTS.length]!,
      id: `doc-page-${index + 1}`,
      title: `文档 ${index + 1}`,
    }));
    render(<DocumentsPanel documents={documents} analyses={[]} />);

    expect(screen.getAllByTestId('document-link')).toHaveLength(20);
    expect(screen.getByTestId('documents-page-indicator')).toHaveTextContent('第 1 页，共 3 页');
    fireEvent.click(screen.getByTestId('documents-page-next'));
    expect(screen.getAllByTestId('document-link')).toHaveLength(20);
    expect(screen.getByTestId('documents-page-indicator')).toHaveTextContent('第 2 页，共 3 页');
  });

  it('公告来源使用统一中文名称，不直接显示技术标识', () => {
    const { container } = render(<DocumentsPanel documents={[DOCUMENTS[0]!]} analyses={[]} />);
    expect(container).toHaveTextContent('巨潮资讯');
    expect(container).not.toHaveTextContent('cninfo');
  });

  it('新闻来源本身是中文媒体名时保留原名', () => {
    const chinesePublisher = { ...DOCUMENTS[1]!, source: '澎湃新闻' };
    const { container } = render(
      <DocumentsPanel documents={[chinesePublisher]} analyses={[]} />,
    );
    expect(container).toHaveTextContent('澎湃新闻');
    expect(container).not.toHaveTextContent('未识别的数据来源');
  });
});

describe('SourceDisplay：来源中文化', () => {
  it('已知来源主界面显示中文，技术标识放在详情中', () => {
    render(<SourceDisplay source="sina_via_akshare" dataType="历史日线" />);
    expect(screen.getByTestId('source-display')).toHaveTextContent('新浪财经行情');
    expect(screen.getByTestId('source-display')).toHaveTextContent('通过 AKShare 采集');
    expect(screen.getByText('查看技术信息')).toBeInTheDocument();
  });

  it('未知标识明确提示未识别', () => {
    render(<SourceDisplay source="new_vendor" />);
    expect(screen.getByTestId('source-display')).toHaveTextContent('未识别的数据来源');
    expect(screen.getByTestId('source-display')).toHaveAttribute('data-source-known', 'false');
  });
});

describe('HistoricalBarsChart：历史行情独立展示', () => {
  it('显示日线区间、最新收盘与来源', () => {
    render(
      <HistoricalBarsChart
        bars={DAILY_BARS}
        meta={DAILY_BARS_META}
        summary={DAILY_BARS_META.summaries['1y']}
        rangeLabel="近 1 年"
      />,
    );
    expect(screen.getByTestId('historical-line-chart')).toHaveAttribute(
      'aria-label',
      '历史日线收盘价，共 3 条',
    );
    expect(screen.getByTestId('historical-line-chart')).toHaveAttribute(
      'data-chart-library',
      'echarts',
    );
    expect(screen.getByTestId('historical-latest-close')).toHaveTextContent('1,215.04');
    expect(screen.getByTestId('historical-source')).toHaveTextContent('akshare');
    expect(screen.getByTestId('historical-bars')).not.toHaveTextContent('实时');
    expect(screen.getByTestId('historical-bars')).toHaveTextContent('横轴是日期');
    expect(screen.getByTestId('historical-bars')).toHaveTextContent('前复权（便于比较长期涨跌）');
    expect(screen.getByTestId('history-summary')).toHaveTextContent('区间最高收盘');
  });

  it('日线与 5 分钟线使用明确周期切换', () => {
    render(
      <MarketHistoryPanel
        dailyBars={DAILY_BARS}
        dailyMeta={DAILY_BARS_META}
        dailyMessage={null}
        minuteBars={MINUTE_BARS}
        minuteMeta={null}
        minuteMessage={null}
      />,
    );
    expect(screen.getByTestId('history-period-1d')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('history-range-1y')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('history-period-5m'));
    expect(screen.getByTestId('history-period-5m')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('historical-line-chart')).toHaveAttribute(
      'aria-label',
      '历史5 分钟线收盘价，共 2 条',
    );
  });
});

describe('PredictionPanel 的无预测状态', () => {
  it('模型不可用（503）→ 模型不可用状态 + 免责声明', () => {
    render(<PredictionPanel horizon="next_5d" prediction={null} state="model_unavailable" />);
    expect(screen.getByTestId('state-model_unavailable')).toHaveTextContent('模型不可用');
    expect(screen.getByTestId('disclaimer')).toHaveTextContent('仅供研究，不构成投资建议');
  });

  it('无预测（09:45 前 / 样本不足）→ 无预测状态', () => {
    render(<PredictionPanel horizon="today_close" prediction={null} state="no_prediction" />);
    expect(screen.getByTestId('state-no_prediction')).toHaveTextContent('无预测');
  });

  it('回补进行中 → 首次回补状态，不显示未经训练的预测', () => {
    render(<PredictionPanel horizon="today_close" prediction={null} state="initial_backfill" />);
    expect(screen.getByTestId('state-initial_backfill')).toHaveTextContent('首次回补');
    expect(screen.queryByTestId('prediction-probability')).not.toBeInTheDocument();
  });
});

describe('WatchlistTable：排序、搜索、数据新鲜度（spec §13.1）', () => {
  const noop = vi.fn();

  it('展示新鲜度徽标：新鲜 / 行情可能已过期', () => {
    render(<WatchlistTable items={WATCHLIST} onRemove={noop} onMove={noop} />);
    expect(screen.getByTestId('badge-fresh')).toBeInTheDocument();
    expect(screen.getByTestId('badge-quote_stale')).toBeInTheDocument();
    expect(screen.getAllByTestId('watchlist-row')).toHaveLength(2);
  });

  it('回补状态放在研究状态列，不覆盖行情新鲜度', () => {
    render(
      <WatchlistTable
        items={[{ ...WATCHLIST[0]!, backfill_job: RUNNING_JOB }]}
        onRemove={noop}
        onMove={noop}
      />,
    );
    expect(screen.getByTestId('row-analysis-status')).toHaveTextContent('首次回补');
    expect(screen.getByTestId('badge-fresh')).toBeInTheDocument();
  });

  it('已调出沪深300的自选股带标记', () => {
    render(
      <WatchlistTable
        items={[{ ...WATCHLIST[0]!, is_current_universe_member: false }]}
        onRemove={noop}
        onMove={noop}
      />,
    );
    expect(screen.getByTestId('row-universe-exited')).toHaveTextContent('已调出沪深300');
  });
});
