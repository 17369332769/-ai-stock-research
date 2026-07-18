import type { ReactNode } from 'react';

import { MARKET_PHASE_LABELS, STALE_THRESHOLD_SECONDS } from '@/lib/constants';
import { changeTone, formatAgeSeconds, formatCompactNumber, formatDate, formatDateTime, formatPrice, formatRatioAsPercent } from '@/lib/format';
import { resolveQuoteStatus } from '@/lib/ui-state';
import type { MarketDTO, QuoteDTO, RelativeStrengthDTO } from '@/lib/api/types';
import { StateBadge } from './StateNotice';
import { SourceDisplay } from './SourceDisplay';
import { StockOutlined } from '@ant-design/icons';
import { Card, Space, Tag, Typography } from 'antd';

export interface QuoteHeaderProps {
  symbol: string;
  name: string;
  quote: QuoteDTO | null | undefined;
  market: MarketDTO | null | undefined;
  relativeStrength?: RelativeStrengthDTO | null;
  /** 已调出沪深300（spec §3.1）。 */
  exited?: boolean;
  missingAction?: ReactNode;
  /** 已有行情但延迟/过期时显示的上游刷新操作。 */
  refreshAction?: ReactNode;
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
  refreshAction,
}: QuoteHeaderProps) {
  const status = resolveQuoteStatus(quote, market);
  const tone = changeTone(quote?.change_percent);

  return (
    <Card className="quote-header" data-testid="quote-header">
      <Typography.Text type="secondary" className="quote-header__eyebrow"><StockOutlined /> 股票研究概览</Typography.Text>
      <div className="quote-header__identity">
        <Typography.Title className="quote-header__name">
          {name}
          <span className="quote-header__symbol">{symbol}</span>
        </Typography.Title>
        <Space wrap className="quote-header__badges">
          {market ? (
            <Tag data-testid="market-phase">
              {MARKET_PHASE_LABELS[market.phase]}
            </Tag>
          ) : null}
          {status.closed ? <StateBadge state="market_closed" /> : null}
          {status.stale ? <StateBadge state="quote_stale" /> : null}
          {status.delayed ? (
            <Tag color="warning" data-testid="badge-delayed">
              行情可能延迟
            </Tag>
          ) : null}
          {status.isRealtime ? (
            <Tag color="success" data-testid="badge-realtime">
              实时
            </Tag>
          ) : null}
          {exited ? (
            <Tag color="warning" data-testid="badge-universe-exited">
              已调出沪深300
            </Tag>
          ) : null}
        </Space>
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
              {quote.change_amount == null ? '' : `${quote.change_amount > 0 ? '+' : ''}${formatPrice(quote.change_amount)} · `}
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
                {status.closed
                  ? `${formatAgeSeconds(status.ageSeconds)}前获取（最近交易时段）`
                  : status.stale
                    ? `已过期（${formatAgeSeconds(status.ageSeconds)}前获取，阈值 ${STALE_THRESHOLD_SECONDS} 秒）`
                    : status.delayed
                      ? `可能延迟（${formatAgeSeconds(status.ageSeconds)}前获取）`
                      : `${formatAgeSeconds(status.ageSeconds)}前获取（最新 / 新鲜）`}
              </dd>
            </div>
            <div>
              <dt>今开 / 最高 / 最低 / 昨收</dt>
              <dd>{formatPrice(quote.open)} / {formatPrice(quote.high)} / {formatPrice(quote.low)} / {formatPrice(quote.previous_close)}</dd>
            </div>
            <div>
              <dt>成交量 / 成交额</dt>
              <dd>{formatCompactNumber(quote.volume, '股')} / {formatCompactNumber(quote.amount, '元')}</dd>
            </div>
            <div>
              <dt>换手率 / 量比</dt>
              <dd>{quote.turnover_rate == null ? '—' : `${quote.turnover_rate.toFixed(2)}%`} / {quote.volume_ratio == null ? '—' : quote.volume_ratio.toFixed(2)}</dd>
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
      {quote && refreshAction ? <div className="quote-header__refresh-action">{refreshAction}</div> : null}
    </Card>
  );
}
