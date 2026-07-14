import { BETTER_THAN_BASELINE_LABEL, NOT_BETTER_THAN_BASELINE_LABEL } from '@/lib/constants';
import { formatDateTime, formatMetric, formatProbability } from '@/lib/format';
import type { ScorecardDTO } from '@/lib/api/types';

export interface ScorecardTableProps {
  scorecard: ScorecardDTO;
}

/**
 * 模型成绩单（spec §7.4）。
 * 所有统计量（命中率、MAE、Brier、基准、better_than_baseline）均由 API 计算，前端只渲染。
 */
export function ScorecardTable({ scorecard }: ScorecardTableProps) {
  const better = scorecard.better_than_baseline;

  return (
    <div className="scorecard" data-testid="scorecard" data-model-key={scorecard.model_key}>
      <div className="scorecard__head">
        <span className="scorecard__model" data-testid="scorecard-model-key">
          {scorecard.model_key}
        </span>
        <span
          className={`badge ${better ? 'badge--ok' : 'badge--warning'}`}
          data-testid="scorecard-baseline-flag"
          data-better-than-baseline={String(better)}
        >
          {better ? BETTER_THAN_BASELINE_LABEL : NOT_BETTER_THAN_BASELINE_LABEL}
        </span>
        <span className="scorecard__calculated" data-testid="scorecard-calculated-at">
          统计时间 {formatDateTime(scorecard.calculated_at)}
        </span>
      </div>

      <dl className="scorecard__counts">
        <div>
          <dt>可结算样本</dt>
          <dd data-testid="scorecard-eligible-count">{scorecard.eligible_count}</dd>
        </div>
        <div>
          <dt>已结算</dt>
          <dd data-testid="scorecard-settled-count">{scorecard.settled_count}</dd>
        </div>
        <div>
          <dt>待结算</dt>
          <dd data-testid="scorecard-pending-count">{scorecard.pending_count}</dd>
        </div>
      </dl>

      <div className="table-scroll">
        <table className="table" data-testid="scorecard-metrics">
          <thead>
            <tr>
              <th scope="col">指标</th>
              <th scope="col">模型</th>
              <th scope="col">基准</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">方向命中率</th>
              <td data-testid="scorecard-direction-accuracy">
                {formatProbability(scorecard.direction_accuracy)}
              </td>
              <td data-testid="scorecard-baseline-direction-accuracy">
                {formatProbability(scorecard.baseline_direction_accuracy)}
              </td>
            </tr>
            <tr>
              <th scope="row">MAE</th>
              <td data-testid="scorecard-mae">{formatMetric(scorecard.mae)}</td>
              <td data-testid="scorecard-baseline-mae">{formatMetric(scorecard.baseline_mae)}</td>
            </tr>
            <tr>
              <th scope="row">Brier Score</th>
              <td data-testid="scorecard-brier">{formatMetric(scorecard.brier_score)}</td>
              <td data-testid="scorecard-baseline-brier">
                {formatMetric(scorecard.baseline_brier_score)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
