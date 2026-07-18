import { BETTER_THAN_BASELINE_LABEL, NOT_BETTER_THAN_BASELINE_LABEL } from '@/lib/constants';
import { formatDateTime, formatMetric, formatProbability } from '@/lib/format';
import type { ScorecardDTO } from '@/lib/api/types';
import { BarChartOutlined } from '@ant-design/icons';
import { Descriptions, Space, Table, Tag, Typography } from 'antd';

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
      <Space className="scorecard__head" wrap>
        <Typography.Text strong className="scorecard__model" data-testid="scorecard-model-key">
          <BarChartOutlined />{' '}
          {modelName(scorecard.model_key)}
        </Typography.Text>
        <Tag
          color={better ? 'success' : 'warning'}
          data-testid="scorecard-baseline-flag"
          data-better-than-baseline={String(better)}
        >
          {better ? BETTER_THAN_BASELINE_LABEL : NOT_BETTER_THAN_BASELINE_LABEL}
        </Tag>
        <Typography.Text type="secondary" className="scorecard__calculated" data-testid="scorecard-calculated-at">
          统计时间 {formatDateTime(scorecard.calculated_at)}
        </Typography.Text>
      </Space>
      <details className="metric-help">
        <summary>查看模型技术信息</summary>
        <p>模型标识：<code>{scorecard.model_key}</code></p>
      </details>

      <Descriptions className="scorecard__counts" bordered size="small" column={3} items={[
        { key: 'eligible', label: '可结算样本', children: <strong data-testid="scorecard-eligible-count">{scorecard.eligible_count}</strong> },
        { key: 'settled', label: '已结算', children: <strong data-testid="scorecard-settled-count">{scorecard.settled_count}</strong> },
        { key: 'pending', label: '待结算', children: <strong data-testid="scorecard-pending-count">{scorecard.pending_count}</strong> },
      ]} />

      <div className="table-scroll" tabIndex={0} role="region" aria-label={`${scorecard.model_key}模型与基准指标对比`} data-testid="scorecard-metrics">
        <Table
          size="small"
          pagination={false}
          rowKey="metric"
          columns={[
            { title: '指标', dataIndex: 'metric', key: 'metric' },
            { title: '模型', dataIndex: 'model', key: 'model' },
            { title: '基准', dataIndex: 'baseline', key: 'baseline' },
          ]}
          dataSource={[
            { metric: '方向命中率', model: <span data-testid="scorecard-direction-accuracy">{formatProbability(scorecard.direction_accuracy)}</span>, baseline: <span data-testid="scorecard-baseline-direction-accuracy">{formatProbability(scorecard.baseline_direction_accuracy)}</span> },
            { metric: 'MAE', model: <span data-testid="scorecard-mae">{formatMetric(scorecard.mae)}</span>, baseline: <span data-testid="scorecard-baseline-mae">{formatMetric(scorecard.baseline_mae)}</span> },
            { metric: 'Brier Score', model: <span data-testid="scorecard-brier">{formatMetric(scorecard.brier_score)}</span>, baseline: <span data-testid="scorecard-baseline-brier">{formatMetric(scorecard.baseline_brier_score)}</span> },
          ]}
        />
      </div>
      <details className="metric-help">
        <summary>这些指标是什么意思？</summary>
        <p>MAE表示预测收益与实际收益的平均绝对误差，越低越好；Brier Score衡量概率预测误差，越低越好。必须结合样本数和基准一起判断。</p>
      </details>
    </div>
  );
}
