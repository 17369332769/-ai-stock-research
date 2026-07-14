import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiGet, apiGetItem, apiGetList, apiPost, isApiError } from '@/lib/api/client';
import { errorMessage } from '@/lib/error-messages';
import { SNAPSHOT, WATCHLIST } from './fixtures';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Response 的 body 只能被消费一次，因此每次调用都要新建（否则第二次请求会炸）。 */
function stubFetch(factory: (() => Response) | Error) {
  const fn = vi.fn(async () => {
    if (factory instanceof Error) throw factory;
    return factory();
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('列表信封（spec §7）', () => {
  it('解析 {data, page, request_id}', async () => {
    stubFetch(() =>
  jsonResponse({
        data: WATCHLIST,
        page: { next_cursor: 'abc', has_more: true },
        request_id: 'req-1',
      }),
    );

    const result = await apiGetList('/watchlist');
    expect(result.items).toHaveLength(2);
    expect(result.page).toEqual({ next_cursor: 'abc', has_more: true });
    expect(result.requestId).toBe('req-1');
  });

  it('缺少 page 时按无更多数据处理', async () => {
    stubFetch(() =>
  jsonResponse({ data: [], request_id: 'req-2' }));
    const result = await apiGetList('/watchlist');
    expect(result.page).toEqual({ next_cursor: null, has_more: false });
  });
});

describe('单对象响应：裸对象与信封都要能解析', () => {
  it('§7.2 snapshot 是裸对象', async () => {
    stubFetch(() =>
  jsonResponse(SNAPSHOT));
    const snapshot = await apiGet<typeof SNAPSHOT>('/stocks/600519/snapshot');
    expect(snapshot.symbol).toBe('600519');
    expect(snapshot.quote?.freshness).toBe('fresh');
  });

  it('§7.4 成绩单带 {data, request_id} 信封', async () => {
    stubFetch(() =>
  jsonResponse({ data: { model_key: 'm', window: 100 }, request_id: 'req-3' }));
    const scorecard = await apiGet<{ model_key: string }>('/models/m/scorecard');
    expect(scorecard.model_key).toBe('m');
  });

  it('202 首次回补：保留状态码与作业体（spec §7）', async () => {
    stubFetch(() =>
  jsonResponse(
        { data: { id: 'job-1', status: 'running', total_steps: 3 }, request_id: 'req-4' },
        202,
      ),
    );
    const result = await apiGetItem<{ id: string }>('/stocks/600519/predictions/latest');
    expect(result.status).toBe(202);
    expect(result.data.id).toBe('job-1');
  });
});

describe('错误契约与状态映射（spec §7）', () => {
  const cases = [
    ['NOT_CURRENT_UNIVERSE_MEMBER', 409, '该股票不是当前沪深300成分股'],
    ['DUPLICATE_WATCHLIST_ITEM', 409, '已在自选股中'],
    ['INSUFFICIENT_DATA', 422, '样本不足'],
    ['PROVIDER_UNAVAILABLE', 424, '数据源'],
    ['MODEL_UNAVAILABLE', 503, '模型'],
  ] as const;

  it.each(cases)('%s(%d) 被解析成 ApiError', async (code, status) => {
    stubFetch(() =>
  jsonResponse(
        { error: { code, message: `后端文案：${code}`, request_id: 'req-err' } },
        status,
      ),
    );

    await expect(apiPost('/watchlist', { symbol: '600519' })).rejects.toBeInstanceOf(ApiError);

    try {
      await apiPost('/watchlist', { symbol: '600519' });
    } catch (error) {
      expect(isApiError(error)).toBe(true);
      const apiError = error as ApiError;
      expect(apiError.code).toBe(code);
      expect(apiError.status).toBe(status);
      expect(apiError.requestId).toBe('req-err');
      expect(errorMessage(apiError)).toBe(`后端文案：${code}`);
    }
  });

  it('未知错误码归一为 UNKNOWN，并给出兜底文案', async () => {
    stubFetch(() =>
  jsonResponse({ error: { code: 'WHATEVER', message: 'x', request_id: 'r' } }, 500));
    try {
      await apiGet('/watchlist');
      expect.unreachable('应当抛出 ApiError');
    } catch (error) {
      expect((error as ApiError).code).toBe('UNKNOWN');
      expect(errorMessage(error)).toBe('请求失败，请稍后重试。');
    }
  });

  it('网络不可达 → NETWORK_ERROR，文案提示本地服务未启动', async () => {
    stubFetch(new TypeError('fetch failed'));
    try {
      await apiGet('/watchlist');
      expect.unreachable('应当抛出 ApiError');
    } catch (error) {
      expect((error as ApiError).code).toBe('NETWORK_ERROR');
      expect(errorMessage(error)).toContain('无法连接研究服务');
    }
  });
});
