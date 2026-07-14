/**
 * 红线测试（验收会逐条查）。
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AnalysisCard } from '@/components/AnalysisCard';
import { EvidenceList } from '@/components/EvidenceList';
import { PredictionHistoryTable } from '@/components/PredictionHistoryTable';
import { PredictionPanel } from '@/components/PredictionPanel';
import { QuoteHeader } from '@/components/QuoteHeader';
import { ScorecardTable } from '@/components/ScorecardTable';
import { WatchlistTable } from '@/components/WatchlistTable';
import { resolveConfidenceDisplay } from '@/lib/prediction';
import {
  ANOMALY_ANALYSIS,
  ANOMALY_WITHOUT_EVIDENCE,
  FRESH_QUOTE,
  NAME,
  PREDICTION_5D,
  PREDICTION_TODAY,
  SCORECARD_5D,
  SCORECARD_TODAY,
  SETTLED_PREDICTION,
  STALE_QUOTE,
  SYMBOL,
  TRADING_MARKET,
  WATCHLIST,
} from './fixtures';

describe('红线：概率与区间必须同时出现（spec §13.2）', () => {
  it('预测面板同时给出上涨概率与 P20/P80 区间', () => {
    render(<PredictionPanel horizon="next_5d" prediction={PREDICTION_5D} />);
    expect(screen.getByTestId('prediction-probability')).toHaveTextContent('38.0%');
    expect(screen.getByTestId('prediction-interval')).toHaveTextContent('-4.10%');
    expect(screen.getByTestId('prediction-interval')).toHaveTextContent('+1.90%');
    expect(screen.getByTestId('prediction-expected-return')).toHaveTextContent('-1.10%');
  });

  it('页面不出现只有方向、没有概率区间的表达', () => {
    const { container } = render(<PredictionPanel horizon="next_5d" prediction={PREDICTION_5D} />);
    const text = container.textContent ?? '';
    // 若出现"看涨/看跌"字样，则必须同时出现概率与区间；此处直接禁止该表达。
    expect(text).not.toMatch(/看涨|看跌/);
  });

  it('历史预测表格每行同样给出概率与区间', () => {
    render(<PredictionHistoryTable predictions={[SETTLED_PREDICTION]} />);
    expect(screen.getByTestId('history-probability')).toHaveTextContent('38.0%');
    expect(screen.getByTestId('history-interval')).toHaveTextContent('~');
  });
});

describe('红线：任何预测区域都显示免责声明（spec §13.2）', () => {
  it('有预测时显示', () => {
    render(<PredictionPanel horizon="next_5d" prediction={PREDICTION_5D} />);
    expect(screen.getByTestId('disclaimer')).toHaveTextContent('仅供研究，不构成投资建议');
  });

  it('无预测时同样显示', () => {
    render(<PredictionPanel horizon="next_5d" prediction={null} state="no_prediction" />);
    expect(screen.getByTestId('disclaimer')).toHaveTextContent('仅供研究，不构成投资建议');
  });
});

describe('红线：模型未优于基准 → 显示「未优于基准」且置信度为低（spec §9.4）', () => {
  it('better_than_baseline=false 时展示未优于基准 + 低置信度', () => {
    render(<PredictionPanel horizon="next_5d" prediction={PREDICTION_5D} />);
    expect(screen.getByTestId('baseline-flag')).toHaveTextContent('未优于基准');
    expect(screen.getByTestId('baseline-flag')).toHaveAttribute('data-better-than-baseline', 'false');
    expect(screen.getByTestId('prediction-confidence')).toHaveTextContent('置信度：低');
    expect(screen.getByTestId('prediction-confidence')).toHaveAttribute('data-confidence', 'low');
  });

  it('接口若返回 better_than_baseline=false 但置信度为 medium，展示端下调为低并明示不一致', () => {
    const inconsistent = { ...PREDICTION_5D, confidence: 'medium' as const };
    expect(resolveConfidenceDisplay(inconsistent)).toEqual({ label: 'low', clamped: true });

    render(<PredictionPanel horizon="next_5d" prediction={inconsistent} />);
    expect(screen.getByTestId('prediction-confidence')).toHaveTextContent('置信度：低');
    expect(screen.getByTestId('confidence-clamped-note')).toBeInTheDocument();
  });

  it('better_than_baseline=true 时保留 API 置信度，不做下调', () => {
    expect(resolveConfidenceDisplay(PREDICTION_TODAY)).toEqual({ label: 'medium', clamped: false });
    render(<PredictionPanel horizon="today_close" prediction={PREDICTION_TODAY} />);
    expect(screen.getByTestId('baseline-flag')).toHaveTextContent('优于基准');
    expect(screen.getByTestId('prediction-confidence')).toHaveTextContent('置信度：中');
  });

  it('成绩单标注未优于基准', () => {
    render(<ScorecardTable scorecard={SCORECARD_5D} />);
    const flag = screen.getByTestId('scorecard-baseline-flag');
    expect(flag).toHaveTextContent('未优于基准');
    expect(flag).toHaveAttribute('data-better-than-baseline', 'false');
  });

  it('优于基准的模型标注优于基准', () => {
    render(<ScorecardTable scorecard={SCORECARD_TODAY} />);
    const flag = screen.getByTestId('scorecard-baseline-flag');
    expect(flag).toHaveTextContent('优于基准');
    expect(flag).not.toHaveTextContent('未优于基准');
    expect(flag).toHaveAttribute('data-better-than-baseline', 'true');
  });

  it('成绩单同时展示模型与基准的三项指标和样本数（spec §7.4）', () => {
    render(<ScorecardTable scorecard={SCORECARD_5D} />);
    expect(screen.getByTestId('scorecard-eligible-count')).toHaveTextContent('100');
    expect(screen.getByTestId('scorecard-settled-count')).toHaveTextContent('98');
    expect(screen.getByTestId('scorecard-pending-count')).toHaveTextContent('2');
    expect(screen.getByTestId('scorecard-direction-accuracy')).toHaveTextContent('54.0%');
    expect(screen.getByTestId('scorecard-mae')).toHaveTextContent('0.0180');
    expect(screen.getByTestId('scorecard-brier')).toHaveTextContent('0.2470');
    expect(screen.getByTestId('scorecard-baseline-direction-accuracy')).toHaveTextContent('52.0%');
    expect(screen.getByTestId('scorecard-baseline-mae')).toHaveTextContent('0.0190');
    expect(screen.getByTestId('scorecard-baseline-brier')).toHaveTextContent('0.2500');
  });
});

describe('红线：每条 AI 结论必须有可点击证据，无证据时显示「原因未知」（spec §3.2）', () => {
  it('有证据时渲染可点击链接与原文引用', () => {
    render(<AnalysisCard analysis={ANOMALY_ANALYSIS} />);
    const link = screen.getByTestId('evidence-link');
    expect(link).toHaveAttribute('href', ANOMALY_ANALYSIS.evidence[0]!.source_url);
    expect(link.tagName).toBe('A');
    expect(screen.getByTestId('evidence-quote')).toHaveTextContent('净利润同比增长15%到20%');
  });

  it('无证据时显示「原因未知」，方向为未知，且含固定文本「未找到可验证事件原因」', () => {
    render(<AnalysisCard analysis={ANOMALY_WITHOUT_EVIDENCE} />);
    expect(screen.getByTestId('evidence-unknown')).toHaveTextContent('原因未知');
    expect(screen.getByTestId('analysis-direction')).toHaveTextContent('未知');
    expect(screen.getByTestId('analysis-summary')).toHaveTextContent('未找到可验证事件原因');
  });

  it('无证据且摘要缺少固定文本时，界面补出固定文本', () => {
    render(
      <AnalysisCard
        analysis={{ ...ANOMALY_WITHOUT_EVIDENCE, summary: '当日相对指数大幅偏离。' }}
      />,
    );
    expect(screen.getByTestId('analysis-no-cause')).toHaveTextContent('未找到可验证事件原因');
    expect(screen.getByTestId('evidence-unknown')).toHaveTextContent('原因未知');
  });

  it('空证据列表直接渲染「原因未知」', () => {
    render(<EvidenceList evidence={[]} />);
    expect(screen.getByTestId('evidence-unknown')).toHaveTextContent('原因未知');
  });
});

describe('红线：禁止把旧行情标记为实时（spec §3.2）', () => {
  it('stale 行情的整块区域不出现「实时」字样', () => {
    const { container } = render(
      <QuoteHeader symbol={SYMBOL} name={NAME} quote={STALE_QUOTE} market={TRADING_MARKET} />,
    );
    expect(container.textContent).not.toContain('实时');
    expect(container.textContent).toContain('行情可能已过期');
  });

  it('休市时不出现「实时」字样，且显示最新交易日', () => {
    const { container } = render(
      <QuoteHeader
        symbol={SYMBOL}
        name={NAME}
        quote={FRESH_QUOTE}
        market={{ phase: 'closed', is_trading_day: false, latest_trading_day: '2026-07-10' }}
      />,
    );
    expect(container.textContent).not.toContain('实时');
    expect(container.textContent).toContain('休市');
    expect(screen.getByTestId('latest-trading-day')).toBeInTheDocument();
  });
});

const FORBIDDEN_PATTERNS = [
  /买入/,
  /卖出/,
  /下单/,
  /委托/,
  /开户/,
  /加仓/,
  /减仓/,
  /持仓收益/,
  /稳赚/,
  /保本/,
  /收益承诺/,
  /必涨/,
  /涨停敢死/,
];

describe('红线：页面不得出现交易按钮、持仓收益诱导或收益承诺（spec §13.2）', () => {
  const noop = vi.fn();

  const trees = [
    ['预测面板', <PredictionPanel key="p" horizon="next_5d" prediction={PREDICTION_5D} scorecard={SCORECARD_5D} />],
    ['行情头', <QuoteHeader key="q" symbol={SYMBOL} name={NAME} quote={FRESH_QUOTE} market={TRADING_MARKET} />],
    ['自选股表格', <WatchlistTable key="w" items={WATCHLIST} onRemove={noop} onMove={noop} />],
    ['异动分析', <AnalysisCard key="a" analysis={ANOMALY_ANALYSIS} />],
    ['成绩单', <ScorecardTable key="s" scorecard={SCORECARD_5D} />],
    ['历史预测', <PredictionHistoryTable key="h" predictions={[SETTLED_PREDICTION]} />],
  ] as const;

  it.each(trees)('%s 不含交易/收益承诺文案', (_name, tree) => {
    const { container } = render(tree);
    const text = container.textContent ?? '';
    for (const pattern of FORBIDDEN_PATTERNS) {
      expect(text).not.toMatch(pattern);
    }
  });

  it('自选股表格里的按钮只有排序与移除，没有交易入口', () => {
    render(<WatchlistTable items={WATCHLIST} onRemove={noop} onMove={noop} />);
    const labels = screen.getAllByRole('button').map((node) => node.textContent ?? '');
    for (const label of labels) {
      expect(label).not.toMatch(/买|卖|下单|交易/);
    }
  });
});
