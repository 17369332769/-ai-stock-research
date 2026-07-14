import { formatDate, formatMetric, formatRatioAsPercent } from '@/lib/format';
import type { AnalogDTO } from '@/lib/api/types';
import { EmptyHint } from './Section';

export interface AnalogsPanelProps {
  analogs: AnalogDTO[];
  /** 有效候选少于30个时 API 返回 422，页面据此关闭该功能并说明样本不足（spec §10）。 */
  insufficient?: boolean;
  insufficientMessage?: string;
}

/**
 * 历史相似行情（spec §7.5 / §10）。
 * 只展示当时可见特征与真实后续收益，不描述为因果关系。
 */
export function AnalogsPanel({ analogs, insufficient, insufficientMessage }: AnalogsPanelProps) {
  if (insufficient) {
    return (
      <EmptyHint>
        <span data-testid="analogs-insufficient">
          {insufficientMessage ?? '有效历史候选样本不足，已关闭历史相似行情。'}
        </span>
      </EmptyHint>
    );
  }

  if (analogs.length === 0) {
    return <EmptyHint>暂无历史相似行情。</EmptyHint>;
  }

  const featureKeys = Object.keys(analogs[0]?.features ?? {});

  return (
    <div data-testid="analogs-panel">
      <p className="analogs__caveat" data-testid="analogs-caveat">
        相似案例仅为历史统计，不代表因果关系，也不预示未来表现。
      </p>
      <div className="table-scroll">
        <table className="table" data-testid="analogs-table">
          <caption className="table__caption">
            按距离排序的历史相似状态（特征版本 {analogs[0]?.feature_set_version ?? '—'}）
          </caption>
          <thead>
            <tr>
              <th scope="col">相似日期</th>
              <th scope="col">距离</th>
              {featureKeys.map((key) => (
                <th scope="col" key={key}>
                  {key}
                </th>
              ))}
              <th scope="col">后续1日收益</th>
              <th scope="col">后续5日收益</th>
            </tr>
          </thead>
          <tbody>
            {analogs.map((analog) => (
              <tr key={`${analog.as_of}-${analog.distance}`} data-testid="analog-row">
                <td>{formatDate(analog.as_of)}</td>
                <td>{formatMetric(analog.distance, 3)}</td>
                {featureKeys.map((key) => (
                  <td key={key}>{formatMetric(analog.features[key], 3)}</td>
                ))}
                <td data-testid="analog-forward-1d">
                  {formatRatioAsPercent(analog.forward_returns.next_1d)}
                </td>
                <td data-testid="analog-forward-5d">
                  {formatRatioAsPercent(analog.forward_returns.next_5d)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
