'use client';

import { useEffect, useRef, useState } from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Space, Typography } from 'antd';

import { getJob, getSnapshot, refreshQuote } from '@/lib/api/endpoints';
import type { JobDTO, MarketDTO, QuoteDTO } from '@/lib/api/types';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime } from '@/lib/format';
import { sourcePresentation } from '@/lib/source-display';

type RefreshState = 'idle' | 'submitting' | 'queued' | 'running' | 'succeeded' | 'failed' | 'timeout';

export interface QuoteRefreshControlProps {
  symbol: string;
  market: MarketDTO | null | undefined;
  onQuote: (quote: QuoteDTO) => void;
  maxWaitSeconds?: number;
}

const MAX_WAIT_SECONDS = 30;

export function QuoteRefreshControl({
  symbol,
  market,
  onQuote,
  maxWaitSeconds = MAX_WAIT_SECONDS,
}: QuoteRefreshControlProps) {
  const [state, setState] = useState<RefreshState>('idle');
  const [job, setJob] = useState<JobDTO | null>(null);
  const [source, setSource] = useState('eastmoney_via_akshare');
  const [elapsed, setElapsed] = useState(0);
  const [cooldown, setCooldown] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const jobId = job?.id ?? null;

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown(cooldown - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    if (!jobId || (state !== 'queued' && state !== 'running')) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const current = await getJob(jobId);
        if (cancelled) return;
        setJob(current);
        const started = startedAtRef.current ?? Date.now();
        const waited = Math.min(maxWaitSeconds, Math.floor((Date.now() - started) / 1000));
        setElapsed(waited);

        if (current.status === 'succeeded') {
          const snapshot = await getSnapshot(symbol);
          if (cancelled) return;
          if (snapshot.data.quote) {
            onQuote(snapshot.data.quote);
            setState('succeeded');
            setCooldown(30);
            setMessage('最新行情已取得，只更新了实时行情区域。');
          } else {
            setState('failed');
            setCooldown(30);
            setMessage('任务已结束，但唯一行情来源没有返回这只股票的报价。');
          }
          return;
        }
        if (current.status === 'failed') {
          setState('failed');
          setCooldown(30);
          setMessage(current.error_message ?? '行情来源暂时无法连接。');
          return;
        }
        if (waited >= maxWaitSeconds) {
          setState('timeout');
          setCooldown(30);
          setMessage('等待已超过 30 秒。任务仍可能继续执行，可稍后再试。');
          return;
        }
        setState(current.status === 'running' ? 'running' : 'queued');
        timer = window.setTimeout(poll, 1000);
      } catch (error) {
        if (cancelled) return;
        setState('failed');
        setCooldown(30);
        setMessage(errorMessage(error));
      }
    };

    timer = window.setTimeout(poll, 500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, maxWaitSeconds, onQuote, state, symbol]);

  const handleRefresh = async () => {
    setState('submitting');
    setMessage(null);
    setElapsed(0);
    startedAtRef.current = Date.now();
    try {
      const response = await refreshQuote(symbol);
      setSource(response.data.source);
      setJob(response.data.job);
      setCooldown(response.data.retry_after_seconds);

      if (response.data.retry_after_seconds > 0 && response.data.job.status === 'failed') {
        setState('failed');
        setMessage(response.data.job.error_message ?? '行情来源暂时无法连接。');
        return;
      }
      setState(response.data.job.status === 'running' ? 'running' : 'queued');
    } catch (error) {
      setState('failed');
      setCooldown(30);
      setMessage(errorMessage(error));
    }
  };

  const busy = state === 'submitting' || state === 'queued' || state === 'running';
  const disabled = busy || cooldown > 0;
  const sourceName = sourcePresentation(source).name;
  const trading = market
    ? ['call_auction', 'morning', 'afternoon'].includes(market.phase)
    : true;
  const buttonLabel = busy
    ? state === 'queued'
      ? '已提交，等待获取…'
      : `正在获取…已等待 ${elapsed} 秒`
    : cooldown > 0
      ? `${cooldown} 秒后可再次获取`
      : !trading
        ? '获取最近可用行情'
        : '获取最新行情';

  return (
    <div className="quote-refresh" data-testid="quote-refresh" aria-live="polite">
      <Space wrap align="center">
      <Button
        type="primary"
        icon={<ReloadOutlined />}
        onClick={handleRefresh}
        disabled={disabled}
        loading={busy}
        data-testid="quote-refresh-button"
      >
        {buttonLabel}
      </Button>
      <Typography.Text type="secondary" className="quote-refresh__estimate">预计约 5–15 秒，高峰期可能需要 30 秒</Typography.Text>
      </Space>
      {state === 'succeeded' ? (
        <Alert type="success" showIcon title={message} data-testid="quote-refresh-success" />
      ) : null}
      {state === 'failed' || state === 'timeout' ? (
        <Alert type="error" showIcon data-testid="quote-refresh-error" title={`${sourceName}获取失败`} description={`${formatDateTime(job?.finished_at ?? new Date().toISOString())}：${message} 历史行情不受影响。`} />
      ) : null}
    </div>
  );
}
