'use client';

import Link from 'next/link';
import { Fragment, useMemo, useState } from 'react';

import { UNIVERSE_EXITED_LABEL } from '@/lib/constants';
import {
  changeTone,
  formatAgeSeconds,
  formatCompactNumber,
  formatDateTime,
  formatPrice,
  formatRatioAsPercent,
} from '@/lib/format';
import {
  sortResearchPoolItems,
  type ResearchPoolSortKey,
  type SortDirection,
} from '@/lib/research-pool';
import { isJobRunning, resolveQuoteStatus } from '@/lib/ui-state';
import type { WatchlistItemDTO } from '@/lib/api/types';
import { StateBadge } from './StateNotice';

export interface WatchlistTableProps {
  items: WatchlistItemDTO[];
  onRemove?: (symbol: string) => void;
  onMove?: (symbol: string, direction: 'up' | 'down') => void;
  busySymbol?: string | null;
  rowError?: Record<string, string | undefined>;
  pageNumber?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  hasNextPage?: boolean;
  hasPreviousPage?: boolean;
  onNextPage?: () => void;
  onPreviousPage?: () => void;
  query?: string;
  onQueryChange?: (query: string) => void;
  onSearch?: (query: string) => void;
  sortKey?: ResearchPoolSortKey;
  sortDirection?: SortDirection;
  onSortChange?: (key: ResearchPoolSortKey, direction: SortDirection) => void;
  returnTo?: string;
  showSearch?: boolean;
  /** 筛选前的范围总数，用于区分“范围为空”和“筛选无结果”。 */
  sourceItemCount?: number;
}

const COLUMNS: { key: ResearchPoolSortKey; label: string }[] = [
  { key: 'symbol', label: '股票' },
  { key: 'price', label: '最新价' },
  { key: 'change_percent', label: '涨跌幅' },
  { key: 'amount', label: '成交额' },
  { key: 'freshness', label: '行情状态' },
  { key: 'analysis_updated_at', label: '研究状态' },
];

const ANALYSIS_LABELS: Record<NonNullable<WatchlistItemDTO['analysis_status']>, string> = {
  waiting: '等待触发',
  queued: '分析排队中',
  analyzing: '分析进行中',
  analyzed: '已分析',
  failed: '分析失败',
};

function quoteBadge(item: WatchlistItemDTO, testIds = true) {
  const status = resolveQuoteStatus(item.quote, item.market);
  if (!testIds) {
    if (!item.quote) return <span className="badge badge--neutral">暂无实时行情</span>;
    if (status.closed) return <span className="badge badge--info">休市</span>;
    if (status.delayed) return <span className="badge badge--warning">行情可能延迟</span>;
    if (status.stale) return <span className="badge badge--warning">行情可能已过期</span>;
    return <span className="badge badge--ok">最新</span>;
  }
  if (!item.quote) return <StateBadge state="quote_unavailable" title={status.availabilityLabel} />;
  if (status.closed) return <StateBadge state="market_closed" />;
  if (status.delayed) {
    return (
      <span className="badge badge--warning" data-testid={testIds ? 'badge-delayed' : undefined}>
        行情可能延迟
      </span>
    );
  }
  if (status.stale) {
    return (
      <StateBadge
        state="quote_stale"
        title={`距上次更新 ${formatAgeSeconds(status.ageSeconds)}`}
      />
    );
  }
  return (
    <span className="badge badge--ok" data-testid={testIds ? 'badge-fresh' : undefined}>
      最新
    </span>
  );
}

function detailHref(symbol: string, returnTo?: string): string {
  if (!returnTo) return `/stocks/${symbol}`;
  return `/stocks/${symbol}?return_to=${encodeURIComponent(returnTo)}`;
}

