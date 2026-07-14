'use client';

import { useState } from 'react';

import { PredictionHistoryTable } from '@/components/PredictionHistoryTable';
import { ScorecardTable } from '@/components/ScorecardTable';
import { Section } from '@/components/Section';
import { StateNotice } from '@/components/StateNotice';
import { Disclaimer } from '@/components/Disclaimer';
import { getPredictionHistory, getScorecard, getSystemStatus, getWatchlist } from '@/lib/api/endpoints';
import type { PredictionHorizon, ScorecardDTO, ScorecardWindow } from '@/lib/api/types';
import { errorMessage } from '@/lib/error-messages';
import { HORIZON_LABELS } from '@/lib/constants';
import { useApiResource } from '@/lib/hooks/useApiResource';
import { mapErrorToState } from '@/lib/ui-state';

const WINDOWS: { key: ScorecardWindow; label: string }[] = [
  { key: '20', label: '最近20次' },
  { key: '100', label: '最近100次' },
  { key: 'all', label: '全部历史' },
];

const HORIZONS: PredictionHorizon[] = ['today_close', 'next_5d'];

/** 预测成绩单（spec §13.1）：模型维度 + 股票维度。所有统计量由 API 计算。 */
export default function ScorecardPage() {
  const [window, setWindow] = useState<ScorecardWindow>('100');
  const [symbol, setSymbol] = useState<string>('');
  const [horizon, setHorizon] = useState<PredictionHorizon>('next_5d');

  // 模型清单来自系统状态（模型连接状态，spec §13.1）。
  const status = useApiResource(() => getSystemStatus(), []);

  const modelKeys = (status.data?.models ?? []).map((model) => model.model_key);

  const scorecards = useApiResource(async () => {
    if (modelKeys.length === 0) return [] as ScorecardDTO[];
    const results = await Promise.all(
      modelKeys.map(async (modelKey) => {
        try {
          return await getScorecard(modelKey, window);
        } catch {
          return null;
        }
      }),
    );
    return results.filter((card): card is ScorecardDTO => card !== null);
  }, [modelKeys.join(','), window]);

  const watchlist = useApiResource(async () => (await getWatchlist()).items, []);
  const symbols = (watchlist.data ?? []).map((item) => item.symbol);
  const activeSymbol = symbol || symbols[0] || '';

  const history = useApiResource(async () => {
    if (!activeSymbol) return [];
    return (await getPredictionHistory(activeSymbol, horizon)).items;
  }, [activeSymbol, horizon]);

  const modelErrorState = mapErrorToState(status.error);

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
          <StateNotice state="model_unavailable" detail="当前没有已注册的模型版本。" />
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
              <span className="field__label">自选股</span>
              <select
                className="field__select"
                value={activeSymbol}
                onChange={(event) => setSymbol(event.target.value)}
                data-testid="scorecard-symbol-select"
              >
                {symbols.length === 0 ? <option value="">暂无自选股</option> : null}
                {symbols.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">预测目标</span>
              <select
                className="field__select"
                value={horizon}
                onChange={(event) => setHorizon(event.target.value as PredictionHorizon)}
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
        {!activeSymbol ? (
          <p className="empty-hint">请先添加自选股。</p>
        ) : history.error ? (
          <StateNotice
            state={mapErrorToState(history.error) ?? 'no_prediction'}
            detail={errorMessage(history.error)}
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
