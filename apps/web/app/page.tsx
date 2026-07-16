'use client';

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { AddStockPanel } from '@/components/AddStockPanel';
import { BackfillProgress } from '@/components/BackfillProgress';
import { Section } from '@/components/Section';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { WatchlistTable } from '@/components/WatchlistTable';
import {
  addExtraWatchlistItem,
  getJob,
  getResearchPool,
  getSystemStatus,
  removeExtraWatchlistItem,
  retryBackfill,
  type ResearchPoolScope,
} from '@/lib/api/endpoints';
import type { JobDTO, WatchlistItemDTO } from '@/lib/api/types';
import { DATA_SOURCE_STATUS_LABELS, MARKET_PHASE_LABELS } from '@/lib/constants';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime } from '@/lib/format';
import { useApiResource } from '@/lib/hooks/useApiResource';
import {
  matchesResearchPoolFilters,
  type ResearchDirection,
  type ResearchPoolSortKey,
  type ResearchQuoteFilter,
  type ResearchSignalFilter,
  type SortDirection,
} from '@/lib/research-pool';
import { isJobRunning, mapErrorToState, resolveQuoteStatus, type UiState } from '@/lib/ui-state';

const SCOPES: Array<{ key: ResearchPoolScope; label: string }> = [
  { key: 'csi300', label: '沪深300' },
  { key: 'extra', label: '我的关注' },
  { key: 'all', label: '全部研究' },
];

const DIRECTIONS: Array<{ key: ResearchDirection; label: string }> = [
  { key: 'all', label: '全部涨跌' },
  { key: 'up', label: '上涨' },
  { key: 'down', label: '下跌' },
  { key: 'flat', label: '平盘' },
];

const QUOTE_FILTERS: Array<{ key: ResearchQuoteFilter; label: string }> = [
  { key: 'all', label: '全部行情' },
  { key: 'abnormal', label: '行情异常' },
  { key: 'latest', label: '最新' },
  { key: 'delayed', label: '可能延迟' },
  { key: 'stale', label: '可能过期' },
  { key: 'unavailable', label: '无行情' },
];

const SIGNAL_FILTERS: Array<{ key: ResearchSignalFilter; label: string }> = [
  { key: 'all', label: '全部研究状态' },
  { key: 'events', label: '有异动或事件' },
  { key: 'anomaly', label: '有异动' },
  { key: 'documents', label: '有公告或新闻' },
  { key: 'prediction', label: '有预测' },
  { key: 'waiting', label: '等待或失败' },
];

const SORT_OPTIONS: Array<{ key: ResearchPoolSortKey; direction: SortDirection; label: string }> = [
  { key: 'display_order', direction: 'asc', label: '默认顺序' },
  { key: 'symbol', direction: 'asc', label: '代码从小到大' },
  { key: 'symbol', direction: 'desc', label: '代码从大到小' },
  { key: 'name', direction: 'asc', label: '名称正序' },
  { key: 'name', direction: 'desc', label: '名称倒序' },
  { key: 'price', direction: 'desc', label: '价格从高到低' },
  { key: 'price', direction: 'asc', label: '价格从低到高' },
  { key: 'change_percent', direction: 'desc', label: '涨跌幅从高到低' },
  { key: 'change_percent', direction: 'asc', label: '涨跌幅从低到高' },
  { key: 'amount', direction: 'desc', label: '成交额从高到低' },
  { key: 'amount', direction: 'asc', label: '成交额从低到高' },
  { key: 'freshness', direction: 'asc', label: '数据年龄从新到旧' },
  { key: 'freshness', direction: 'desc', label: '数据年龄从旧到新' },
  { key: 'anomaly_strength', direction: 'desc', label: '异动强度从高到低' },
  { key: 'anomaly_strength', direction: 'asc', label: '异动强度从低到高' },
  { key: 'analysis_updated_at', direction: 'desc', label: '分析更新时间从新到旧' },
  { key: 'analysis_updated_at', direction: 'asc', label: '分析更新时间从旧到新' },
];

