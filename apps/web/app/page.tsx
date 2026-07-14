'use client';

import { useCallback, useMemo, useState } from 'react';

import { AddStockPanel } from '@/components/AddStockPanel';
import { BackfillProgress } from '@/components/BackfillProgress';
import { Section } from '@/components/Section';
import { StateNotice, StateNoticeList } from '@/components/StateNotice';
import { WatchlistTable } from '@/components/WatchlistTable';
import {
  addWatchlistItem,
  getJob,
  getWatchlist,
  removeWatchlistItem,
  reorderWatchlist,
} from '@/lib/api/endpoints';
import type { JobDTO, WatchlistItemDTO } from '@/lib/api/types';
import { errorMessage } from '@/lib/error-messages';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { formatAgeSeconds } from '@/lib/format';
import { isJobRunning, mapErrorToState, resolveQuoteStatus, type UiState } from '@/lib/ui-state';

/** 自选股页（spec §13.1）：排序、搜索、数据新鲜度。无任何交易入口。 */
export default function WatchlistPage() {
  const { data, error, loading, loaded, reload } = useApiResource(
    async () => (await getWatchlist()).items,
    [],
  );

  const [addError, setAddError] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);
  const [busySymbol, setBusySymbol] = useState<string | null>(null);
  const [backfillJobs, setBackfillJobs] = useState<Record<string, JobDTO>>({});

  const items = useMemo<WatchlistItemDTO[]>(() => data ?? [], [data]);

  const handleAdd = useCallback(
    async (symbol: string) => {
      setAdding(true);
      setAddError(null);
      try {
        const response = await addWatchlistItem(symbol);
        const job = response.data?.backfill_job ?? null;
        // 202 + 回补作业 → 进入"首次回补"状态（spec §7.1）
        if (job) {
          setBackfillJobs((current) => ({ ...current, [symbol]: job }));
        }
        reload();
      } catch (cause) {
        setAddError(cause);
      } finally {
        setAdding(false);
      }
    },
    [reload],
  );

  const handleRemove = useCallback(
    async (symbol: string) => {
      setBusySymbol(symbol);
      try {
        await removeWatchlistItem(symbol);
        reload();
      } catch (cause) {
        setAddError(cause);
      } finally {
        setBusySymbol(null);
      }
    },
    [reload],
  );

  const handleMove = useCallback(
    async (symbol: string, direction: 'up' | 'down') => {
      const ordered = [...items].sort((a, b) => a.display_order - b.display_order);
      const index = ordered.findIndex((item) => item.symbol === symbol);
      const target = direction === 'up' ? index - 1 : index + 1;
      if (index < 0 || target < 0 || target >= ordered.length) return;

      const next = [...ordered];
      const moved = next[index];
      const swapped = next[target];
      if (!moved || !swapped) return;
      next[index] = swapped;
      next[target] = moved;

      setBusySymbol(symbol);
      try {
        await reorderWatchlist(next.map((item) => item.symbol));
        reload();
      } catch (cause) {
        setAddError(cause);
      } finally {
        setBusySymbol(null);
      }
    },
    [items, reload],
  );

  const refreshJob = useCallback(async (symbol: string, jobId: string) => {
    try {
      const job = await getJob(jobId);
      setBackfillJobs((current) => ({ ...current, [symbol]: job }));
    } catch {
      // 作业查询失败不影响自选股列表展示。
    }
  }, []);

  // 页面级状态：数据源失败（424 / 网络）等
  const pageStates: UiState[] = [];
  const errorState = mapErrorToState(error);
  if (errorState) pageStates.push(errorState);

  // 行情过期：只要有任一自选股 API 标记 stale，就在页面级提示（spec §3.2 红线）
  const staleItems = items.filter((item) => resolveQuoteStatus(item.quote, item.market).stale);
  if (staleItems.length > 0 && !pageStates.includes('quote_stale')) pageStates.push('quote_stale');

  const closedMarket = items.some((item) => resolveQuoteStatus(item.quote, item.market).closed);
  if (closedMarket && !pageStates.includes('market_closed')) pageStates.push('market_closed');

  const runningJobs = Object.entries(backfillJobs).filter(([, job]) => isJobRunning(job));
  const inlineJobs = [
    ...runningJobs,
    ...items
      .filter((item) => isJobRunning(item.backfill_job))
      .map((item) => [item.symbol, item.backfill_job as JobDTO] as const),
  ];
  const uniqueJobs = new Map<string, JobDTO>(inlineJobs as Iterable<[string, JobDTO]>);

  return (
    <div>
      <h1 className="page-title">自选股</h1>
      <p className="page-subtitle">
        仅支持沪深300当前成分股。本页只做研究记录，不提供任何交易功能。
      </p>

      <StateNoticeList
        states={pageStates}
        details={{
          quote_stale: `${staleItems.length} 只自选股的行情已超过 180 秒未更新，不能作为实时行情使用。`,
          provider_failed: errorMessage(error),
          market_closed: '当前非交易时段，展示最新交易日的收盘数据。',
        }}
      />

      <Section id="add-stock" title="添加自选股">
        <AddStockPanel onAdd={handleAdd} addError={addError} adding={adding} />
      </Section>

      {uniqueJobs.size > 0 ? (
        <Section id="backfill" title="首次回补进行中">
          <StateNotice
            state="initial_backfill"
            detail="回补完成前不显示预测（spec §3.1）。"
          />
          {[...uniqueJobs.entries()].map(([symbol, job]) => (
            <div key={symbol} className="backfill-block">
              <h3 className="section__title">{symbol}</h3>
              <BackfillProgress job={job} />
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => refreshJob(symbol, job.id)}
                data-testid={`refresh-job-${symbol}`}
              >
                刷新进度
              </button>
            </div>
          ))}
        </Section>
      ) : null}

      <Section
        id="watchlist"
        title="自选股列表"
        subtitle={
          staleItems.length > 0
            ? `其中 ${staleItems.length} 只行情已过期（最长 ${formatAgeSeconds(
                Math.max(...staleItems.map((item) => item.quote?.age_seconds ?? 0)),
              )} 未更新）`
            : undefined
        }
        action={
          <button type="button" className="btn" onClick={reload} data-testid="watchlist-refresh">
            刷新
          </button>
        }
      >
        {loading && !loaded ? (
          <p className="empty-hint">加载中…</p>
        ) : error ? (
          <StateNotice
            state={errorState ?? 'provider_failed'}
            detail={errorMessage(error)}
            action={
              <button type="button" className="btn" onClick={reload}>
                重试
              </button>
            }
          />
        ) : (
          <WatchlistTable
            items={items}
            onRemove={handleRemove}
            onMove={handleMove}
            busySymbol={busySymbol}
          />
        )}
      </Section>
    </div>
  );
}
