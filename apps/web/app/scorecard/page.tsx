'use client';

import { Suspense, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { PredictionHistoryTable } from '@/components/PredictionHistoryTable';
import { ScorecardTable } from '@/components/ScorecardTable';
import { Section } from '@/components/Section';
import { StateNotice } from '@/components/StateNotice';
import { Disclaimer } from '@/components/Disclaimer';
import { getPredictionHistory, getResearchPool, getScorecard, getSystemStatus } from '@/lib/api/endpoints';
import type { PredictionHorizon, ScorecardDTO, ScorecardWindow } from '@/lib/api/types';
import { errorMessage } from '@/lib/error-messages';
import { HORIZON_LABELS } from '@/lib/constants';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { isInsufficientData, mapErrorToState } from '@/lib/ui-state';

const WINDOWS: { key: ScorecardWindow; label: string }[] = [
  { key: '20', label: '最近20次' },
  { key: '100', label: '最近100次' },
  { key: 'all', label: '全部历史' },
];

const HORIZONS: PredictionHorizon[] = ['today_close', 'next_5d'];

/** 预测成绩单（spec §13.1）：模型维度 + 股票维度。所有统计量由 API 计算。 */
function ScorecardContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [window, setWindow] = useState<ScorecardWindow>('100');
  const [symbolSearch, setSymbolSearch] = useState('');
  const symbol = searchParams.get('symbol') ?? '';
  const horizon: PredictionHorizon = searchParams.get('horizon') === 'today_close' ? 'today_close' : 'next_5d';

  // 模型清单来自系统状态（模型连接状态，spec §13.1）。
  const status = useApiResource(() => getSystemStatus(), []);

  const modelKeys = (status.data?.models ?? []).map((model) => model.model_key);

  const scorecards = useApiResource(async () => {
    if (modelKeys.length === 0) return [] as ScorecardDTO[];
    return Promise.all(modelKeys.map((modelKey) => getScorecard(modelKey, window)));
  }, [modelKeys.join(','), window]);

  const researchPool = useApiResource(async () => (await getResearchPool('all')).items, []);
  const poolItems = researchPool.data ?? [];
  const normalizedSearch = symbolSearch.trim().toLowerCase();
  const visibleSymbols = normalizedSearch
    ? poolItems.filter(
        (item) =>
          item.symbol.toLowerCase().includes(normalizedSearch) ||
          (item.name ?? '').toLowerCase().includes(normalizedSearch),
      )
    : poolItems;
  const activeSymbol = symbol || poolItems[0]?.symbol || '';
  const activeItem = poolItems.find((item) => item.symbol === activeSymbol);
  const selectItems =
    activeItem && !visibleSymbols.some((item) => item.symbol === activeSymbol)
      ? [activeItem, ...visibleSymbols]
      : visibleSymbols;

  const updateSelection = (nextSymbol: string, nextHorizon: PredictionHorizon) => {
    const params = new URLSearchParams(searchParams.toString());
    if (nextSymbol) params.set('symbol', nextSymbol);
    else params.delete('symbol');
    params.set('horizon', nextHorizon);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  const history = useApiResource(async () => {
    if (!activeSymbol) return [];
    return (await getPredictionHistory(activeSymbol, horizon)).items;
  }, [activeSymbol, horizon]);

  const modelErrorState = mapErrorToState(status.error);
  const marketSource = status.data?.sources.find((source) => source.key === 'akshare') ?? null;

  return (
    <div data-testid="scorecard-page">
      <h1 className="page-title">预测成绩单</h1>
      <p className="page-subtitle">
        每次预测永久保存并自动结算。未到目标时间的预测不进入分母。
      </p>

      <Section
        id="model-scorecard"
        title="模型维度"
        subtitle="全部历史 / 最近100次 / 最近20次；含基准对照。"
        action={
          <div className="filter-row" role="group" aria-label="统计窗口">
            {WINDOWS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`filter-chip ${window === item.key ? 'filter-chip--active' : ''}`}
                aria-pressed={window === item.key}
                onClick={() => setWindow(item.key)}
                data-testid={`window-${item.key}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        }
      >
        {status.error ? (
          <StateNotice
            state={modelErrorState ?? 'model_unavailable'}
            detail={errorMessage(status.error)}
          />
        ) : status.loading && !status.loaded ? (
          <p className="empty-hint">加载中…</p>
        ) : modelKeys.length === 0 ? (
          <div data-testid="model-setup-guide">
            <StateNotice state="model_unavailable" detail="模型尚未启用，当前不会生成正式预测。" />
            <ol className="setup-checklist">
              <li>
                历史行情覆盖：{marketSource ? `${marketSource.coverage}/${marketSource.total}` : '正在检查'}
              </li>
              <li>生成特征数据并完成训练，模型版本先进入“候选”。</li>
              <li>确认离线成绩优于基准后，将指定版本设为“启用”。</li>
              <li>只有已启用版本会生成正式预测并进入成绩单。</li>
            </ol>
            <Link className="btn btn--ghost" href="/settings/data-sources">
              查看数据与模型运行状态
            </Link>
          </div>
        ) : scorecards.loading && !scorecards.loaded ? (
          <p className="empty-hint">加载中…</p>
        ) : (scorecards.data ?? []).length === 0 ? (
          <StateNotice state="no_prediction" detail="所选窗口内暂无可结算的预测。" />
        ) : (
          <div data-testid="model-scorecards" data-window={window}>
            {(scorecards.data ?? []).map((card) => (
              <ScorecardTable key={`${card.model_key}-${card.window}`} scorecard={card} />
            ))}
          </div>
        )}
        <Disclaimer />
      </Section>

      <Section
        id="stock-scorecard"
        title="股票维度"
        subtitle="逐条预测、实际结果与误差；未结算的记录标注为待结算。"
        action={
          <div className="toolbar">
            <label className="field">
              <span className="field__label">搜索研究池股票</span>
              <input
                className="field__input"
                type="search"
                value={symbolSearch}
                placeholder="名称或代码"
                onChange={(event) => setSymbolSearch(event.target.value)}
                data-testid="scorecard-symbol-search"
              />
            </label>
            <label className="field">
              <span className="field__label">研究池股票</span>
              <select
                className="field__select"
                value={activeSymbol}
                onChange={(event) => {
                  updateSelection(event.target.value, horizon);
                }}
                data-testid="scorecard-symbol-select"
              >
                {poolItems.length === 0 ? <option value="">研究池暂无股票</option> : null}
                {selectItems.map((item) => (
                  <option key={item.symbol} value={item.symbol}>
                    {item.name ?? item.symbol}（{item.symbol}）
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">预测目标</span>
              <select
                className="field__select"
                value={horizon}
                onChange={(event) => {
                  const value = event.target.value as PredictionHorizon;
                  updateSelection(activeSymbol, value);
                }}
                data-testid="scorecard-horizon-select"
              >
                {HORIZONS.map((item) => (
                  <option key={item} value={item}>
                    {HORIZON_LABELS[item]}
                  </option>
                ))}
              </select>
            </label>
          </div>
        }
      >
        {researchPool.error ? (
          <StateNotice state={mapErrorToState(researchPool.error) ?? 'provider_failed'} detail={errorMessage(researchPool.error)} action={<button type="button" className="btn" onClick={researchPool.reload}>重试研究池</button>} />
        ) : researchPool.loading && !researchPool.loaded ? (
          <p className="empty-hint" role="status">正在读取研究池股票…</p>
        ) : !activeSymbol ? (
          <p className="empty-hint">当前研究池暂无可查看的预测记录。</p>
        ) : history.error ? (
          <StateNotice
            state={isInsufficientData(history.error) ? 'no_prediction' : mapErrorToState(history.error) ?? 'provider_failed'}
            detail={errorMessage(history.error)}
            action={<button type="button" className="btn" onClick={history.reload}>重试预测记录</button>}
          />
        ) : history.loading && !history.loaded ? (
          <p className="empty-hint">加载中…</p>
        ) : (
          <div data-testid="stock-scorecard" data-symbol={activeSymbol}>
            <PredictionHistoryTable predictions={history.data ?? []} />
          </div>
        )}
        <Disclaimer />
      </Section>
    </div>
  );
}

export default function ScorecardPage() {
  return (
    <Suspense fallback={<p className="empty-hint">正在加载模型表现…</p>}>
      <ScorecardContent />
    </Suspense>
  );
}
