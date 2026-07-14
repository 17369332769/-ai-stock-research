import type { ReactNode } from 'react';

import {
  BETTER_THAN_BASELINE_LABEL,
  CONFIDENCE_LABELS,
  HORIZON_LABELS,
  NOT_BETTER_THAN_BASELINE_LABEL,
} from '@/lib/constants';
import { formatDateTime, formatMetric, formatPrice, formatProbability, formatRatioAsPercent } from '@/lib/format';
import { resolveConfidenceDisplay } from '@/lib/prediction';
import type { PredictionDTO, PredictionHorizon, ScorecardDTO } from '@/lib/api/types';
import type { UiState } from '@/lib/ui-state';
import { Disclaimer } from './Disclaimer';
import { StateNotice } from './StateNotice';

export interface PredictionPanelProps {
  horizon: PredictionHorizon;
  prediction: PredictionDTO | null;
  /** 无预测时的状态：no_prediction / model_unavailable / initial_backfill。 */
  state?: UiState | null;
  stateDetail?: ReactNode;
  /** 展开区的滚动验证指标（spec §3.3）。 */
  scorecard?: ScorecardDTO | null;
}

/**
 * 预测区域（spec §3.3 / §13.2）。
 *
 * 红线：
 *  - 概率与区间必须同时出现，不允许只显示"看涨/看跌"。
 *  - 必须显示"仅供研究，不构成投资建议"（无论有无预测）。
 *  - better_than_baseline=false → 显示"未优于基准"且置信度为低。
 *  - 不出现任何交易/下单/收益承诺元素。
 */
export function PredictionPanel({
  horizon,
  prediction,
  state,
  stateDetail,
  scorecard,
}: PredictionPanelProps) {
  const horizonLabel = HORIZON_LABELS[horizon];

  if (!prediction) {
    return (
      <div className="prediction" data-testid={`prediction-${horizon}`} data-horizon={horizon}>
        <h3 className="prediction__title">{horizonLabel}预测</h3>
        <StateNotice state={state ?? 'no_prediction'} detail={stateDetail} />
        <Disclaimer />
      </div>
    );
  }

  const confidence = resolveConfidenceDisplay(prediction);
  const betterThanBaseline = prediction.model.better_than_baseline;

  return (
    <div className="prediction" data-testid={`prediction-${horizon}`} data-horizon={horizon}>
      <div className="prediction__head">
        <h3 className="prediction__title">{horizonLabel}预测</h3>
        <span
          className={`badge ${betterThanBaseline ? 'badge--ok' : 'badge--warning'}`}
          data-testid="baseline-flag"
          data-better-than-baseline={String(betterThanBaseline)}
        >
          {betterThanBaseline ? BETTER_THAN_BASELINE_LABEL : NOT_BETTER_THAN_BASELINE_LABEL}
        </span>
        <span
          className={`badge badge--confidence-${confidence.label}`}
          data-testid="prediction-confidence"
          data-confidence={confidence.label}
          data-confidence-clamped={String(confidence.clamped)}
        >
          置信度：{CONFIDENCE_LABELS[confidence.label]}
        </span>
      </div>

      {confidence.clamped ? (
        <p className="prediction__warning" data-testid="confidence-clamped-note">
          模型未优于基准，置信度按低展示（接口返回值与基准判定不一致，已按研究口径下调）。
        </p>
      ) : null}

      {/* 概率与区间必须同时出现（spec §13.2） */}
      <dl className="prediction__grid">
        <div className="prediction__cell">
          <dt>上涨概率</dt>
          <dd data-testid="prediction-probability">{formatProbability(prediction.probability_up)}</dd>
        </div>
        <div className="prediction__cell">
          <dt>预期收益</dt>
          <dd data-testid="prediction-expected-return">
            {formatRatioAsPercent(prediction.expected_return)}
          </dd>
        </div>
        <div className="prediction__cell">
          <dt>收益区间（P20 / P80）</dt>
          <dd data-testid="prediction-interval">
            {formatRatioAsPercent(prediction.return_interval.p20)} ~{' '}
            {formatRatioAsPercent(prediction.return_interval.p80)}
          </dd>
        </div>
        <div className="prediction__cell">
          <dt>参考价</dt>
          <dd data-testid="prediction-reference-price">{formatPrice(prediction.reference_price)}</dd>
        </div>
        <div className="prediction__cell">
          <dt>数据截止</dt>
          <dd data-testid="prediction-data-cutoff">{formatDateTime(prediction.data_cutoff)}</dd>
        </div>
        <div className="prediction__cell">
          <dt>模型版本</dt>
          <dd data-testid="prediction-model-version">
            {prediction.model.key} · {prediction.model.version}
          </dd>
        </div>
      </dl>

      <details className="prediction__details" data-testid="prediction-validation">
        <summary>查看滚动验证表现</summary>
        {scorecard ? (
          <dl className="prediction__grid">
            <div className="prediction__cell">
              <dt>验证样本数</dt>
              <dd data-testid="validation-settled-count">{scorecard.settled_count}</dd>
            </div>
            <div className="prediction__cell">
              <dt>方向命中率</dt>
              <dd data-testid="validation-direction-accuracy">
                {formatProbability(scorecard.direction_accuracy)}
              </dd>
            </div>
            <div className="prediction__cell">
              <dt>MAE</dt>
              <dd data-testid="validation-mae">{formatMetric(scorecard.mae)}</dd>
            </div>
            <div className="prediction__cell">
              <dt>Brier Score</dt>
              <dd data-testid="validation-brier">{formatMetric(scorecard.brier_score)}</dd>
            </div>
            <div className="prediction__cell">
              <dt>基准方向命中率</dt>
              <dd data-testid="validation-baseline-direction-accuracy">
                {formatProbability(scorecard.baseline_direction_accuracy)}
              </dd>
            </div>
            <div className="prediction__cell">
              <dt>基准 MAE / Brier</dt>
              <dd data-testid="validation-baseline-metrics">
                {formatMetric(scorecard.baseline_mae)} / {formatMetric(scorecard.baseline_brier_score)}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="empty-hint">暂无滚动验证数据。</p>
        )}
      </details>

      <Disclaimer />
    </div>
  );
}
