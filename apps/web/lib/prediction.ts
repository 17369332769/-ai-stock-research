/**
 * 预测区域的展示规则（不做数值计算）。
 */

import type { ConfidenceLabel, PredictionDTO } from './api/types';

export interface ConfidenceDisplay {
  label: ConfidenceLabel;
  /**
   * true = API 返回的置信度与 better_than_baseline=false 冲突，
   * 展示端按 spec §9.4「未优于基准时置信度只能为 low」降级显示，并明示不一致。
   */
  clamped: boolean;
}

/**
 * spec §9.4 / §13 红线：模型未优于基准时，界面必须显示"未优于基准"且置信度为低。
 * 正常情况下 API 已保证该不变量；此处只做展示端兜底，绝不上调置信度。
 */
export function resolveConfidenceDisplay(prediction: PredictionDTO): ConfidenceDisplay {
  const apiLabel = prediction.confidence;
  if (prediction.model.better_than_baseline === false && apiLabel !== 'low') {
    return { label: 'low', clamped: true };
  }
  return { label: apiLabel, clamped: false };
}