/** 研究池列表：桌面使用精简表格，手机使用重新组织的信息卡片。 */
export function WatchlistTable({
  items,
  onRemove,
  onMove,
  busySymbol,
  rowError = {},
  pageNumber,
  pageSize = 50,
  onPageChange,
  query: controlledQuery,
  onQueryChange,
  onSearch,
  sortKey: controlledSortKey,
  sortDirection: controlledSortDirection,
  onSortChange,
  returnTo,
  showSearch = true,
  sourceItemCount,
}: WatchlistTableProps) {
  const [localQuery, setLocalQuery] = useState('');
  const [localSortKey, setLocalSortKey] = useState<ResearchPoolSortKey>('display_order');
  const [localSortDirection, setLocalSortDirection] = useState<SortDirection>('asc');
  const [localPage, setLocalPage] = useState(1);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const query = controlledQuery ?? localQuery;
  const sortKey = controlledSortKey ?? localSortKey;
  const sortDirection = controlledSortDirection ?? localSortDirection;
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const filtered = normalized
      ? items.filter(
          (item) =>
            item.symbol.toLowerCase().includes(normalized) ||
            (item.name ?? '').toLowerCase().includes(normalized),
        )
      : items;
    return sortResearchPoolItems(filtered, sortKey, sortDirection);
  }, [items, query, sortDirection, sortKey]);

  const totalPages = Math.max(1, Math.ceil(visible.length / pageSize));
  const requestedPage = pageNumber ?? localPage;
  const currentPage = Math.min(Math.max(1, requestedPage), totalPages);
  const pageItems = visible.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  function setPage(next: number) {
    const safe = Math.min(Math.max(1, next), totalPages);
    if (onPageChange) onPageChange(safe);
    else setLocalPage(safe);
  }

  function setQuery(value: string) {
    if (onQueryChange) {
      onQueryChange(value);
    } else {
      setLocalQuery(value);
      setPage(1);
    }
  }

  function toggleSort(key: ResearchPoolSortKey) {
    const direction: SortDirection = key === sortKey && sortDirection === 'asc' ? 'desc' : 'asc';
    if (onSortChange) {
      onSortChange(key, direction);
    } else {
      setLocalSortKey(key);
      setLocalSortDirection(direction);
      setPage(1);
    }
  }

  function toggleExpanded(symbol: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  }

  return (
    <div data-testid="watchlist" aria-busy="false">
      {showSearch ? <form
        className="toolbar research-list__search"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch?.(query.trim());
        }}
      >
        <label className="field">
          <span className="field__label">搜索研究池</span>
          <input
            className="field__input"
            type="search"
            value={query}
            placeholder="按代码或名称筛选"
            onChange={(event) => setQuery(event.target.value)}
            data-testid="watchlist-search"
          />
        </label>
        {query ? (
          <button type="button" className="btn btn--ghost" onClick={() => setQuery('')}>
            清除
          </button>
        ) : null}
        {onSearch ? (
          <button type="submit" className="btn" data-testid="watchlist-search-submit">
            搜索
          </button>
        ) : null}
        <span className="toolbar__count" data-testid="watchlist-count" role="status" aria-live="polite">
          共 {items.length} 只，显示 {visible.length} 只 · 第 {currentPage}/{totalPages} 页
        </span>
      </form> : (
        <span className="toolbar__count research-list__count" data-testid="watchlist-count" role="status" aria-live="polite">
          共 {items.length} 只，显示 {visible.length} 只 · 第 {currentPage}/{totalPages} 页
        </span>
      )}

      <div
        className="table-scroll research-table"
        tabIndex={0}
        role="region"
        aria-label="研究池股票表格，可横向滚动查看更多字段"
      >
        <table className="table" data-testid="watchlist-table">
          <caption className="table__caption sr-only">研究池股票行情与研究状态</caption>
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={column.key === 'symbol' ? 'table__sticky-column' : undefined}
                  aria-sort={
                    sortKey === column.key
                      ? sortDirection === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : 'none'
                  }
                >
                  <button
                    type="button"
                    className="table__sort"
                    onClick={() => toggleSort(column.key)}
                    data-testid={`sort-${column.key}`}
                  >
                    {column.label}
                    {sortKey === column.key ? (
                      <span aria-hidden="true">{sortDirection === 'asc' ? ' ▲' : ' ▼'}</span>
                    ) : null}
                  </button>
                </th>
              ))}
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((item) => {
              const status = resolveQuoteStatus(item.quote, item.market);
              const exited = item.is_current_universe_member === false && item.pool_source !== 'extra';
              const tone = changeTone(item.quote?.change_percent);
              const isExpanded = expanded.has(item.symbol);
              const analysisStatus = item.analysis_status ?? 'waiting';
              const backfilling = isJobRunning(item.backfill_job);
              return (
                <Fragment key={item.symbol}>
                        <tr data-testid="watchlist-row" data-symbol={item.symbol}>
                          <td className="table__sticky-column">
                            <Link
                              href={detailHref(item.symbol, returnTo)}
                              className="stock-link stock-link--identity"
                              data-testid="watchlist-symbol-link"
                            >
                              <strong>{item.name ?? item.symbol}</strong>
                              <small>{item.symbol}</small>
                            </Link>
                            {item.industry ? <span className="stock-industry">{item.industry}</span> : null}
                            <span className="stock-signals">
                              {exited ? (
                                <span className="badge badge--warning" data-testid="row-universe-exited">
                                  {UNIVERSE_EXITED_LABEL}
                                </span>
                              ) : null}
                              {item.pool_source === 'extra' ? (
                                <span className="badge badge--warning" data-testid="row-extra-watchlist">
                                  我的关注
                                </span>
                              ) : null}
                              {item.has_anomaly ? <span className="badge badge--warning">有异动</span> : null}
                              {item.has_documents ? <span className="badge badge--info">有事件</span> : null}
                              {item.has_prediction ? <span className="badge badge--neutral">有预测</span> : null}
                            </span>
                          </td>
                          <td className={`cell--${tone}`} data-testid="row-price">
                            {formatPrice(item.quote?.price)}
                          </td>
                          <td className={`cell--${tone}`} data-testid="row-change-percent">
                            {formatRatioAsPercent(item.quote?.change_percent)}
                          </td>
                          <td>{formatCompactNumber(item.quote?.amount, '元')}</td>
                          <td data-testid="row-freshness">
                            {quoteBadge(item)}
                            {status.ageSeconds !== null ? (
                              <span className="cell__secondary">{formatAgeSeconds(status.ageSeconds)}前</span>
                            ) : null}
                          </td>
                          <td data-testid="row-analysis-status">
                            <span
                              className={`badge ${backfilling ? 'badge--info' : analysisStatus === 'analyzed' ? 'badge--ok' : analysisStatus === 'failed' ? 'badge--danger' : 'badge--neutral'}`}
                            >
                              {backfilling ? '首次回补' : ANALYSIS_LABELS[analysisStatus]}
                            </span>
                            <span className="cell__secondary">{formatDateTime(item.analysis_updated_at)}</span>
                          </td>
                          <td className="cell--actions">
                            <button
                              type="button"
                              className="btn btn--ghost"
                              aria-expanded={isExpanded}
                              onClick={() => toggleExpanded(item.symbol)}
                              data-testid="row-expand"
                            >
                              {isExpanded ? '收起' : '更多指标'}
                            </button>
                            {onMove ? (
                              <>
                                <button
                                  type="button"
                                  className="btn btn--ghost"
                                  onClick={() => onMove(item.symbol, 'up')}
                                  disabled={busySymbol === item.symbol}
                                  aria-label={`将 ${item.symbol} 上移`}
                                  data-testid="row-move-up"
                                >
                                  上移
                                </button>
                                <button
                                  type="button"
                                  className="btn btn--ghost"
                                  onClick={() => onMove(item.symbol, 'down')}
                                  disabled={busySymbol === item.symbol}
                                  aria-label={`将 ${item.symbol} 下移`}
                                  data-testid="row-move-down"
                                >
                                  下移
                                </button>
                              </>
                            ) : null}
                            {item.can_remove !== false && onRemove ? (
                              <button
                                type="button"
                                className="btn btn--danger"
                                onClick={() => onRemove(item.symbol)}
                                disabled={busySymbol === item.symbol}
                                aria-label={`从我的关注移除 ${item.symbol}`}
                                data-testid="row-remove"
                              >
                                {busySymbol === item.symbol ? '移除中…' : '移除'}
                              </button>
                            ) : null}
                          </td>
                        </tr>
                        {isExpanded || rowError[item.symbol] ? (
                          <tr className="research-table__details" data-testid="row-details">
                            <td colSpan={7}>
                              {isExpanded ? (
                                <dl className="row-details-grid">
                                  <div><dt>今开</dt><dd>{formatPrice(item.quote?.open)}</dd></div>
                                  <div><dt>最高</dt><dd>{formatPrice(item.quote?.high)}</dd></div>
                                  <div><dt>最低</dt><dd>{formatPrice(item.quote?.low)}</dd></div>
                                  <div><dt>昨收</dt><dd>{formatPrice(item.quote?.previous_close)}</dd></div>
                                  <div><dt>成交量</dt><dd>{formatCompactNumber(item.quote?.volume, '股')}</dd></div>
                                  <div><dt>行情时间</dt><dd>{formatDateTime(item.quote?.market_time ?? item.quote?.observed_at)}</dd></div>
                                  <div><dt>系统获取</dt><dd>{formatDateTime(item.quote?.fetched_at ?? item.quote?.observed_at)}</dd></div>
                                  <div><dt>异动强度</dt><dd>{item.anomaly_strength == null ? '—' : `${(item.anomaly_strength * 100).toFixed(0)}%`}</dd></div>
                                </dl>
                              ) : null}
                              {rowError[item.symbol] ? (
                                <p className="form-error" role="alert">{rowError[item.symbol]}</p>
                              ) : null}
                            </td>
                          </tr>
                        ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="research-cards" aria-label="研究池股票卡片">
        {pageItems.map((item) => {
          const status = resolveQuoteStatus(item.quote, item.market);
          const analysisStatus = item.analysis_status ?? 'waiting';
          const backfilling = isJobRunning(item.backfill_job);
          return (
            <article className="research-card" key={item.symbol} data-testid="watchlist-mobile-card">
              <Link href={detailHref(item.symbol, returnTo)} className="research-card__link">
                <span><strong>{item.name ?? item.symbol}</strong> <small>{item.symbol}</small></span>
                <span aria-hidden="true">›</span>
              </Link>
              <div className="research-card__price">
                <strong>{formatPrice(item.quote?.price)}</strong>
                <span className={`cell--${changeTone(item.quote?.change_percent)}`}>
                  {formatRatioAsPercent(item.quote?.change_percent)}
                </span>
              </div>
              <dl className="research-card__meta">
                <div><dt>行情</dt><dd>{quoteBadge(item, false)} {status.ageSeconds !== null ? `${formatAgeSeconds(status.ageSeconds)}前` : ''}</dd></div>
                <div><dt>研究</dt><dd>{backfilling ? '首次回补' : ANALYSIS_LABELS[analysisStatus]}</dd></div>
                <div><dt>成交额</dt><dd>{formatCompactNumber(item.quote?.amount, '元')}</dd></div>
                <div><dt>信号</dt><dd>{[item.has_anomaly ? '异动' : '', item.has_documents ? '事件' : '', item.has_prediction ? '预测' : ''].filter(Boolean).join(' · ') || '暂无'}</dd></div>
              </dl>
              <details className="research-card__details">
                <summary>更多指标</summary>
                <p>今开 {formatPrice(item.quote?.open)} · 最高 {formatPrice(item.quote?.high)} · 最低 {formatPrice(item.quote?.low)} · 昨收 {formatPrice(item.quote?.previous_close)}</p>
              </details>
              {item.can_remove !== false && onRemove ? (
                <button type="button" className="btn btn--danger" onClick={() => onRemove(item.symbol)} disabled={busySymbol === item.symbol}>
                  {busySymbol === item.symbol ? '移除中…' : '移出我的关注'}
                </button>
              ) : null}
              {rowError[item.symbol] ? <p className="form-error" role="alert">{rowError[item.symbol]}</p> : null}
            </article>
          );
        })}
      </div>

      {visible.length > pageSize ? (
        <nav className="pagination" aria-label="研究池分页" data-testid="watchlist-pagination">
          <button type="button" className="btn btn--ghost" onClick={() => setPage(currentPage - 1)} disabled={currentPage === 1} data-testid="watchlist-page-prev">
            上一页
          </button>
          <span data-testid="watchlist-page-indicator">第 {currentPage} 页，共 {totalPages} 页</span>
          <button type="button" className="btn btn--ghost" onClick={() => setPage(currentPage + 1)} disabled={currentPage === totalPages} data-testid="watchlist-page-next">
            下一页
          </button>
        </nav>
      ) : null}

      {visible.length === 0 ? (
        <p className="empty-hint" data-testid="watchlist-empty" role="status">
          {(sourceItemCount ?? items.length) === 0
            ? '当前范围暂无股票；沪深300同步完成后会自动显示。'
            : '当前筛选没有匹配的研究池股票，请调整或清除筛选条件。'}
        </p>
      ) : null}
    </div>
  );
}
