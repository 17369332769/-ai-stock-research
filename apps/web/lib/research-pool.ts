import type { WatchlistItemDTO } from './api/types';
import { isMarketClosed } from './ui-state';

export type ResearchDirection = 'all' | 'up' | 'down' | 'flat';
export type ResearchQuoteFilter =
  | 'all'
  | 'abnormal'
  | 'latest'
  | 'delayed'
  | 'stale'
  | 'unavailable';
export type ResearchSignalFilter =
  | 'all'
  | 'events'
  | 'anomaly'
  | 'documents'
  | 'prediction'
  | 'waiting';
export type ResearchPoolSortKey =
  | 'display_order'
  | 'symbol'
  | 'name'
  | 'price'
  | 'change_percent'
  | 'amount'
  | 'freshness'
  | 'anomaly_strength'
  | 'analysis_updated_at';
export type SortDirection = 'asc' | 'desc';

export interface ResearchPoolFilters {
  query: string;
  direction: ResearchDirection;
  quote: ResearchQuoteFilter;
  signal: ResearchSignalFilter;
  industry: string;
}

type QuoteAgeFilterValue = Exclude<ResearchQuoteFilter, 'all' | 'abnormal'>;

export function quoteFilterValue(item: WatchlistItemDTO): QuoteAgeFilterValue {
  if (!item.quote) return 'unavailable';
  if (isMarketClosed(item.market)) return 'latest';
  return item.quote.age_status ?? (item.quote.freshness === 'stale' ? 'stale' : 'latest');
}

export function matchesResearchPoolFilters(
  item: WatchlistItemDTO,
  filters: ResearchPoolFilters,
): boolean {
  const query = filters.query.trim().toLowerCase();
  if (
    query &&
    !item.symbol.toLowerCase().includes(query) &&
    !(item.name ?? '').toLowerCase().includes(query)
  ) {
    return false;
  }

  const change = item.quote?.change_percent;
  if (filters.direction === 'up' && !(change !== undefined && change !== null && change > 0)) {
    return false;
  }
  if (filters.direction === 'down' && !(change !== undefined && change !== null && change < 0)) {
    return false;
  }
  if (filters.direction === 'flat' && change !== 0) return false;

  const quoteValue = quoteFilterValue(item);
  if (filters.quote === 'abnormal' && quoteValue === 'latest') return false;
  if (filters.quote !== 'all' && filters.quote !== 'abnormal' && quoteValue !== filters.quote) {
    return false;
  }
  if (filters.industry && item.industry !== filters.industry) return false;

  if (filters.signal === 'anomaly' && !item.has_anomaly) return false;
  if (filters.signal === 'events' && !item.has_anomaly && !item.has_documents) return false;
  if (filters.signal === 'documents' && !item.has_documents) return false;
  if (filters.signal === 'prediction' && !item.has_prediction) return false;
  if (
    filters.signal === 'waiting' &&
    !['waiting', 'queued', 'analyzing', 'failed'].includes(item.analysis_status ?? 'waiting')
  ) {
    return false;
  }
  return true;
}

function sortValue(item: WatchlistItemDTO, key: ResearchPoolSortKey): string | number {
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
    case 'amount':
      return item.quote?.amount ?? Number.NEGATIVE_INFINITY;
    case 'freshness': {
      return item.quote?.data_age_seconds ?? item.quote?.age_seconds ?? Number.POSITIVE_INFINITY;
    }
    case 'anomaly_strength':
      return item.anomaly_strength ?? Number.NEGATIVE_INFINITY;
    case 'analysis_updated_at':
      return item.analysis_updated_at ?? '';
  }
}

export function sortResearchPoolItems(
  items: readonly WatchlistItemDTO[],
  key: ResearchPoolSortKey,
  direction: SortDirection,
): WatchlistItemDTO[] {
  return [...items].sort((left, right) => {
    const a = sortValue(left, key);
    const b = sortValue(right, key);
    const compared =
      typeof a === 'number' && typeof b === 'number'
        ? a - b
        : String(a).localeCompare(String(b), 'zh-CN');
    return direction === 'asc' ? compared : -compared;
  });
}
