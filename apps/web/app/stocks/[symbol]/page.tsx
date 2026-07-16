'use client';

import Link from 'next/link';
import { Suspense, use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';

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
import { errorMessage } from '@/lib/error-messages';
import {
  getAnalogs,
  getAnalyses,
  getBars,
  getDocuments,
  getJob,
  getLatestPrediction,
  getResearchPool,
  getScorecard,
  getSnapshot,
  refreshAnalyses,
  retryBackfill,
  type ResearchPoolScope,
} from '@/lib/api/endpoints';
import type {
  AnalysisDTO,
  JobDTO,
  PredictionDTO,
  PredictionHorizon,
  QuoteDTO,
  SnapshotDTO,
} from '@/lib/api/types';
import { useApiResource } from '@/lib/hooks/useApiResource';
import {
  matchesResearchPoolFilters,
  sortResearchPoolItems,
  type ResearchDirection,
  type ResearchPoolSortKey,
  type ResearchQuoteFilter,
  type ResearchSignalFilter,
  type SortDirection,
} from '@/lib/research-pool';
import {
  isInsufficientData,
  isJobRunning,
  mapErrorToState,
  resolvePageStates,
  resolveQuoteStatus,
  type UiState,
} from '@/lib/ui-state';

interface SnapshotLoad {
  snapshot: SnapshotDTO | null;
  job: JobDTO | null;
}

interface PredictionLoad {
  horizon: PredictionHorizon;
  prediction: PredictionDTO | null;
  job: JobDTO | null;
}

async function loadSnapshot(symbol: string): Promise<SnapshotLoad> {
  const response = await getSnapshot(symbol);
  if (response.status === 202) {
    return { snapshot: null, job: response.data as unknown as JobDTO };
  }
  return { snapshot: response.data, job: response.data.backfill_job ?? null };
}

async function loadPrediction(symbol: string, horizon: PredictionHorizon): Promise<PredictionLoad> {
  const response = await getLatestPrediction(symbol, horizon);
  if (response.status === 202) {
    if ('backfill_job' in response.data) {
      return { horizon, prediction: null, job: response.data.backfill_job };
    }
    throw new Error('预测回补响应缺少 backfill_job');
  }
  if ('backfill_job' in response.data) throw new Error('预测响应状态与数据不一致');
  return { horizon, prediction: response.data, job: null };
}

function useJobMonitor(initialJob: JobDTO | null, onFinished: () => void) {
  const [job, setJob] = useState<JobDTO | null>(initialJob);
  const [error, setError] = useState<unknown>(null);
  const [pollRetry, setPollRetry] = useState(0);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    setJob(initialJob);
    setError(null);
  }, [initialJob]);

  useEffect(() => {
    if (!job || !isJobRunning(job)) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const current = await getJob(job.id);
        if (cancelled) return;
        setJob(current);
        setError(null);
        if (!isJobRunning(current)) onFinishedRef.current();
      } catch (cause) {
        if (!cancelled) {
          setError(cause);
          setPollRetry((value) => value + 1);
        }
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [job, pollRetry]);

  return { job, error, setJob };
}

function ResourceLoading({ label }: { label: string }) {
  return (
    <div className="section-skeleton" role="status" aria-live="polite">
      <span className="sr-only">正在加载{label}</span>
      <div className="skeleton-row" />
      <div className="skeleton-row" />
    </div>
  );
}

function predictionState(error: unknown): UiState {
  if (isInsufficientData(error)) return 'no_prediction';
  return mapErrorToState(error) ?? 'provider_failed';
}

const POOL_SCOPES = new Set<ResearchPoolScope>(['csi300', 'extra', 'all']);
const DIRECTIONS = new Set<ResearchDirection>(['all', 'up', 'down', 'flat']);
const QUOTE_FILTERS = new Set<ResearchQuoteFilter>([
  'all',
  'abnormal',
  'latest',
  'delayed',
  'stale',
  'unavailable',
]);
const SIGNAL_FILTERS = new Set<ResearchSignalFilter>([
  'all',
  'events',
  'anomaly',
  'documents',
  'prediction',
  'waiting',
]);
const SORT_KEYS = new Set<ResearchPoolSortKey>([
  'display_order',
  'symbol',
  'name',
  'price',
  'change_percent',
  'amount',
  'freshness',
  'anomaly_strength',
  'analysis_updated_at',
]);

function allowedParam<T extends string>(value: string | null, allowed: Set<T>, fallback: T): T {
  return value && allowed.has(value as T) ? (value as T) : fallback;
}

