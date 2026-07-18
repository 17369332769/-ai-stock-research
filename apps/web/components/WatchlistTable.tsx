'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  DownOutlined,
  RightOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { Button, Card, Descriptions, Empty, Input, Pagination, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

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
  sourceItemCount?: number;
}

const ANALYSIS_LABELS: Record<NonNullable<WatchlistItemDTO['analysis_status']>, string> = {
  waiting: '等待触发',
  queued: '分析排队中',
  analyzing: '分析进行中',
  analyzed: '已分析',
  failed: '分析失败',
};

function quoteBadge(item: WatchlistItemDTO, testIds = true) {
  const status = resolveQuoteStatus(item.quote, item.market);
  if (!item.quote) return testIds ? <StateBadge state="quote_unavailable" title={status.availabilityLabel} /> : <Tag>暂无实时行情</Tag>;
  if (status.closed) return testIds ? <StateBadge state="market_closed" /> : <Tag color="blue">休市</Tag>;
  if (status.delayed) return <Tag color="warning" data-testid={testIds ? 'badge-delayed' : undefined}>行情可能延迟</Tag>;
  if (status.stale) return testIds ? <StateBadge state="quote_stale" title={`距上次更新 ${formatAgeSeconds(status.ageSeconds)}`} /> : <Tag color="warning">行情可能已过期</Tag>;
  return <Tag color="success" data-testid={testIds ? 'badge-fresh' : undefined}>最新</Tag>;
}

function detailHref(symbol: string, returnTo?: string): string {
  if (!returnTo) return `/stocks/${symbol}`;
  return `/stocks/${symbol}?return_to=${encodeURIComponent(returnTo)}`;
}

