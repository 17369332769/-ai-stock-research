import { CONFIDENCE_LABELS, NOT_BETTER_THAN_BASELINE_LABEL } from '@/lib/constants';
import { formatDateTime, formatMetric, formatPrice, formatProbability, formatRatioAsPercent } from '@/lib/format';
import { resolveConfidenceDisplay } from '@/lib/prediction';
import type { PredictionDTO } from '@/lib/api/types';

export interface PredictionHistoryTableProps {
  predictions: PredictionDTO[];
}

/**
 * 股票维度成绩单（spec §3.4 / §7.4）：每次预测、实际结果、误差。
 * 原始预测不可覆盖，未结算的行显示"待结算"，不进入任何前端统计。
 */
export function PredictionHistoryTable({ predictions }: PredictionHistoryTableProps) {
  if (predictions.length === 0) {
    return <p className="empty-hint" data-testid="prediction-history-empty">暂无历史预测。</p>;
  }

  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label="历史预测记录，可横向滚动">
      <table className="table" data-testid="prediction-history-table">
        <caption className="table__caption">逐条预测、实际结果与结算误差</caption>
        <thead>
          <tr>
            <th scope="col">预测时间</th>
            <th scope="col">参考价</th>
            <th scope="col">上涨概率</th>
            <th scope="col">预期收益</th>
            <th scope="col">P20 / P80 区间</th>
            <th scope="col">置信度</th>
            <th scope="col">模型版本</th>
            <th scope="col">实际收益</th>
            <th scope="col">方向</th>
            <th scope="col">绝对误差</th>
            <th scope="col">结算时间</th>
          </tr>
        </thead>
        <tbody>
          {predictions.map((prediction) => {
            const confidence = resolveConfidenceDisplay(prediction);
            const outcome = prediction.outcome ?? null;
            const key = prediction.id ?? `${prediction.as_of}-${prediction.model.version}`;

            return (
              <tr key={key} data-testid="prediction-history-row" data-settled={String(outcome !== null)}>
                <td>{formatDateTime(prediction.as_of)}</td>
                <td>{formatPrice(prediction.reference_price)}</td>
                <td data-testid="history-probability">
                  {formatProbability(prediction.probability_up)}
                </td>
                <td>{formatRatioAsPercent(prediction.expected_return)}</td>
                <td data-testid="history-interval">
                  {formatRatioAsPercent(prediction.return_interval.p20)} ~{' '}
                  {formatRatioAsPercent(prediction.return_interval.p80)}
                </td>
                <td>
                  {CONFIDENCE_LABELS[confidence.label]}
                  {prediction.model.better_than_baseline === false ? (
                    <span className="badge badge--warning" data-testid="history-baseline-flag">
                      {NOT_BETTER_THAN_BASELINE_LABEL}
                    </span>
                  ) : null}
                </td>
                <td>{prediction.model.version}</td>
                <td data-testid="history-actual-return">
                  {outcome ? formatRatioAsPercent(outcome.actual_return) : '待结算'}
                </td>
                <td data-testid="history-direction-correct">
                  {outcome ? (outcome.direction_correct ? '命中' : '未命中') : '—'}
                </td>
                <td>{outcome ? formatMetric(outcome.absolute_error) : '—'}</td>
                <td>{outcome ? formatDateTime(outcome.settled_at) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