const VALID_SORT_KEYS = new Set<ResearchPoolSortKey>(SORT_OPTIONS.map((item) => item.key));

interface ActionFeedback {
  kind: 'success' | 'error';
  message: string;
  symbol?: string;
  canUndo?: boolean;
}

function enumParam<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value && allowed.includes(value as T) ? (value as T) : fallback;
}

function ResearchPoolPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const scope = enumParam(
    searchParams.get('scope'),
    SCOPES.map((item) => item.key),
    'csi300',
  );
  const query = searchParams.get('q') ?? '';
  const [queryDraft, setQueryDraft] = useState(query);
  const direction = enumParam(
    searchParams.get('direction'),
    DIRECTIONS.map((item) => item.key),
    'all',
  );
  const quoteFilter = enumParam(
    searchParams.get('quote'),
    QUOTE_FILTERS.map((item) => item.key),
    'all',
  );
  const signal = enumParam(
    searchParams.get('signal'),
    SIGNAL_FILTERS.map((item) => item.key),
    'all',
  );
  const industry = searchParams.get('industry') ?? '';
  const sortCandidate = searchParams.get('sort') as ResearchPoolSortKey | null;
  const sortKey = sortCandidate && VALID_SORT_KEYS.has(sortCandidate) ? sortCandidate : 'display_order';
  const sortDirection = enumParam(searchParams.get('order'), ['asc', 'desc'] as const, 'asc');
  const parsedPage = Number(searchParams.get('page') ?? '1');
  const page = Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const parsedPageSize = Number(searchParams.get('page_size') ?? '50');
  const pageSize = [25, 50, 100].includes(parsedPageSize) ? parsedPageSize : 50;

  const updateParams = useCallback(
    (updates: Record<string, string | number | null | undefined>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === '' || value === 'all') next.delete(key);
        else next.set(key, String(value));
      }
      const suffix = next.toString();
      router.replace(suffix ? `${pathname}?${suffix}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  useEffect(() => {
    setQueryDraft(query);
  }, [query]);

  useEffect(() => {
    if (queryDraft === query) return;
    const timer = window.setTimeout(() => {
      updateParams({ q: queryDraft, page: null });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [query, queryDraft, updateParams]);

  const { data, error, loading, loaded, reload } = useApiResource(
    () => getResearchPool(scope),
    [scope],
  );
  const systemStatus = useApiResource(() => getSystemStatus(), []);
  const [addError, setAddError] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);
  const [busySymbol, setBusySymbol] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string | undefined>>({});
  const [feedback, setFeedback] = useState<ActionFeedback | null>(null);
  const [backfillJobs, setBackfillJobs] = useState<Record<string, JobDTO>>({});
  const [jobPollError, setJobPollError] = useState<string | null>(null);
  const [jobPollRetry, setJobPollRetry] = useState(0);

  const items = useMemo<WatchlistItemDTO[]>(() => data?.items ?? [], [data]);
  const industries = useMemo(
    () => [...new Set(items.map((item) => item.industry).filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [items],
  );

  const filteredItems = useMemo(
    () =>
      items.filter((item) =>
        matchesResearchPoolFilters(item, {
          query: '',
          direction,
          quote: quoteFilter,
          signal,
          industry,
        }),
      ),
    [direction, industry, items, quoteFilter, signal],
  );

  const handleAdd = useCallback(
    async (symbol: string) => {
      setAdding(true);
      setAddError(null);
      setFeedback(null);
      try {
        const response = await addExtraWatchlistItem(symbol);
        const addedSymbol = response.data.watchlist_item.symbol;
        if (response.data.backfill_job) {
          setBackfillJobs((current) => ({ ...current, [addedSymbol]: response.data.backfill_job! }));
        }
        setFeedback({
          kind: 'success',
          symbol: addedSymbol,
          message: `${addedSymbol} 已加入我的关注，可立即查看首次数据回补状态。`,
        });
        updateParams({
          scope: 'extra',
          q: addedSymbol,
          direction: null,
          quote: null,
          signal: null,
          industry: null,
          page: null,
        });
        reload();
      } catch (cause) {
        setAddError(cause);
      } finally {
        setAdding(false);
      }
    },
    [reload, updateParams],
  );

  const handleRemove = useCallback(
    async (symbol: string) => {
      if (!window.confirm(`确认将 ${symbol} 移出我的关注？历史行情、分析和预测会继续保留。`)) return;
      setBusySymbol(symbol);
      setRowErrors((current) => ({ ...current, [symbol]: undefined }));
      setFeedback(null);
      try {
        await removeExtraWatchlistItem(symbol);
        setFeedback({
          kind: 'success',
          symbol,
          canUndo: true,
          message: `${symbol} 已移出我的关注，历史行情、分析和预测均已保留。`,
        });
        reload();
      } catch (cause) {
        setRowErrors((current) => ({ ...current, [symbol]: errorMessage(cause) }));
      } finally {
        setBusySymbol(null);
      }
    },
    [reload],
  );

  const handleUndoRemove = useCallback(async () => {
    const symbol = feedback?.symbol;
    if (!symbol) return;
    setBusySymbol(symbol);
    try {
      const response = await addExtraWatchlistItem(symbol);
      if (response.data.backfill_job) {
        setBackfillJobs((current) => ({ ...current, [symbol]: response.data.backfill_job! }));
      }
      setFeedback({ kind: 'success', symbol, message: `${symbol} 已恢复到我的关注。` });
      reload();
    } catch (cause) {
      setFeedback({ kind: 'error', symbol, message: `无法恢复 ${symbol}：${errorMessage(cause)}` });
    } finally {
      setBusySymbol(null);
    }
  }, [feedback?.symbol, reload]);

  const handleRetryBackfill = useCallback(async (symbol: string) => {
    setBusySymbol(symbol);
    setJobPollError(null);
    try {
      const response = await retryBackfill(symbol);
      setBackfillJobs((current) => ({ ...current, [symbol]: response.data }));
    } catch (cause) {
      setJobPollError(`无法重新发起 ${symbol} 的回补：${errorMessage(cause)}`);
    } finally {
      setBusySymbol(null);
    }
  }, []);

  useEffect(() => {
    const persisted = items.filter(
      (item) =>
        item.backfill_job &&
        (isJobRunning(item.backfill_job) || item.backfill_job.status === 'failed'),
    );
    if (persisted.length === 0) return;
    setBackfillJobs((current) => {
      const next = { ...current };
      for (const item of persisted) next[item.symbol] = item.backfill_job!;
      return next;
    });
  }, [items]);

  useEffect(() => {
    const running = Object.entries(backfillJobs).filter(([, job]) => isJobRunning(job));
    if (running.length === 0) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const refreshed = await Promise.all(
          running.map(async ([symbol, job]) => [symbol, await getJob(job.id)] as const),
        );
        if (cancelled) return;
        setBackfillJobs((current) => {
          const next = { ...current };
          for (const [symbol, job] of refreshed) next[symbol] = job;
          return next;
        });
        setJobPollError(null);
        if (refreshed.some(([, job]) => !isJobRunning(job))) reload();
      } catch (cause) {
        if (!cancelled) {
          setJobPollError(`回补进度暂时无法更新：${errorMessage(cause)}；系统将在2秒后重试。`);
          setJobPollRetry((value) => value + 1);
        }
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backfillJobs, jobPollRetry, reload]);

  const staleItems = items.filter((item) => resolveQuoteStatus(item.quote, item.market).stale);
  const delayedItems = items.filter((item) => resolveQuoteStatus(item.quote, item.market).delayed);
  const unavailableItems = items.filter((item) => !item.quote);
  const risingCount = items.filter((item) => (item.quote?.change_percent ?? 0) > 0).length;
  const fallingCount = items.filter((item) => (item.quote?.change_percent ?? 0) < 0).length;
  const eventCount = items.filter((item) => item.has_anomaly || item.has_documents).length;
  const waitingCount = items.filter((item) =>
    ['waiting', 'queued', 'analyzing', 'failed'].includes(item.analysis_status ?? 'waiting'),
  ).length;
  const market = items[0]?.market ?? null;
  const latestQuoteAt = items
    .map((item) => item.quote?.fetched_at ?? item.quote?.observed_at ?? null)
    .filter((value): value is string => value !== null)
    .sort()
    .at(-1) ?? null;

  const pageStates: UiState[] = [];
  const errorState = mapErrorToState(error);
  if (errorState) pageStates.push(errorState);
  if (staleItems.length > 0) pageStates.push('quote_stale');
  if (items.some((item) => resolveQuoteStatus(item.quote, item.market).closed)) {
    pageStates.push('market_closed');
  }

  const activeFilters: Array<{ key: string; label: string; clear: () => void }> = [];
  if (query) activeFilters.push({ key: 'q', label: `搜索：${query}`, clear: () => updateParams({ q: null, page: null }) });
  if (direction !== 'all') activeFilters.push({ key: 'direction', label: DIRECTIONS.find((item) => item.key === direction)!.label, clear: () => updateParams({ direction: null, page: null }) });
  if (quoteFilter !== 'all') activeFilters.push({ key: 'quote', label: QUOTE_FILTERS.find((item) => item.key === quoteFilter)!.label, clear: () => updateParams({ quote: null, page: null }) });
  if (signal !== 'all') activeFilters.push({ key: 'signal', label: SIGNAL_FILTERS.find((item) => item.key === signal)!.label, clear: () => updateParams({ signal: null, page: null }) });
  if (industry) activeFilters.push({ key: 'industry', label: `行业：${industry}`, clear: () => updateParams({ industry: null, page: null }) });

  const currentLocation = `${pathname}${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;
  const sources = systemStatus.data?.sources ?? [];
  const healthySources = sources.filter((source) => source.status === 'ok').length;
  const scopeLabel = SCOPES.find((item) => item.key === scope)?.label ?? '研究池';

  return (
    <div data-testid="research-pool-page">
      <div className="page-heading">
        <div>
          <h1 className="page-title">股票研究池</h1>
          <p className="page-subtitle">沪深300由系统自动维护；你添加的范围外股票集中在“我的关注”。</p>
        </div>
        <span className="market-summary">
          {market ? MARKET_PHASE_LABELS[market.phase] : '市场状态读取中'}
          {market?.latest_trading_day ? ` · 最近交易日 ${market.latest_trading_day}` : ''}
        </span>
      </div>

      <section className="research-controls" aria-label="研究池范围、搜索与筛选">
        <nav className="scope-tabs" aria-label="研究池范围" data-testid="research-pool-tabs">
          {SCOPES.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`scope-tab ${scope === item.key ? 'scope-tab--active' : ''}`}
              aria-pressed={scope === item.key}
              onClick={() =>
                updateParams({
                  scope: item.key,
                  q: null,
                  direction: null,
                  quote: null,
                  signal: null,
                  industry: null,
                  page: null,
                })
              }
              data-testid={`scope-${item.key}`}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="research-controls__primary">
          <label className="field research-search">
            <span className="field__label">搜索当前研究范围</span>
            <input
              className="field__input"
              type="search"
              value={queryDraft}
              placeholder="输入代码或名称"
              onChange={(event) => setQueryDraft(event.target.value)}
              data-testid="watchlist-search"
            />
          </label>
          {queryDraft ? <button type="button" className="btn btn--ghost" onClick={() => setQueryDraft('')}>清除搜索</button> : null}
          <details className="add-stock-disclosure">
            <summary className="btn btn--primary">添加到我的关注</summary>
            <div className="add-stock-disclosure__body">
              <AddStockPanel
                onAdd={handleAdd}
                addError={addError}
                adding={adding}
                addedSymbol={feedback?.kind === 'success' && !feedback.canUndo ? feedback.symbol : null}
                onResetError={() => setAddError(null)}
              />
            </div>
          </details>
        </div>

        <details className="research-filter-disclosure">
          <summary className="btn btn--ghost">筛选与排序</summary>
          <div className="research-filters">
          <label className="field"><span className="field__label">涨跌</span><select className="field__select" value={direction} onChange={(event) => updateParams({ direction: event.target.value, page: null })}>{DIRECTIONS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          <label className="field"><span className="field__label">行情</span><select className="field__select" value={quoteFilter} onChange={(event) => updateParams({ quote: event.target.value, page: null })}>{QUOTE_FILTERS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          <label className="field"><span className="field__label">研究信号</span><select className="field__select" value={signal} onChange={(event) => updateParams({ signal: event.target.value, page: null })}>{SIGNAL_FILTERS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
          <label className="field"><span className="field__label">行业</span><select className="field__select" value={industry} onChange={(event) => updateParams({ industry: event.target.value, page: null })}><option value="">全部行业</option>{industries.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="field"><span className="field__label">排序</span><select className="field__select" value={`${sortKey}:${sortDirection}`} onChange={(event) => { const [key, order] = event.target.value.split(':') as [ResearchPoolSortKey, SortDirection]; updateParams({ sort: key === 'display_order' ? null : key, order: key === 'display_order' ? null : order, page: null }); }}>{SORT_OPTIONS.map((item) => <option key={`${item.key}:${item.direction}`} value={`${item.key}:${item.direction}`}>{item.label}</option>)}</select></label>
          <label className="field"><span className="field__label">每页</span><select className="field__select field__select--compact" value={pageSize} onChange={(event) => updateParams({ page_size: event.target.value, page: null })}>{[25, 50, 100].map((item) => <option key={item} value={item}>{item}只</option>)}</select></label>
          </div>
        </details>

        {activeFilters.length > 0 ? (
          <div className="active-filters" aria-label="已生效筛选">
            {activeFilters.map((item) => <button key={item.key} type="button" className="filter-chip filter-chip--active" onClick={item.clear}>{item.label} ×</button>)}
            <button type="button" className="btn btn--ghost" onClick={() => updateParams({ q: null, direction: null, quote: null, signal: null, industry: null, page: null })}>清除全部</button>
          </div>
        ) : null}
      </section>

      <StateNoticeList
        states={pageStates}
        details={{
          quote_stale: `${staleItems.length} 只股票的行情已超过120秒未更新，不能作为实时行情使用。`,
          provider_failed: errorMessage(error),
          market_closed: '当前非交易时段，显示最近交易时段行情及其获取时间。',
        }}
      />

      {feedback ? (
        <div className={`action-toast action-toast--${feedback.kind}`} role={feedback.kind === 'error' ? 'alert' : 'status'} aria-live="polite">
          <span>{feedback.message}</span>
          {feedback.canUndo ? <button type="button" className="btn btn--ghost" onClick={handleUndoRemove} disabled={busySymbol === feedback.symbol}>撤销</button> : null}
          {feedback.symbol && !feedback.canUndo ? <a className="btn btn--ghost" href={`/stocks/${feedback.symbol}?return_to=${encodeURIComponent(currentLocation)}`}>查看股票</a> : null}
          <button type="button" className="btn btn--ghost" aria-label="关闭提示" onClick={() => setFeedback(null)}>关闭</button>
        </div>
      ) : null}

      <div className="research-summary" data-testid="workbench-summary">
        <button type="button" className={`summary-card ${direction === 'up' ? 'summary-card--active' : ''}`} onClick={() => updateParams({ direction: direction === 'up' ? null : 'up', page: null })}><span>上涨</span><strong>{risingCount}</strong></button>
        <button type="button" className={`summary-card ${direction === 'down' ? 'summary-card--active' : ''}`} onClick={() => updateParams({ direction: direction === 'down' ? null : 'down', page: null })}><span>下跌</span><strong>{fallingCount}</strong></button>
        <button type="button" className={`summary-card ${quoteFilter === 'abnormal' ? 'summary-card--active' : ''}`} onClick={() => updateParams({ quote: quoteFilter === 'abnormal' ? null : 'abnormal', page: null })}><span>行情异常</span><strong>{delayedItems.length + staleItems.length + unavailableItems.length}</strong><small>延迟 {delayedItems.length} · 过期 {staleItems.length} · 无 {unavailableItems.length}</small></button>
        <button type="button" className={`summary-card ${signal === 'events' ? 'summary-card--active' : ''}`} onClick={() => updateParams({ signal: signal === 'events' ? null : 'events', page: null })}><span>事件股票</span><strong>{eventCount}</strong></button>
        <button type="button" className={`summary-card ${signal === 'waiting' ? 'summary-card--active' : ''}`} onClick={() => updateParams({ signal: signal === 'waiting' ? null : 'waiting', page: null })}><span>待处理研究</span><strong>{waitingCount}</strong></button>
      </div>

      {jobPollError ? <StateNotice state="provider_failed" detail={jobPollError} /> : null}
      {Object.keys(backfillJobs).length > 0 ? (
        <Section id="backfill" title="首次数据回补" subtitle="进度会自动更新，完成后列表将自动读取最新状态。">
          {Object.entries(backfillJobs).map(([symbol, job]) => (
            <div key={symbol} className="backfill-block">
              <h3 className="section__title">{symbol}</h3>
              <BackfillProgress job={job} />
              {job.status === 'failed' ? <button type="button" className="btn" onClick={() => handleRetryBackfill(symbol)} disabled={busySymbol === symbol}>{busySymbol === symbol ? '正在重新发起…' : '重新发起回补'}</button> : null}
            </div>
          ))}
        </Section>
      ) : null}

      <Section
        id="watchlist"
        title={`${scopeLabel}股票`}
        subtitle={`${filteredItems.length} 只符合结构化筛选；${query ? `继续按“${query}”匹配代码或名称。` : `最近行情获取于 ${formatDateTime(latestQuoteAt)}。`}`}
        action={<button type="button" className="btn" onClick={reload} disabled={loading} data-testid="watchlist-refresh">{loading ? '正在读取…' : '重新读取列表'}</button>}
      >
        <div aria-busy={loading}>
          {loading && !loaded ? (
            <div className="skeleton-list" role="status"><span className="sr-only">正在加载{scopeLabel}股票</span>{Array.from({ length: 5 }, (_, index) => <div key={index} className="skeleton-row" />)}</div>
          ) : error ? (
            <StateNotice state={errorState ?? 'provider_failed'} detail={errorMessage(error)} action={<button type="button" className="btn" onClick={reload}>重试</button>} />
          ) : (
            <WatchlistTable
              items={filteredItems}
              onRemove={handleRemove}
              busySymbol={busySymbol}
              rowError={rowErrors}
              query={query}
              onQueryChange={(value) => updateParams({ q: value, page: null })}
              sortKey={sortKey}
              sortDirection={sortDirection}
              onSortChange={(key, order) => updateParams({ sort: key === 'display_order' ? null : key, order: key === 'display_order' ? null : order, page: null })}
              pageNumber={page}
              pageSize={pageSize}
              onPageChange={(value) => updateParams({ page: value === 1 ? null : value })}
              returnTo={currentLocation}
              showSearch={false}
              sourceItemCount={items.length}
            />
          )}
        </div>
      </Section>

      <details className="system-summary">
        <summary>
          {systemStatus.loading && !systemStatus.loaded
            ? '系统状态：读取中'
            : systemStatus.error
              ? '系统状态：读取失败'
              : `系统状态：数据源 ${healthySources}/${sources.length} 正常`}
        </summary>
        {systemStatus.error ? (
          <StateNotice state="provider_failed" detail={errorMessage(systemStatus.error)} action={<button type="button" className="btn" onClick={systemStatus.reload}>重试</button>} />
        ) : (
          <p className="empty-hint">
            {sources.length === 0
              ? '正在读取数据源状态。'
              : sources.map((source) => `${source.name} ${DATA_SOURCE_STATUS_LABELS[source.status]}`).join(' · ')}
          </p>
        )}
      </details>
    </div>
  );
}

export default function WatchlistPage() {
  return (
    <Suspense fallback={<p className="empty-hint">正在加载研究池…</p>}>
      <ResearchPoolPage />
    </Suspense>
  );
}
