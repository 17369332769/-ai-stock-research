import { RESEARCH_ONLY_DISCLAIMER } from '@/lib/constants';

/**
 * spec §13.2：任何预测区域都必须显示"仅供研究，不构成投资建议"。
 * 每个预测区域各自渲染一份，避免因布局调整而丢失。
 */
export function Disclaimer() {
  return (
    <p className="disclaimer" data-testid="disclaimer" role="note">
      {RESEARCH_ONLY_DISCLAIMER}
    </p>
  );
}
