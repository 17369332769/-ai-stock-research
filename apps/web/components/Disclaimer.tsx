import { RESEARCH_ONLY_DISCLAIMER } from '@/lib/constants';
import { Alert } from 'antd';

/**
 * spec §13.2：任何预测区域都必须显示"仅供研究，不构成投资建议"。
 * 每个预测区域各自渲染一份，避免因布局调整而丢失。
 */
export function Disclaimer() {
  return (
    <Alert className="disclaimer" data-testid="disclaimer" type="warning" showIcon title={RESEARCH_ONLY_DISCLAIMER} role="note" />
  );
}
