import { BETTER_THAN_BASELINE_LABEL, NOT_BETTER_THAN_BASELINE_LABEL } from '@/lib/constants';
import { formatDateTime, formatMetric, formatProbability } from '@/lib/format';
import type { ScorecardDTO } from '@/lib/api/types';

export interface ScorecardTableProps {
  scorecard: ScorecardDTO;
}

function modelName(modelKey: string): string {
  if (modelKey.includes('today')) return '今日收盘预测模型';
  if (modelKey.includes('5d')) return '未来5个交易日预测模型';
  return '研究预测模型';
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
          {modelName(scorecard.model_key)}
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
      <details className="metric-help">
        <summary>查看模型技术信息</summary>
        <p>模型标识：<code>{scorecard.model_key}</code></p>
      </details>

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

      <div className="table-scroll" tabIndex={0} role="region" aria-label={`${scorecard.model_key}模型与基准指标对比`}>
        <table className="table" data-testid="scorecard-metrics">
          <caption className="table__caption">模型与简单基准的滚动验证指标对比</caption>
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
      <details className="metric-help">
        <summary>这些指标是什么意思？</summary>
        <p>MAE表示预测收益与实际收益的平均绝对误差，越低越好；Brier Score衡量概率预测误差，越低越好。必须结合样本数和基准一起判断。</p>
      </details>
    </div>
  );
}
