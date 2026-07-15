import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { QuoteRefreshControl } from '@/components/QuoteRefreshControl';
import type { JobDTO } from '@/lib/api/types';
import { CLOSED_MARKET, FRESH_QUOTE, SNAPSHOT, SYMBOL, TRADING_MARKET } from './fixtures';

const endpointMocks = vi.hoisted(() => ({
  getJob: vi.fn(),
  getSnapshot: vi.fn(),
  refreshQuote: vi.fn(),
}));

vi.mock('@/lib/api/endpoints', () => endpointMocks);

const QUEUED_REFRESH: JobDTO = {
  id: 'quote-job-1',
  job_type: 'quote_refresh',
  symbol: SYMBOL,
  status: 'queued' as const,
  completed_steps: 0,
  total_steps: 1,
  current_step: 'fetch_quote',
  error_code: null,
  error_message: null,
  warnings: [],
  updated_at: '2026-07-15T10:35:00+08:00',
};

function refreshResponse(job: JobDTO = QUEUED_REFRESH, retryAfter = 0) {
  return {
    status: 202,
    data: {
      job,
      source: 'eastmoney_via_akshare',
      estimated_seconds: 10,
      retry_after_seconds: retryAfter,
      requested_at: '2026-07-15T10:35:00+08:00',
    },
    requestId: 'request-1',
  };
}

describe('QuoteRefreshControl：单股最新行情刷新', () => {
  beforeEach(() => {
    endpointMocks.getJob.mockReset();
    endpointMocks.getSnapshot.mockReset();
    endpointMocks.refreshQuote.mockReset();
  });

  it('无行情时显示预计时间，成功后只回传新的 Quote', async () => {
    const onQuote = vi.fn();
    endpointMocks.refreshQuote.mockResolvedValue(refreshResponse());
    endpointMocks.getJob.mockResolvedValue({
      ...QUEUED_REFRESH,
      status: 'succeeded',
      completed_steps: 1,
      finished_at: '2026-07-15T10:35:08+08:00',
    });
    endpointMocks.getSnapshot.mockResolvedValue({
      status: 200,
      data: { ...SNAPSHOT, quote: FRESH_QUOTE },
      requestId: 'request-2',
    });

    render(<QuoteRefreshControl symbol={SYMBOL} market={TRADING_MARKET} onQuote={onQuote} />);
    expect(screen.getByText('预计约 5–15 秒，高峰期可能需要 30 秒')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('quote-refresh-button'));

    await waitFor(() => expect(screen.getByTestId('quote-refresh-success')).toBeInTheDocument());
    expect(endpointMocks.refreshQuote).toHaveBeenCalledWith(SYMBOL);
    expect(onQuote).toHaveBeenCalledWith(FRESH_QUOTE);
  });

  it('上游失败时显示中文来源、失败原因与30秒冷却', async () => {
    const failed = {
      ...QUEUED_REFRESH,
      status: 'failed' as const,
      error_code: 'PROVIDER_UNAVAILABLE',
      error_message: '唯一行情来源暂时不可用',
      finished_at: '2026-07-15T10:35:05+08:00',
    };
    endpointMocks.refreshQuote.mockResolvedValue(refreshResponse(failed, 30));

    render(<QuoteRefreshControl symbol={SYMBOL} market={TRADING_MARKET} onQuote={vi.fn()} />);
    fireEvent.click(screen.getByTestId('quote-refresh-button'));

    await waitFor(() => expect(screen.getByTestId('quote-refresh-error')).toBeInTheDocument());
    expect(screen.getByTestId('quote-refresh-error')).toHaveTextContent('东方财富行情获取失败');
    expect(screen.getByTestId('quote-refresh-error')).toHaveTextContent('历史行情不受影响');
    expect(screen.getByTestId('quote-refresh-button')).toHaveTextContent('30 秒后可再次获取');
  });

  it('超过等待上限后停止等待并给出明确提示', async () => {
    endpointMocks.refreshQuote.mockResolvedValue(refreshResponse());
    endpointMocks.getJob.mockResolvedValue({ ...QUEUED_REFRESH, status: 'running' });

    render(
      <QuoteRefreshControl
        symbol={SYMBOL}
        market={TRADING_MARKET}
        onQuote={vi.fn()}
        maxWaitSeconds={1}
      />,
    );
    fireEvent.click(screen.getByTestId('quote-refresh-button'));

    await waitFor(
      () => expect(screen.getByTestId('quote-refresh-error')).toHaveTextContent('等待已超过 30 秒'),
      { timeout: 2500 },
    );
  });

  it('休市时按钮说明将获取最近可用行情', () => {
    render(<QuoteRefreshControl symbol={SYMBOL} market={CLOSED_MARKET} onQuote={vi.fn()} />);
    expect(screen.getByTestId('quote-refresh-button')).toHaveTextContent('获取最近可用行情');
  });
});
