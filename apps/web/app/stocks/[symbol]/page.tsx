'use client';

import { use, useCallback, useState } from 'react';

import { AnalogsPanel } from '@/components/AnalogsPanel';
import { AnalysisCard } from '@/components/AnalysisCard';
import { BackfillProgress } from '@/components/BackfillProgress';
import { DocumentsPanel } from '@/components/DocumentsPanel';
import { MarketHistoryPanel } from '@/components/MarketHistoryPanel';
import { PredictionPanel } from '@/components/PredictionPanel';
import { QuoteHeader } from '@/components/QuoteHeader';
import { QuoteRefreshControl } from '@/components/QuoteRefreshControl';
import { ScorecardTable } from '@/components/ScorecardTable';
import { EmptyHint, Section } from '@/components/Section';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { UNIVERSE_EXITED_LABEL } from '@/lib/constants';
import { formatAgeSeconds } from '@/lib/format';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { loadResearchPage } from '@/lib/research-page';
import { resolvePageStates, resolveQuoteStatus, type UiState } from '@/lib/ui-state';
import type { QuoteDTO } from '@/lib/api/types';

/**
 * 股票研究页（spec §3.2）。
 *
 * 信息顺序固定：顶部行情 → 历史行情 → 异动摘要 → 预测 → 公告新闻 → 历史相似行情 → 模型成绩。
 * data-order 属性用于 E2E 断言顺序未被打乱。
 */
export default function StockResearchPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);
  const [quoteOverride, setQuoteOverride] = useState<{
    symbol: string;
    quote: QuoteDTO;
  } | null>(null);

  const { data, loading, loaded, reload } = useApiResource(
    () => loadResearchPage(symbol),
    [symbol],
  );

  const handleReload = useCallback(() => reload(), [reload]);
  const handleQuote = useCallback(
    (quote: QuoteDTO) => setQuoteOverride({ symbol, quote }),
    [symbol],
  );

  if (loading && !loaded) {
    return <p className="empty-hint">加载中…</p>;
  }

  if (!data) {
    return (
      <StateNotice
        state="provider_failed"
        detail="无法加载该股票的研究数据。"
        action={
          <button type="button" className="btn" onClick={handleReload}>
            重试
          </button>
        }
      />
    );
  }

  const snapshot = data.snapshot;
  const quote = quoteOverride?.symbol === symbol ? quoteOverride.quote : snapshot?.quote ?? null;
  const market = snapshot?.market ?? null;
  const quoteStatus = resolveQuoteStatus(quote, market);
  const exited = snapshot?.is_current_universe_member === false;

  const pageStates: UiState[] = resolvePageStates({
    job: data.backfillJob,
    quote,
    market,
    missing: snapshot?.missing ?? null,
  });
  if (data.snapshotState && !pageStates.includes(data.snapshotState)) {
    pageStates.unshift(data.snapshotState);
  }

  const scorecardByModel = new Map(data.scorecards.map((card) => [card.model_key, card]));

  return (
    <div data-testid="research-page" data-symbol={symbol}>
      {/* 顶部：最新价、涨跌幅、行情时间、数据源、新鲜度（spec §3.2） */}
      <QuoteHeader
        symbol={symbol}
        name={snapshot?.name ?? symbol}
        quote={quote}
        market={market}
        relativeStrength={snapshot?.relative_strength ?? null}
        exited={exited}
        missingAction={
          quote ? null : (
            <QuoteRefreshControl symbol={symbol} market={market} onQuote={handleQuote} />
          )
        }
      />

      <StateNoticeList
        states={pageStates}
        details={{
          quote_stale: `行情已超过 180 秒未更新（距上次更新 ${formatAgeSeconds(
            quoteStatus.ageSeconds,
          )}），不能作为实时行情使用。`,
          quote_unavailable: quoteStatus.availabilityLabel,
          provider_failed: data.snapshotMessage ?? undefined,
          market_closed: '当前非交易时段，实时行情暂停更新。',
          partial_data:
            snapshot?.missing && snapshot.missing.length > 0
              ? `以下数据未取得：${snapshot.missing.join('、')}。`
              : undefined,
        }}
      />

      {exited ? (
        <StateNotice
          state="partial_data"
          detail={`${UNIVERSE_EXITED_LABEL}：历史页面与既有预测保留，不再生成新预测，也不能重新添加。`}
        />
      ) : null}

      {data.backfillJob ? (
        <Section id="backfill" title="首次回补">
          <BackfillProgress job={data.backfillJob} />
        </Section>
      ) : null}

      <Section
        id="history"
        order={1}
        title="历史行情"
        subtitle="这里展示历史收盘价格，不是当前实时价格；可先看结论，再阅读走势图。"
      >
        <MarketHistoryPanel
          dailyBars={data.dailyBars}
          dailyMeta={data.dailyBarsMeta}
          dailyMessage={data.dailyBarsMessage}
          minuteBars={data.minuteBars}
          minuteMeta={data.minuteBarsMeta}
          minuteMessage={data.minuteBarsMessage}
        />
      </Section>

      {/* 2. 异动摘要 */}
      <Section id="anomaly" order={2} title="异动摘要" subtitle="先给出量价事实，再检索事件证据。">
        {data.anomalies.length === 0 ? (
          <EmptyHint>
            <span data-testid="anomaly-empty">当前无异动事件。</span>
          </EmptyHint>
        ) : (
          data.anomalies.map((analysis) => (
            <AnalysisCard key={analysis.id} analysis={analysis} />
          ))
        )}
      </Section>

      {/* 3. 预测 */}
      <Section
        id="prediction"
        order={3}
        title="预测"
        subtitle="概率与区间同时给出；模型未优于基准时明确标注。"
      >
        {data.predictions.map((slot) => (
          <PredictionPanel
            key={slot.horizon}
            horizon={slot.horizon}
            prediction={slot.prediction}
            state={slot.state}
            stateDetail={slot.message}
            scorecard={
              slot.prediction ? scorecardByModel.get(slot.prediction.model.key) ?? null : null
            }
          />
        ))}
      </Section>

      {/* 4. 公告新闻 */}
      <Section id="documents" order={4} title="公告与新闻">
        <DocumentsPanel documents={data.documents} analyses={data.documentAnalyses} />
      </Section>

      {/* 5. 历史相似行情 */}
      <Section
        id="analogs"
        order={5}
        title="历史相似行情"
        subtitle="仅使用当时可见特征，展示真实后续收益。"
      >
        <AnalogsPanel
          analogs={data.analogs}
          insufficient={data.analogsInsufficient}
          insufficientMessage={data.analogsMessage ?? undefined}
        />
      </Section>

      {/* 6. 模型成绩 */}
      <Section
        id="scorecard"
        order={6}
        title="模型成绩"
        action={
          <a className="btn" href="/scorecard" data-testid="scorecard-link">
            查看完整成绩单
          </a>
        }
      >
        {data.scorecards.length === 0 ? (
          <EmptyHint>
            <span data-testid="scorecard-empty">{data.scorecardMessage ?? '暂无模型成绩数据。'}</span>
          </EmptyHint>
        ) : (
          data.scorecards.map((card) => <ScorecardTable key={card.model_key} scorecard={card} />)
        )}
      </Section>
    </div>
  );
}
