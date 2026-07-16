/** 错误码 → 产品语言文案（spec §7 错误码表）。 */

import { isApiError } from './api/client';
import type { ClientErrorCode } from './api/types';

const MESSAGES: Record<ClientErrorCode, string> = {
  INVALID_ARGUMENT: '请求参数无效，请检查代码或筛选条件。',
  INSTRUMENT_NOT_FOUND: '未找到该证券，或它不在本产品支持的市场范围内。',
  NOT_CURRENT_UNIVERSE_MEMBER: '该股票不属于当前自动研究范围。',
  DUPLICATE_WATCHLIST_ITEM: '该股票已在我的关注中，无需重复添加。',
  INSUFFICIENT_DATA: '历史样本不足，暂时无法生成结果。',
  PROVIDER_UNAVAILABLE: '上游数据源暂时不可用，已有历史数据仍可查看。',
  MODEL_UNAVAILABLE: '当前没有可用的模型版本，暂不生成预测。',
  NETWORK_ERROR: '无法连接研究服务，请确认本地服务已启动。',
  UNKNOWN: '请求失败，请稍后重试。',
};

/** 取用户可读文案：优先用 API 的 message，其次用错误码对应的默认文案。 */
export function errorMessage(error: unknown): string {
  if (!isApiError(error)) {
    return MESSAGES.UNKNOWN;
  }
  if (error.message && error.code !== 'UNKNOWN') {
    return error.message;
  }
  return MESSAGES[error.code];
}

/** 取错误码对应的固定文案（不使用后端 message）。 */
export function errorCodeMessage(code: ClientErrorCode): string {
  return MESSAGES[code];
}

export function errorCodeOf(error: unknown): ClientErrorCode | null {
  return isApiError(error) ? error.code : null;
}
