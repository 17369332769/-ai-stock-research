import type { ReactNode } from 'react';

import { MARKET_PHASE_LABELS, STALE_THRESHOLD_SECONDS } from '@/lib/constants';
import { changeTone, formatAgeSeconds, formatDate, formatDateTime, formatPrice, formatRatioAsPercent } from '@/lib/format';
import { resolveQuoteStatus } from '@/lib/ui-state';
import type { MarketDTO, QuoteDTO, RelativeStrengthDTO } from '@/lib/api/types';
import { StateBadge } from './StateNotice';
import { SourceDisplay } from './SourceDisplay';

export interface QuoteHeaderProps {
  symbol: string;
  name: string;
  quote: QuoteDTO | null | undefined;
  market: MarketDTO | null | undefined;
  relativeStrength?: RelativeStrengthDTO | null;
  /** 已调出沪深300（spec §3.1）。 */
  exited?: boolean;
  missingAction?: ReactNode;
}

/**
 * 研究页顶部（spec §3.2）：最新价、涨跌幅、行情时间、数据源、新鲜度。
 *
 * 红线：
 *  - freshness=stale 时不得标"实时"，并展示 age_seconds（由 API 给出）。
 *  - 实时行情缺失时只显示明确的空状态，不使用历史价格替代。
 */
export function QuoteHeader({
  symbol,
  name,
  quote,
  market,
  relativeStrength,
  exited,
  missingAction,
}: QuoteHeaderProps) {
  const status = resolveQuoteStatus(quote, market);
  const tone = changeTone(quote?.change_percent);

  return (
    <header className="quote-header" data-testid="quote-header">
      <div className="quote-header__identity">
        <h1 className="quote-header__name">
          {name}
          <span className="quote-header__symbol">{symbol}</span>
        </h1>
        <div className="quote-header__badges">
          {market ? (
            <span className="badge badge--neutral" data-testid="market-phase">
              {MARKET_PHASE_LABELS[market.phase]}
            </span>
          ) : null}
          {status.closed ? <StateBadge state="market_closed" /> : null}
          {status.stale ? <StateBadge state="quote_stale" /> : null}
          {status.isRealtime ? (
            <span className="badge badge--ok" data-testid="badge-realtime">
              实时
            </span>
          ) : null}
          {exited ? (
            <span className="badge badge--warning" data-testid="badge-universe-exited">
              已调出沪深300
            </span>
          ) : null}
        </div>
      </div>

      {quote ? (
        <div className="quote-header__price-row">
          <div className="quote-header__price-block">
            <span className="quote-header__price-label" data-testid="price-label">
              {status.priceLabel}
            </span>
            <span
              className={`quote-header__price quote-header__price--${tone}`}
              data-testid="quote-price"
            >
              {formatPrice(quote.price)}
            </span>
            <span
              className={`quote-header__change quote-header__change--${tone}`}
              data-testid="quote-change-percent"
            >
              {formatRatioAsPercent(quote.change_percent)}
            </span>
          </div>

          <dl className="quote-header__meta">
            <div>
              <dt>行情时间</dt>
              <dd data-testid="quote-market-time">
                {quote.market_time ? formatDateTime(quote.market_time) : '上游未提供'}
              </dd>
            </div>
            <div>
              <dt>获取时间</dt>
              <dd data-testid="quote-observed-at">
                {formatDateTime(quote.fetched_at ?? quote.observed_at)}
              </dd>
            </div>
            <div>
              <dt>数据源</dt>
              <dd data-testid="quote-source">
                <SourceDisplay
                  source={quote.source}
                  sourceUrl={quote.source_url}
                  dataType={
                    status.isRealtime
                      ? '实时行情'
                      : status.closed
                        ? '最近可用行情'
                        : '历史报价'
                  }
                />
              </dd>
            </div>
            <div>
              <dt>数据年龄</dt>
              <dd data-testid="quote-freshness">
                {status.stale
                  ? `已过期（距上次更新 ${formatAgeSeconds(status.ageSeconds)}，阈值 ${STALE_THRESHOLD_SECONDS} 秒）`
                  : '3 分钟内（新鲜）'}
              </dd>
            </div>
            {status.closed && market ? (
              <div>
                <dt>最新交易日</dt>
                <dd data-testid="latest-trading-day">{formatDate(market.latest_trading_day)}</dd>
              </div>
            ) : null}
            {relativeStrength ? (
              <div>
                <dt>相对{relativeStrength.benchmark}</dt>
                <dd data-testid="relative-strength">
                  个股 {formatRatioAsPercent(relativeStrength.stock_change_percent)} / 基准{' '}
                  {formatRatioAsPercent(relativeStrength.benchmark_change_percent)}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : (
        <div className="quote-header__empty" data-testid="quote-missing">
          <p className="empty-hint">
            {status.availabilityLabel}。当前还没有取得这只股票的最新报价，历史行情仍可正常查看。
          </p>
          {missingAction}
        </div>
      )}
    </header>
  );
}
