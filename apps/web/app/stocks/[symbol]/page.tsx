'use client';

import { use, useCallback } from 'react';

import { AnalogsPanel } from '@/components/AnalogsPanel';
import { AnalysisCard } from '@/components/AnalysisCard';
import { BackfillProgress } from '@/components/BackfillProgress';
import { DocumentsPanel } from '@/components/DocumentsPanel';
import { PredictionPanel } from '@/components/PredictionPanel';
import { QuoteHeader } from '@/components/QuoteHeader';
import { ScorecardTable } from '@/components/ScorecardTable';
import { EmptyHint, Section } from '@/components/Section';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { UNIVERSE_EXITED_LABEL } from '@/lib/constants';
import { formatAgeSeconds } from '@/lib/format';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { loadResearchPage } from '@/lib/research-page';
import { resolvePageStates, resolveQuoteStatus, type UiState } from '@/lib/ui-state';

/**
 * 股票研究页（spec §3.2）。
 *
 * 信息顺序固定：顶部行情 → 异动摘要 → 预测 → 公告新闻 → 历史相似行情 → 模型成绩。
 * data-order 属性用于 E2E 断言顺序未被打乱。
 */
export default function StockResearchPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);

  const { data, loading, loaded, reload } = useApiResource(
    () => loadResearchPage(symbol),
    [symbol],
  );

  const handleReload = useCallback(() => reload(), [reload]);

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
  const quote = snapshot?.quote ?? null;
  const market = snapshot?.market ?? null;
  const quoteStatus = resolveQuoteStatus(quote, market);
  const exited = snapshot?.in_current_universe === false;

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
      />

      <StateNoticeList
        states={pageStates}
        details={{
          quote_stale: `行情已超过 180 秒未更新（距上次更新 ${formatAgeSeconds(
            quoteStatus.ageSeconds,
          )}），不能作为实时行情使用。`,
          provider_failed: data.snapshotMessage ?? undefined,
          market_closed: '当前非交易时段，展示最新交易日的收盘数据。',
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

      {/* 1. 异动摘要 */}
      <Section id="anomaly" order={1} title="异动摘要" subtitle="先给出量价事实，再检索事件证据。">
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

      {/* 2. 预测 */}
      <Section
        id="prediction"
        order={2}
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

      {/* 3. 公告新闻 */}
      <Section id="documents" order={3} title="公告与新闻">
        <DocumentsPanel documents={data.documents} analyses={data.documentAnalyses} />
      </Section>

      {/* 4. 历史相似行情 */}
      <Section
        id="analogs"
        order={4}
        title="历史相似行情"
        subtitle="仅使用当时可见特征，展示真实后续收益。"
      >
        <AnalogsPanel
          analogs={data.analogs}
          insufficient={data.analogsInsufficient}
          insufficientMessage={data.analogsMessage ?? undefined}
        />
      </Section>

      {/* 5. 模型成绩 */}
      <Section
        id="scorecard"
        order={5}
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
