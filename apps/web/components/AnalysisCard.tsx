import { DIRECTION_LABELS, EVENT_HORIZON_LABELS, NO_VERIFIABLE_CAUSE_TEXT } from '@/lib/constants';
import { formatDateTime } from '@/lib/format';
import type { AnalysisDTO } from '@/lib/api/types';
import { EvidenceList } from './EvidenceList';
import { RobotOutlined } from '@ant-design/icons';
import { Alert, Card, Divider, Space, Tag, Typography } from 'antd';

export interface AnalysisCardProps {
  analysis: AnalysisDTO;
}

const DIRECTION_TONE: Record<string, string> = {
  positive: 'up',
  negative: 'down',
  neutral: 'flat',
  unknown: 'flat',
};

/**
 * AI 结论卡片（异动摘要 / 文档解读）。
 *
 * 约束：
 *  - 无证据时 direction 必须是 unknown，摘要含固定文本"未找到可验证事件原因"（spec §7.3）。
 *    此处不改写 API 文案，只在缺失时补一行提示，且证据区展示"原因未知"。
 *  - Agent 的 confidence 是 0-1 数值（analyses.confidence），与预测的 low/medium/high 不同。
 */
export function AnalysisCard({ analysis }: AnalysisCardProps) {
  const direction = analysis.direction ?? 'unknown';
  const horizon = analysis.horizon ?? 'unknown';
  const hasEvidence = analysis.evidence.length > 0;
  const summaryHasFixedText = analysis.summary.includes(NO_VERIFIABLE_CAUSE_TEXT);

  return (
    <Card className="analysis" data-testid="analysis-card" data-analysis-type={analysis.analysis_type} size="small" title={<Typography.Title level={3} className="analysis__card-title"><RobotOutlined /> AI 证据分析</Typography.Title>}>
      <Space className="analysis__head" size={[8, 8]} wrap>
        <Tag
          color={direction === 'positive' ? 'error' : direction === 'negative' ? 'success' : 'default'}
          className={`badge badge--${DIRECTION_TONE[direction] ?? 'flat'}`}
          data-testid="analysis-direction"
        >
          影响方向：{DIRECTION_LABELS[direction]}
        </Tag>
        <Tag data-testid="analysis-horizon">
          期限：{EVENT_HORIZON_LABELS[horizon]}
        </Tag>
        <Typography.Text type="secondary" className="analysis__cutoff" data-testid="analysis-cutoff">
          数据截止 {formatDateTime(analysis.data_cutoff)}
        </Typography.Text>
      </Space>

      <Typography.Paragraph className="analysis__summary" data-testid="analysis-summary">
        {analysis.summary}
      </Typography.Paragraph>

      {!hasEvidence && !summaryHasFixedText ? (
        <Alert type="warning" showIcon title={NO_VERIFIABLE_CAUSE_TEXT} data-testid="analysis-no-cause" />
      ) : null}

      {analysis.risk_flags && analysis.risk_flags.length > 0 ? (
        <Alert type="warning" showIcon title="风险提示" description={analysis.risk_flags.join('；')} className="analysis__risks" data-testid="analysis-risk-flags" />
      ) : null}

      <div className="analysis__evidence">
        <Divider className="analysis__evidence-title">证据</Divider>
        <EvidenceList evidence={analysis.evidence} />
      </div>

      {analysis.model_name ? (
        <Typography.Text type="secondary" className="analysis__footer" data-testid="analysis-model">
          分析模型：{analysis.model_provider ?? '未知供应商'} / {analysis.model_name}
        </Typography.Text>
      ) : null}
    </Card>
  );
}