function StockResearchContent({ symbol }: { symbol: string }) {
  const searchParams = useSearchParams();
  const rawReturnTo = searchParams.get('return_to');
  const returnTo = rawReturnTo?.startsWith('/') && !rawReturnTo.startsWith('//') ? rawReturnTo : '/';
  const poolContext = useMemo(() => {
    const queryStart = returnTo.indexOf('?');
    const params = new URLSearchParams(queryStart >= 0 ? returnTo.slice(queryStart + 1) : '');
    return {
      scope: allowedParam(params.get('scope'), POOL_SCOPES, 'csi300'),
      filters: {
        query: params.get('q') ?? '',
        direction: allowedParam(params.get('direction'), DIRECTIONS, 'all'),
        quote: allowedParam(params.get('quote'), QUOTE_FILTERS, 'all'),
        signal: allowedParam(params.get('signal'), SIGNAL_FILTERS, 'all'),
        industry: params.get('industry') ?? '',
      },
      sortKey: allowedParam(params.get('sort'), SORT_KEYS, 'display_order'),
      sortDirection: params.get('order') === 'desc' ? 'desc' as SortDirection : 'asc' as SortDirection,
    };
  }, [returnTo]);
  const [quoteOverride, setQuoteOverride] = useState<{ symbol: string; quote: QuoteDTO } | null>(null);
  const [analysisJob, setAnalysisJob] = useState<JobDTO | null>(null);
  const [analysisSubmitError, setAnalysisSubmitError] = useState<unknown>(null);
  const [backfillSubmitError, setBackfillSubmitError] = useState<unknown>(null);

  const snapshot = useApiResource(() => loadSnapshot(symbol), [symbol]);
  const dailyBars = useApiResource(() => getBars(symbol, '1d', 800), [symbol]);
  const minuteBars = useApiResource(() => getBars(symbol, '5m', 500), [symbol]);
  const anomalies = useApiResource(async () => (await getAnalyses(symbol, 'anomaly')).items, [symbol]);
  const documents = useApiResource(async () => (await getDocuments(symbol)).items, [symbol]);
  const documentAnalyses = useApiResource(async () => (await getAnalyses(symbol, 'document')).items, [symbol]);
  const todayPrediction = useApiResource(() => loadPrediction(symbol, 'today_close'), [symbol]);
  const fiveDayPrediction = useApiResource(() => loadPrediction(symbol, 'next_5d'), [symbol]);
  const analogs = useApiResource(async () => (await getAnalogs(symbol, 'next_5d', 10)).items, [symbol]);
  const pool = useApiResource(
    async () => (await getResearchPool(poolContext.scope)).items,
    [poolContext.scope],
  );

  const persistedAnalysisJob = snapshot.data?.snapshot?.analysis_job ?? null;
  useEffect(() => {
    if (persistedAnalysisJob && persistedAnalysisJob.id !== analysisJob?.id) {
      setAnalysisJob(persistedAnalysisJob);
    }
  }, [analysisJob?.id, persistedAnalysisJob]);

  const modelKeys = useMemo(
    () =>
      [...new Set([
        todayPrediction.data?.prediction?.model.key,
        fiveDayPrediction.data?.prediction?.model.key,
      ].filter((value): value is string => Boolean(value)))],
    [fiveDayPrediction.data?.prediction?.model.key, todayPrediction.data?.prediction?.model.key],
  );
  const scorecards = useApiResource(async () => {
    return Promise.all(modelKeys.map((modelKey) => getScorecard(modelKey, '100')));
  }, [modelKeys.join(',')]);

  const initialBackfillJob =
    snapshot.data?.job ?? todayPrediction.data?.job ?? fiveDayPrediction.data?.job ?? null;
  const reloadSnapshot = snapshot.reload;
  const reloadDailyBars = dailyBars.reload;
  const reloadMinuteBars = minuteBars.reload;
  const reloadDocuments = documents.reload;
  const reloadTodayPrediction = todayPrediction.reload;
  const reloadFiveDayPrediction = fiveDayPrediction.reload;
  const reloadAnomalies = anomalies.reload;
  const reloadDocumentAnalyses = documentAnalyses.reload;
  const reloadBackfillResources = useCallback(() => {
    reloadSnapshot();
    reloadTodayPrediction();
    reloadFiveDayPrediction();
    reloadDailyBars();
    reloadMinuteBars();
    reloadDocuments();
  }, [reloadDailyBars, reloadDocuments, reloadFiveDayPrediction, reloadMinuteBars, reloadSnapshot, reloadTodayPrediction]);
  const backfillMonitor = useJobMonitor(initialBackfillJob, reloadBackfillResources);
  const setMonitoredBackfillJob = backfillMonitor.setJob;

  const handleRetryBackfill = useCallback(async () => {
    setBackfillSubmitError(null);
    try {
      const response = await retryBackfill(symbol);
      setMonitoredBackfillJob(response.data);
    } catch (cause) {
      setBackfillSubmitError(cause);
    }
  }, [setMonitoredBackfillJob, symbol]);

  const reloadAnalyses = useCallback(() => {
    reloadAnomalies();
    reloadDocumentAnalyses();
    reloadSnapshot();
  }, [reloadAnomalies, reloadDocumentAnalyses, reloadSnapshot]);
  const analysisMonitor = useJobMonitor(analysisJob, reloadAnalyses);
  const setMonitoredAnalysisJob = analysisMonitor.setJob;

  const handleRefreshAnalyses = useCallback(async () => {
    setAnalysisSubmitError(null);
    try {
      const response = await refreshAnalyses(symbol);
      setAnalysisJob(response.data as JobDTO);
      setMonitoredAnalysisJob(response.data as JobDTO);
    } catch (cause) {
      setAnalysisSubmitError(cause);
    }
  }, [setMonitoredAnalysisJob, symbol]);

  const snapshotData = snapshot.data?.snapshot ?? null;
  const quote = quoteOverride?.symbol === symbol ? quoteOverride.quote : snapshotData?.quote ?? null;
  const market = snapshotData?.market ?? null;
  const quoteStatus = resolveQuoteStatus(quote, market);
  const exited = snapshotData?.is_universe_exit === true;
  const pageStates = resolvePageStates({
    job: backfillMonitor.job,
    quote,
    market,
    missing: snapshotData?.missing ?? null,
    error: snapshot.error,
  });

  const scorecardByModel = new Map((scorecards.data ?? []).map((card) => [card.model_key, card]));
  const poolItems = useMemo(
    () =>
      sortResearchPoolItems(
        (pool.data ?? []).filter((item) => matchesResearchPoolFilters(item, poolContext.filters)),
        poolContext.sortKey,
        poolContext.sortDirection,
      ),
    [pool.data, poolContext],
  );
  const poolIndex = poolItems.findIndex((item) => item.symbol === symbol);
  const previousSymbol = poolIndex > 0 ? poolItems[poolIndex - 1]?.symbol : null;
  const nextSymbol = poolIndex >= 0 && poolIndex < poolItems.length - 1 ? poolItems[poolIndex + 1]?.symbol : null;

  const quoteRefresh = (
    <QuoteRefreshControl
      symbol={symbol}
      market={market}
      onQuote={(nextQuote) => setQuoteOverride({ symbol, quote: nextQuote })}
    />
  );

  const analysisBusy = isJobRunning(analysisMonitor.job);
  const analysisAction = (
    <button type="button" className="btn btn--ghost" onClick={handleRefreshAnalyses} disabled={analysisBusy} data-testid="analysis-refresh">
      {analysisBusy
        ? analysisMonitor.job?.status === 'queued'
          ? '分析排队中…'
          : '正在更新分析…'
        : '生成或更新分析'}
    </button>
  );

  return (
    <div data-testid="research-page" data-symbol={symbol}>
      <nav className="breadcrumb" aria-label="面包屑">
        <Link href={returnTo}>研究池</Link><span aria-hidden="true">/</span><span>{snapshotData?.name ?? symbol}</span>
      </nav>
      <div className="research-context-nav">
        <Link className="btn btn--ghost" href={returnTo}>返回筛选结果</Link>
        <div className="research-context-nav__neighbors">
          {previousSymbol ? <Link className="btn btn--ghost" href={`/stocks/${previousSymbol}?return_to=${encodeURIComponent(returnTo)}`}>上一只</Link> : null}
          {nextSymbol ? <Link className="btn btn--ghost" href={`/stocks/${nextSymbol}?return_to=${encodeURIComponent(returnTo)}`}>下一只</Link> : null}
        </div>
      </div>

      {snapshot.loading && !snapshot.loaded ? (
        <div className="quote-header"><ResourceLoading label="股票行情" /></div>
      ) : (
        <QuoteHeader
          symbol={symbol}
          name={snapshotData?.name ?? symbol}
          quote={quote}
          market={market}
          relativeStrength={snapshotData?.relative_strength ?? null}
          exited={exited}
          missingAction={!quote && !snapshot.error ? quoteRefresh : null}
          refreshAction={quote && (quoteStatus.delayed || quoteStatus.stale) ? quoteRefresh : null}
        />
      )}

      <StateNoticeList
        states={pageStates}
        details={{
          quote_stale: `行情已超过120秒未更新（${quoteStatus.ageSeconds == null ? '更新时间未知' : `${quoteStatus.ageSeconds}秒前获取`}），不能作为实时行情使用。`,
          quote_unavailable: quoteStatus.availabilityLabel,
          provider_failed: snapshot.error ? errorMessage(snapshot.error) : undefined,
          market_closed: '当前非交易时段，显示最近交易时段行情。',
          partial_data: snapshotData?.missing?.length ? `以下数据未取得：${snapshotData.missing.join('、')}。` : undefined,
        }}
        actions={{
          provider_failed: snapshot.error ? <button type="button" className="btn" onClick={snapshot.reload}>重试股票概览</button> : undefined,
        }}
      />

      {exited ? <StateNotice state="partial_data" detail={`${UNIVERSE_EXITED_LABEL}：历史页面与既有预测保留，不再生成新预测。`} /> : null}
      {backfillMonitor.error ? <StateNotice state="provider_failed" detail={`回补进度更新失败：${errorMessage(backfillMonitor.error)}；系统将在2秒后重试。`} /> : null}
      {backfillSubmitError ? <StateNotice state="provider_failed" detail={`无法重新发起回补：${errorMessage(backfillSubmitError)}`} /> : null}
      {backfillMonitor.job ? (
        <Section id="backfill" title="首次数据回补" subtitle="进度会自动更新，完成后各区块会重新读取。" action={backfillMonitor.job.status === 'failed' ? <button type="button" className="btn" onClick={handleRetryBackfill}>重新发起回补</button> : null}>
          <BackfillProgress job={backfillMonitor.job} />
        </Section>
      ) : null}

      <nav className="research-page-nav" aria-label="研究详情页内导航">
        <a href="#overview">概览</a><a href="#history">走势</a><a href="#anomaly">事件</a><a href="#prediction">预测</a><a href="#scorecard">验证</a>
      </nav>
      <span id="overview" className="anchor-target" />

      <Section id="history" order={1} title="历史行情" subtitle="历史收盘价格与当前报价独立；可先看结论，再阅读走势图。">
        {dailyBars.loading && minuteBars.loading ? (
          <ResourceLoading label="历史行情" />
        ) : (
          <MarketHistoryPanel
            dailyBars={dailyBars.data?.items ?? []}
            dailyMeta={dailyBars.data?.meta ?? null}
            dailyMessage={dailyBars.error ? `日线读取失败：${errorMessage(dailyBars.error)}` : null}
            minuteBars={minuteBars.data?.items ?? []}
            minuteMeta={minuteBars.data?.meta ?? null}
            minuteMessage={minuteBars.error ? `5分钟线读取失败：${errorMessage(minuteBars.error)}` : null}
          />
        )}
        {dailyBars.error || minuteBars.error ? <button type="button" className="btn btn--ghost" onClick={() => { dailyBars.reload(); minuteBars.reload(); }}>重试历史行情</button> : null}
      </Section>

      <Section id="anomaly" order={2} title="异动与事件摘要" subtitle="先给出量价事实，再检索可验证事件证据。" action={analysisAction}>
        {analysisSubmitError ? <StateNotice state="provider_failed" detail={errorMessage(analysisSubmitError)} /> : null}
        {analysisMonitor.error ? <StateNotice state="provider_failed" detail={`分析进度读取失败：${errorMessage(analysisMonitor.error)}；系统将在2秒后重试。`} /> : null}
        {analysisMonitor.job?.status === 'failed' ? <StateNotice state="provider_failed" detail={analysisMonitor.job.error_message ?? '分析任务失败，可重新发起。'} /> : null}
        {anomalies.loading && !anomalies.loaded ? (
          <ResourceLoading label="异动分析" />
        ) : anomalies.error ? (
          <StateNotice state="provider_failed" detail={`异动分析读取失败：${errorMessage(anomalies.error)}`} action={<button type="button" className="btn" onClick={anomalies.reload}>重试</button>} />
        ) : (anomalies.data ?? []).length === 0 ? (
          <EmptyHint><span data-testid="anomaly-empty">截至当前，未检测到符合规则的异动事件。</span></EmptyHint>
        ) : (
          (anomalies.data ?? []).map((analysis: AnalysisDTO) => <AnalysisCard key={analysis.id} analysis={analysis} />)
        )}
      </Section>

      <Section id="prediction" order={3} title="预测" subtitle="概率与区间同时给出；模型未优于基准时明确标注。">
        {[todayPrediction, fiveDayPrediction].map((resource, index) => {
          const horizon: PredictionHorizon = index === 0 ? 'today_close' : 'next_5d';
          if (resource.loading && !resource.loaded) return <ResourceLoading key={horizon} label={`${horizon}预测`} />;
          const prediction = resource.data?.prediction ?? null;
          return (
            <div key={horizon}>
              <PredictionPanel
                horizon={horizon}
                prediction={prediction}
                state={resource.error ? predictionState(resource.error) : resource.data?.job ? 'initial_backfill' : 'no_prediction'}
                stateDetail={resource.error ? errorMessage(resource.error) : resource.data?.job ? '历史数据回补完成前不生成预测。' : '当前尚未生成该周期预测。'}
                scorecard={prediction ? scorecardByModel.get(prediction.model.key) ?? null : null}
              />
              {resource.error ? <button type="button" className="btn btn--ghost" onClick={resource.reload}>重试{horizon === 'today_close' ? '今日收盘' : '未来5日'}预测</button> : null}
            </div>
          );
        })}
      </Section>

      <Section id="documents" order={4} title="公告与新闻" action={analysisAction}>
        {documents.loading && !documents.loaded ? (
          <ResourceLoading label="公告与新闻" />
        ) : documents.error ? (
          <StateNotice state="provider_failed" detail={`公告与新闻读取失败：${errorMessage(documents.error)}`} action={<button type="button" className="btn" onClick={documents.reload}>重试</button>} />
        ) : (
          <>
            {documentAnalyses.error ? <StateNotice state="provider_failed" detail={`文档已取得，但分析摘要读取失败：${errorMessage(documentAnalyses.error)}`} action={<button type="button" className="btn" onClick={documentAnalyses.reload}>重试摘要</button>} /> : null}
            {documentAnalyses.loading && !documentAnalyses.loaded ? <p className="empty-hint" role="status">文档已取得，分析摘要正在读取…</p> : null}
            <DocumentsPanel documents={documents.data ?? []} analyses={documentAnalyses.data ?? []} />
          </>
        )}
      </Section>

      <Section id="analogs" order={5} title="历史相似行情" subtitle="仅使用当时可见特征，展示真实后续收益。">
        {analogs.loading && !analogs.loaded ? (
          <ResourceLoading label="历史相似行情" />
        ) : analogs.error && !isInsufficientData(analogs.error) ? (
          <StateNotice state="provider_failed" detail={`历史相似行情读取失败：${errorMessage(analogs.error)}`} action={<button type="button" className="btn" onClick={analogs.reload}>重试</button>} />
        ) : (
          <AnalogsPanel analogs={analogs.data ?? []} insufficient={isInsufficientData(analogs.error)} insufficientMessage={analogs.error ? errorMessage(analogs.error) : undefined} />
        )}
      </Section>

      <Section
        id="scorecard"
        order={6}
        title="模型验证"
        action={<Link className="btn" href={`/scorecard?symbol=${symbol}&horizon=next_5d`}>查看完整预测成绩单</Link>}
      >
        {scorecards.loading && !scorecards.loaded ? (
          <ResourceLoading label="模型验证" />
        ) : scorecards.error ? (
          <StateNotice state="provider_failed" detail={`模型验证读取失败：${errorMessage(scorecards.error)}`} action={<button type="button" className="btn" onClick={scorecards.reload}>重试</button>} />
        ) : (scorecards.data ?? []).length === 0 ? (
          <EmptyHint><span data-testid="scorecard-empty">当前预测尚无可用的滚动验证成绩。</span></EmptyHint>
        ) : (
          (scorecards.data ?? []).map((card) => <ScorecardTable key={card.model_key} scorecard={card} />)
        )}
      </Section>
    </div>
  );
}

export default function StockResearchPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = use(params);
  return (
    <Suspense fallback={<p className="empty-hint">正在加载股票研究页…</p>}>
      <StockResearchContent symbol={symbol} />
    </Suspense>
  );
}
