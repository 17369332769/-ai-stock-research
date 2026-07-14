import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AnalogsPanel } from '@/components/AnalogsPanel';
import { BackfillProgress } from '@/components/BackfillProgress';
import { DocumentsPanel } from '@/components/DocumentsPanel';
import { PredictionPanel } from '@/components/PredictionPanel';
import { QuoteHeader } from '@/components/QuoteHeader';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { WatchlistTable } from '@/components/WatchlistTable';
import { UI_STATES, stateLabel } from '@/lib/ui-state';
import {
  CLOSED_MARKET,
  FRESH_QUOTE,
  JOB_WITH_WARNING,
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
    expect(screen.getByTestId('quote-source')).toHaveTextContent('eastmoney_via_akshare');
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

  it('休市时显示「休市」与最新交易日，收盘价不标成实时', () => {
    render(<QuoteHeader symbol={SYMBOL} name={NAME} quote={FRESH_QUOTE} market={CLOSED_MARKET} />);
    expect(screen.getByTestId('badge-market_closed')).toHaveTextContent('休市');
    expect(screen.getByTestId('latest-trading-day')).toHaveTextContent('2026');
    expect(screen.getByTestId('price-label')).toHaveTextContent('最新收盘价');
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

  it('回补中的自选股展示首次回补徽标', () => {
    render(
      <WatchlistTable
        items={[{ ...WATCHLIST[0]!, backfill_job: RUNNING_JOB }]}
        onRemove={noop}
        onMove={noop}
      />,
    );
    expect(screen.getByTestId('badge-initial_backfill')).toBeInTheDocument();
  });

  it('已调出沪深300的自选股带标记', () => {
    render(
      <WatchlistTable
        items={[{ ...WATCHLIST[0]!, in_current_universe: false }]}
        onRemove={noop}
        onMove={noop}
      />,
    );
    expect(screen.getByTestId('row-universe-exited')).toHaveTextContent('已调出沪深300');
  });
});
