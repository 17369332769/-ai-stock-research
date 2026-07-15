'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';

import { UNIVERSE_EXITED_LABEL } from '@/lib/constants';
import { changeTone, formatAgeSeconds, formatDateTime, formatPrice, formatRatioAsPercent } from '@/lib/format';
import { isJobRunning, resolveQuoteStatus } from '@/lib/ui-state';
import type { WatchlistItemDTO } from '@/lib/api/types';
import { StateBadge } from './StateNotice';

export interface WatchlistTableProps {
  items: WatchlistItemDTO[];
  onRemove: (symbol: string) => void;
  onMove?: (symbol: string, direction: 'up' | 'down') => void;
  busySymbol?: string | null;
  pageNumber?: number;
  hasNextPage?: boolean;
  hasPreviousPage?: boolean;
  onNextPage?: () => void;
  onPreviousPage?: () => void;
  onSearch?: (query: string) => void;
}

type SortKey = 'display_order' | 'symbol' | 'name' | 'price' | 'change_percent' | 'freshness';
type SortDirection = 'asc' | 'desc';

const PAGE_SIZE = 50;

const COLUMNS: { key: SortKey; label: string; sortable: boolean }[] = [
  { key: 'display_order', label: '顺序', sortable: true },
  { key: 'symbol', label: '代码', sortable: true },
  { key: 'name', label: '名称', sortable: true },
  { key: 'price', label: '最新价', sortable: true },
  { key: 'change_percent', label: '涨跌幅', sortable: true },
  { key: 'freshness', label: '数据新鲜度', sortable: true },
];

/** 排序取值：只读 API 已给出的字段，不做任何派生计算。 */
function sortValue(item: WatchlistItemDTO, key: SortKey): string | number {
  switch (key) {
    case 'display_order':
      return item.display_order;
    case 'symbol':
      return item.symbol;
    case 'name':
      return item.name ?? item.symbol;
    case 'price':
      return item.quote?.price ?? Number.NEGATIVE_INFINITY;
    case 'change_percent':
      return item.quote?.change_percent ?? Number.NEGATIVE_INFINITY;
    case 'freshness':
      // fresh 排前，stale 其次，无行情最后。
      if (!item.quote) return 2;
      return item.quote.freshness === 'fresh' ? 0 : 1;
  }
}