function changeClass(value: number | null | undefined) {
  return `cell--${changeTone(value)}`;
}

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
      ? items.filter((item) => item.symbol.toLowerCase().includes(normalized) || (item.name ?? '').toLowerCase().includes(normalized))
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
    if (onQueryChange) onQueryChange(value);
    else {
      setLocalQuery(value);
      setPage(1);
    }
  }

  function toggleSort(key: ResearchPoolSortKey) {
    const direction: SortDirection = key === sortKey && sortDirection === 'asc' ? 'desc' : 'asc';
    if (onSortChange) onSortChange(key, direction);
    else {
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

  function columnTitle(key: ResearchPoolSortKey, label: string) {
    const active = sortKey === key;
    return (
      <Button
        type="text"
        size="small"
        className="table__sort"
        onClick={() => toggleSort(key)}
        data-testid={`sort-${key}`}
        icon={active ? (sortDirection === 'asc' ? <ArrowUpOutlined /> : <ArrowDownOutlined />) : undefined}
        iconPlacement="end"
      >
        {label}
      </Button>
    );
  }

  const columns: ColumnsType<WatchlistItemDTO> = [
    {
      key: 'symbol',
      title: columnTitle('symbol', '股票'),
      width: 170,
      render: (_, item) => {
        const exited = item.is_current_universe_member === false && item.pool_source !== 'extra';
        return (
          <div className="stock-identity">
            <Link href={detailHref(item.symbol, returnTo)} className="stock-link stock-link--identity" data-testid="watchlist-symbol-link">
              <strong>{item.name ?? item.symbol}</strong><small>{item.symbol}</small>
            </Link>
            {item.industry ? <Typography.Text type="secondary" className="stock-industry">{item.industry}</Typography.Text> : null}
            <Space size={[4, 4]} wrap className="stock-signals">
              {exited ? <Tag color="warning" data-testid="row-universe-exited">{UNIVERSE_EXITED_LABEL}</Tag> : null}
              {item.pool_source === 'extra' ? <Tag color="blue" data-testid="row-extra-watchlist">我的关注</Tag> : null}
              {item.has_anomaly ? <Tag color="warning">有异动</Tag> : null}
              {item.has_documents ? <Tag color="processing">有事件</Tag> : null}
              {item.has_prediction ? <Tag>有预测</Tag> : null}
            </Space>
          </div>
        );
      },
    },
    {
      key: 'price', title: columnTitle('price', '最新价'), width: 110,
      onCell: () => ({ 'data-testid': 'row-price' } as React.HTMLAttributes<HTMLElement>),
      render: (_, item) => <Typography.Text strong className={changeClass(item.quote?.change_percent)}>{formatPrice(item.quote?.price)}</Typography.Text>,
    },
    {
      key: 'change_percent', title: columnTitle('change_percent', '涨跌幅'), width: 110,
      onCell: () => ({ 'data-testid': 'row-change-percent' } as React.HTMLAttributes<HTMLElement>),
      render: (_, item) => <Typography.Text strong className={changeClass(item.quote?.change_percent)}>{formatRatioAsPercent(item.quote?.change_percent)}</Typography.Text>,
    },
    {
      key: 'amount', title: columnTitle('amount', '成交额'), width: 120,
      render: (_, item) => formatCompactNumber(item.quote?.amount, '元'),
    },
    {
      key: 'freshness', title: columnTitle('freshness', '行情状态'), width: 160,
      onCell: () => ({ 'data-testid': 'row-freshness' } as React.HTMLAttributes<HTMLElement>),
      render: (_, item) => {
        const status = resolveQuoteStatus(item.quote, item.market);
        return <Space orientation="vertical" size={2}>{quoteBadge(item)}{status.ageSeconds !== null ? <Typography.Text type="secondary">{formatAgeSeconds(status.ageSeconds)}前</Typography.Text> : null}</Space>;
      },
    },
    {
      key: 'analysis_updated_at', title: columnTitle('analysis_updated_at', '研究状态'), width: 160,
      onCell: () => ({ 'data-testid': 'row-analysis-status' } as React.HTMLAttributes<HTMLElement>),
      render: (_, item) => {
        const analysisStatus = item.analysis_status ?? 'waiting';
        const backfilling = isJobRunning(item.backfill_job);
        const color = backfilling ? 'processing' : analysisStatus === 'analyzed' ? 'success' : analysisStatus === 'failed' ? 'error' : 'default';
        return <Space orientation="vertical" size={2}><Tag color={color}>{backfilling ? '首次回补' : ANALYSIS_LABELS[analysisStatus]}</Tag><Typography.Text type="secondary">{formatDateTime(item.analysis_updated_at)}</Typography.Text></Space>;
      },
    },
    {
      key: 'actions', title: '操作', width: 245,
      render: (_, item) => {
        const isExpanded = expanded.has(item.symbol);
        return (
          <Space size={4} wrap>
            <Button type="link" icon={isExpanded ? <DownOutlined /> : <RightOutlined />} aria-expanded={isExpanded} onClick={() => toggleExpanded(item.symbol)} data-testid="row-expand">
              {isExpanded ? '收起' : '更多指标'}
            </Button>
            {onMove ? <>
              <Button type="text" icon={<ArrowUpOutlined />} onClick={() => onMove(item.symbol, 'up')} disabled={busySymbol === item.symbol} aria-label={`将 ${item.symbol} 上移`} data-testid="row-move-up" />
              <Button type="text" icon={<ArrowDownOutlined />} onClick={() => onMove(item.symbol, 'down')} disabled={busySymbol === item.symbol} aria-label={`将 ${item.symbol} 下移`} data-testid="row-move-down" />
            </> : null}
            {item.can_remove !== false && onRemove ? <Button danger type="text" icon={<DeleteOutlined />} onClick={() => onRemove(item.symbol)} disabled={busySymbol === item.symbol} loading={busySymbol === item.symbol} aria-label={`从我的关注移除 ${item.symbol}`} data-testid="row-remove">移除</Button> : null}
          </Space>
        );
      },
    },
  ];

  const expandedRowKeys = [...new Set([...expanded, ...Object.keys(rowError).filter((key) => rowError[key])])];

  return (
    <div data-testid="watchlist" aria-busy="false">
      {showSearch ? (
        <form className="toolbar research-list__search" role="search" onSubmit={(event) => { event.preventDefault(); onSearch?.(query.trim()); }}>
          <label className="field">
            <span className="field__label">搜索研究池</span>
            <Input size="large" prefix={<SearchOutlined />} value={query} placeholder="按代码或名称筛选" onChange={(event) => setQuery(event.target.value)} data-testid="watchlist-search" allowClear />
          </label>
          {onSearch ? <Button htmlType="submit" type="primary" size="large" icon={<SearchOutlined />} data-testid="watchlist-search-submit">搜索</Button> : null}
          <Typography.Text type="secondary" className="toolbar__count" data-testid="watchlist-count" role="status" aria-live="polite">共 {items.length} 只，显示 {visible.length} 只 · 第 {currentPage}/{totalPages} 页</Typography.Text>
        </form>
      ) : (
        <Typography.Text type="secondary" className="toolbar__count research-list__count" data-testid="watchlist-count" role="status" aria-live="polite">共 {items.length} 只，显示 {visible.length} 只 · 第 {currentPage}/{totalPages} 页</Typography.Text>
      )}

      <div className="research-table" data-testid="watchlist-table">
        <Table<WatchlistItemDTO>
          rowKey="symbol"
          size="middle"
          columns={columns}
          dataSource={pageItems}
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无股票" /> }}
          onRow={(item) => ({ 'data-testid': 'watchlist-row', 'data-symbol': item.symbol } as React.HTMLAttributes<HTMLTableRowElement>)}
          expandable={{
            showExpandColumn: false,
            expandedRowKeys,
            expandedRowRender: (item) => (
              <div className="row-details" data-testid="row-details">
                <Descriptions size="small" column={{ xs: 2, sm: 4, md: 8 }}
                  items={[
                    { key: 'open', label: '今开', children: formatPrice(item.quote?.open) },
                    { key: 'high', label: '最高', children: formatPrice(item.quote?.high) },
                    { key: 'low', label: '最低', children: formatPrice(item.quote?.low) },
                    { key: 'previous', label: '昨收', children: formatPrice(item.quote?.previous_close) },
                    { key: 'volume', label: '成交量', children: formatCompactNumber(item.quote?.volume, '股') },
                    { key: 'market', label: '行情时间', children: formatDateTime(item.quote?.market_time ?? item.quote?.observed_at) },
                    { key: 'fetched', label: '系统获取', children: formatDateTime(item.quote?.fetched_at ?? item.quote?.observed_at) },
                    { key: 'anomaly', label: '异动强度', children: item.anomaly_strength == null ? '—' : `${(item.anomaly_strength * 100).toFixed(0)}%` },
                  ]}
                />
                {rowError[item.symbol] ? <Typography.Text type="danger" role="alert">{rowError[item.symbol]}</Typography.Text> : null}
              </div>
            ),
          }}
        />
      </div>

      <div className="research-cards" aria-label="研究池股票卡片">
        {pageItems.map((item) => {
          const status = resolveQuoteStatus(item.quote, item.market);
          const analysisStatus = item.analysis_status ?? 'waiting';
          const backfilling = isJobRunning(item.backfill_job);
          return (
            <Card className="research-card" key={item.symbol} data-testid="watchlist-mobile-card" size="small">
              <Link href={detailHref(item.symbol, returnTo)} className="research-card__link">
                <span><strong>{item.name ?? item.symbol}</strong> <small>{item.symbol}</small></span><RightOutlined />
              </Link>
              <div className="research-card__price"><Typography.Title level={3}>{formatPrice(item.quote?.price)}</Typography.Title><Typography.Text strong className={changeClass(item.quote?.change_percent)}>{formatRatioAsPercent(item.quote?.change_percent)}</Typography.Text></div>
              <Descriptions size="small" column={1} items={[
                { key: 'quote', label: '行情', children: <Space wrap>{quoteBadge(item, false)}{status.ageSeconds !== null ? `${formatAgeSeconds(status.ageSeconds)}前` : ''}</Space> },
                { key: 'analysis', label: '研究', children: backfilling ? '首次回补' : ANALYSIS_LABELS[analysisStatus] },
                { key: 'amount', label: '成交额', children: formatCompactNumber(item.quote?.amount, '元') },
                { key: 'signals', label: '信号', children: [item.has_anomaly ? '异动' : '', item.has_documents ? '事件' : '', item.has_prediction ? '预测' : ''].filter(Boolean).join(' · ') || '暂无' },
              ]} />
              <details className="research-card__details"><summary>更多指标</summary><p>今开 {formatPrice(item.quote?.open)} · 最高 {formatPrice(item.quote?.high)} · 最低 {formatPrice(item.quote?.low)} · 昨收 {formatPrice(item.quote?.previous_close)}</p></details>
              {item.can_remove !== false && onRemove ? <Button danger block icon={<DeleteOutlined />} onClick={() => onRemove(item.symbol)} disabled={busySymbol === item.symbol} loading={busySymbol === item.symbol}>移出我的关注</Button> : null}
              {rowError[item.symbol] ? <Typography.Text type="danger" role="alert">{rowError[item.symbol]}</Typography.Text> : null}
            </Card>
          );
        })}
      </div>

      {visible.length > pageSize ? (
        <nav className="pagination" aria-label="研究池分页" data-testid="watchlist-pagination">
          <Button onClick={() => setPage(currentPage - 1)} disabled={currentPage === 1} data-testid="watchlist-page-prev">上一页</Button>
          <span data-testid="watchlist-page-indicator">第 {currentPage} 页，共 {totalPages} 页</span>
          <Pagination current={currentPage} total={visible.length} pageSize={pageSize} showSizeChanger={false} onChange={setPage} responsive />
          <Button onClick={() => setPage(currentPage + 1)} disabled={currentPage === totalPages} data-testid="watchlist-page-next">下一页</Button>
        </nav>
      ) : null}

      {visible.length === 0 ? (
        <div className="empty-hint" data-testid="watchlist-empty" role="status">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={(sourceItemCount ?? items.length) === 0 ? '当前范围暂无股票；沪深300同步完成后会自动显示。' : '当前筛选没有匹配的研究池股票，请调整或清除筛选条件。'} />
        </div>
      ) : null}
    </div>
  );
}
