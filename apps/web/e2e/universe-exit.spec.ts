import { expect, test } from '@playwright/test';

import { SETTLED_PREDICTION, SNAPSHOT, WATCHLIST } from '../tests/fixtures';
import { apiError, installApi } from './mock-api';

/** E2E-6 成分调出（spec §3.1 / §15） */

test.describe('E2E-6 股票被调出沪深300', () => {
  test('历史页面与既有预测保留，标记「已调出沪深300」，停止生成新预测', async ({ page }) => {
    await installApi(page, {
      snapshot: () => ({
        status: 200,
        body: {
          ...SNAPSHOT,
          in_current_universe: false,
          universe_exit_at: '2026-06-30T00:00:00+08:00',
        },
      }),
      // 调出后不再生成新预测
      predictionLatest: () =>
        apiError('INSUFFICIENT_DATA', '该股票已调出沪深300，不再生成新预测', 422),
      // 既有预测仍可查询
      predictionHistory: () => ({
        status: 200,
        body: {
          data: [SETTLED_PREDICTION],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
    });

    await page.goto('/stocks/600519');

    // 历史页面仍可访问：行情、文档、相似行情照常
    await expect(page.getByTestId('research-page')).toBeVisible();
    await expect(page.getByTestId('quote-price')).toContainText('1,215.04');
    await expect(page.getByTestId('documents-list')).toBeVisible();

    // 调出标记
    await expect(page.getByTestId('badge-universe-exited')).toContainText('已调出沪深300');
    await expect(page.getByText('不再生成新预测').first()).toBeVisible();
    // 预测区逐条说明停止生成新预测的原因
    await expect(page.getByTestId('prediction-next_5d')).toContainText('已调出沪深300');

    // 不生成新预测
    await expect(page.getByTestId('prediction-probability')).toHaveCount(0);
    await expect(page.getByTestId('state-no_prediction').first()).toBeVisible();
  });

  test('已调出的股票不能重新添加（409）', async ({ page }) => {
    await installApi(page, {
      search: () => ({
        status: 200,
        body: {
          data: [{ symbol: '600519', name: '贵州茅台', exchange: 'SSE' }],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
      addWatchlist: () =>
        apiError('NOT_CURRENT_UNIVERSE_MEMBER', '600519 已调出沪深300，不能重新添加', 409),
    });

    await page.goto('/');
    await page.getByTestId('instrument-search-input').fill('600519');
    await page.getByTestId('instrument-search-submit').click();
    await page.getByTestId('add-600519').click();

    const error = page.getByTestId('add-error');
    await expect(error).toHaveAttribute('data-error-code', 'NOT_CURRENT_UNIVERSE_MEMBER');
    await expect(error).toContainText('不能重新添加');
  });

  test('自选股列表中标记已调出的持有项', async ({ page }) => {
    await installApi(page, {
      watchlist: () => ({
        status: 200,
        body: {
          data: [{ ...WATCHLIST[0], in_current_universe: false }],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
    });

    await page.goto('/');
    await expect(page.getByTestId('row-universe-exited')).toContainText('已调出沪深300');
  });
});