/** 自选股表格（spec §13.1）：排序、搜索、数据新鲜度。页面不含任何交易入口。 */
export function WatchlistTable({
  items,
  onRemove,
  onMove,
  busySymbol,
  pageNumber,
  hasNextPage,
  hasPreviousPage,
  onNextPage,
  onPreviousPage,
  onSearch,
}: WatchlistTableProps) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('display_order');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [page, setPage] = useState(1);

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const filtered = keyword
      ? items.filter(
          (item) =>
            item.symbol.toLowerCase().includes(keyword) ||
            (item.name ?? '').toLowerCase().includes(keyword),
        )
      : items;

    return [...filtered].sort((left, right) => {
      const a = sortValue(left, sortKey);
      const b = sortValue(right, sortKey);
      let result: number;
      if (typeof a === 'number' && typeof b === 'number') {
        result = a - b;
      } else {
        result = String(a).localeCompare(String(b), 'zh-CN');
      }
      return sortDirection === 'asc' ? result : -result;
    });
  }, [items, query, sortKey, sortDirection]);

  const serverPaged = typeof pageNumber === 'number';
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const currentPage = serverPaged ? pageNumber : Math.min(page, totalPages);
  const pageItems = serverPaged
    ? visible
    : visible.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'));
      setPage(1);
      return;
    }
    setSortKey(key);
    setSortDirection('asc');
    setPage(1);
  }

  return (
    <div data-testid="watchlist">
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch?.(query.trim());
        }}
      >
        <label className="field">
          <span className="field__label">搜索自选股</span>
          <input
            className="field__input"
            type="search"
            value={query}
            placeholder="按代码或名称筛选"
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
            data-testid="watchlist-search"
          />
        </label>
        {onSearch ? (
          <button type="submit" className="btn" data-testid="watchlist-search-submit">
            搜索
          </button>
        ) : null}
        <span className="toolbar__count" data-testid="watchlist-count">
          {serverPaged
            ? `本页 ${visible.length} 只 · 第 ${currentPage} 页`
            : `共 ${items.length} 只，显示 ${visible.length} 只 · 第 ${currentPage}/${totalPages} 页`}
        </span>
      </form>

      <div className="table-scroll">
        <table className="table" data-testid="watchlist-table">
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th key={column.key} scope="col" aria-sort={
                  sortKey === column.key
                    ? sortDirection === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }>
                  {column.sortable ? (
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
                  ) : (
                    column.label
                  )}
                </th>
              ))}
              <th scope="col">行情时间</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((item) => {
              const status = resolveQuoteStatus(item.quote, item.market);
              const backfilling = isJobRunning(item.backfill_job);
              const exited = item.is_current_universe_member === false;
              const tone = changeTone(item.quote?.change_percent);

              return (
                <tr key={item.symbol} data-testid="watchlist-row" data-symbol={item.symbol}>
                  <td>{item.display_order}</td>
                  <td>
                    <Link href={`/stocks/${item.symbol}`} data-testid="watchlist-symbol-link">
                      {item.symbol}
                    </Link>
                  </td>
                  <td>
                    <span>{item.name ?? '—'}</span>
                    {exited ? (
                      <span className="badge badge--warning" data-testid="row-universe-exited">
                        {UNIVERSE_EXITED_LABEL}
                      </span>
                    ) : null}
                  </td>
                  <td className={`cell--${tone}`} data-testid="row-price">
                    {formatPrice(item.quote?.price)}
                  </td>
                  <td className={`cell--${tone}`} data-testid="row-change-percent">
                    {formatRatioAsPercent(item.quote?.change_percent)}
                  </td>
                  <td data-testid="row-freshness">
                    {backfilling ? (
                      <StateBadge state="initial_backfill" />
                    ) : !item.quote ? (
                      <StateBadge state="quote_unavailable" title={status.availabilityLabel} />
                    ) : status.closed ? (
                      <StateBadge state="market_closed" />
                    ) : status.stale ? (
                      <StateBadge
                        state="quote_stale"
                        title={`距上次更新 ${formatAgeSeconds(status.ageSeconds)}`}
                      />
                    ) : (
                      <span className="badge badge--ok" data-testid="badge-fresh">
                        新鲜
                      </span>
                    )}
                  </td>
                  <td data-testid="row-observed-at">{formatDateTime(item.quote?.observed_at)}</td>
                  <td className="cell--actions">
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
                    <button
                      type="button"
                      className="btn btn--danger"
                      onClick={() => onRemove(item.symbol)}
                      disabled={busySymbol === item.symbol}
                      aria-label={`从自选股移除 ${item.symbol}`}
                      data-testid="row-remove"
                    >
                      移除
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {serverPaged && (hasPreviousPage || hasNextPage) ? (
        <nav className="pagination" aria-label="自选股分页" data-testid="watchlist-pagination">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onPreviousPage}
            disabled={!hasPreviousPage}
            data-testid="watchlist-page-prev"
          >
            上一页
          </button>
          <span data-testid="watchlist-page-indicator">第 {currentPage} 页</span>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onNextPage}
            disabled={!hasNextPage}
            data-testid="watchlist-page-next"
          >
            下一页
          </button>
        </nav>
      ) : !serverPaged && visible.length > PAGE_SIZE ? (
        <nav className="pagination" aria-label="自选股分页" data-testid="watchlist-pagination">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={currentPage === 1}
            data-testid="watchlist-page-prev"
          >
            上一页
          </button>
          <span data-testid="watchlist-page-indicator">
            第 {currentPage} 页，共 {totalPages} 页
          </span>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            disabled={currentPage === totalPages}
            data-testid="watchlist-page-next"
          >
            下一页
          </button>
        </nav>
      ) : null}

      {visible.length === 0 ? (
        <p className="empty-hint" data-testid="watchlist-empty">
          {items.length === 0 ? '自选股为空，请从沪深300成分股中添加。' : '没有匹配的自选股。'}
        </p>
      ) : null}
    </div>
  );
}
