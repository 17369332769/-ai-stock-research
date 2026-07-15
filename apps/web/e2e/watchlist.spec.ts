import { expect, test } from '@playwright/test';

import { BACKFILL_JOB_QUEUED, apiError, installApi } from './mock-api';

/**
 * E2E-1 沪深300选股
 * E2E-2 拒绝非成分股
 */

test.describe('E2E-1 沪深300选股', () => {
  test('搜索成分股 → 加入自选 → 展示首次回补进度（三步）', async ({ page }) => {
    let added = false;

    await installApi(page, {
      watchlist: () =>
        added
          ? {
              status: 200,
              body: {
                data: [
                  {
                    symbol: '600519',
                    name: '贵州茅台',
                    display_order: 0,
                    is_current_universe_member: true,
                    quote: null,
                    backfill_job: { ...BACKFILL_JOB_QUEUED, status: 'running', completed_steps: 1, current_step: 'minute_bars' },
                  },
                ],
                page: { next_cursor: null, has_more: false },
                request_id: 'r',
              },
            }
          : { status: 200, body: { data: [], page: { next_cursor: null, has_more: false }, request_id: 'r' } },
      addWatchlist: () => {
        added = true;
        return {
          status: 202,
          body: {
            data: {
              watchlist_item: { symbol: '600519', display_order: 0 },
              backfill_job: BACKFILL_JOB_QUEUED,
            },
            request_id: 'r',
          },
        };
      },
    });

    await page.goto('/');
    await expect(page.getByTestId('watchlist-empty')).toContainText('沪深300');

    await page.getByTestId('instrument-search-input').fill('600519');
    await page.getByTestId('instrument-search-submit').click();

    const results = page.getByTestId('instrument-search-results');
    await expect(results).toContainText('贵州茅台');

    await page.getByTestId('add-600519').click();

    // 202 → 首次回补状态 + 三步进度
    await expect(page.getByTestId('state-initial_backfill').first()).toContainText('首次回补');
    const progress = page.getByTestId('backfill-progress').first();
    await expect(progress).toBeVisible();
    await expect(page.getByTestId('backfill-step-counter').first()).toContainText('/3');

    for (const step of ['daily_bars', 'minute_bars', 'documents']) {
      await expect(page.locator(`[data-step="${step}"]`).first()).toBeVisible();
    }

    // 回补未完成时不显示预测
    await expect(page.getByTestId('prediction-probability')).toHaveCount(0);

    // 自选股列表已包含该股票
    await expect(page.getByTestId('watchlist-row')).toHaveCount(1);
    await expect(page.getByTestId('watchlist-row')).toHaveAttribute('data-symbol', '600519');
  });

  test('自选股表格支持搜索、排序与新鲜度展示', async ({ page }) => {
    await installApi(page);
    await page.goto('/');

    await expect(page.getByTestId('watchlist-row')).toHaveCount(2);
    // 数据新鲜度：一只新鲜、一只已过期
    await expect(page.getByTestId('badge-fresh')).toBeVisible();
    await expect(page.getByTestId('badge-quote_stale').first()).toBeVisible();

    // 排序
    await page.getByTestId('sort-symbol').click();
    await expect(page.getByTestId('watchlist-row').first()).toHaveAttribute('data-symbol', '000001');

    // 搜索
    await page.getByTestId('watchlist-search').fill('茅台');
    await expect(page.getByTestId('watchlist-row')).toHaveCount(1);
    await expect(page.getByTestId('watchlist-row')).toHaveAttribute('data-symbol', '600519');
  });
});

test.describe('E2E-2 拒绝非成分股', () => {
  test('添加非沪深300成分股返回 409 → 界面明确拒绝', async ({ page }) => {
    await installApi(page, {
      search: () => ({
        status: 200,
        body: {
          data: [{ symbol: '600999', name: '非成分示例', exchange: 'SSE' }],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
      addWatchlist: () =>
        apiError('NOT_CURRENT_UNIVERSE_MEMBER', '600999 不是查询日沪深300当前成分股', 409),
    });

    await page.goto('/');
    await page.getByTestId('instrument-search-input').fill('600999');
    await page.getByTestId('instrument-search-submit').click();
    await page.getByTestId('add-600999').click();

    const error = page.getByTestId('add-error');
    await expect(error).toBeVisible();
    await expect(error).toHaveAttribute('data-error-code', 'NOT_CURRENT_UNIVERSE_MEMBER');
    await expect(error).toContainText('沪深300');

    // 未被加入自选股
    await expect(page.locator('[data-symbol="600999"][data-testid="watchlist-row"]')).toHaveCount(0);
  });

  test('重复添加返回 409 DUPLICATE_WATCHLIST_ITEM → 明确错误', async ({ page }) => {
    await installApi(page, {
      addWatchlist: () => apiError('DUPLICATE_WATCHLIST_ITEM', '600519 已在自选股中', 409),
    });

    await page.goto('/');
    await page.getByTestId('instrument-search-input').fill('600519');
    await page.getByTestId('instrument-search-submit').click();
    await page.getByTestId('add-600519').click();

    const error = page.getByTestId('add-error');
    await expect(error).toBeVisible();
    await expect(error).toHaveAttribute('data-error-code', 'DUPLICATE_WATCHLIST_ITEM');
    await expect(error).toContainText('已在自选股中');
  });

  test('搜索无匹配时说明非成分股不可添加', async ({ page }) => {
    await installApi(page, {
      search: () => ({
        status: 200,
        body: { data: [], page: { next_cursor: null, has_more: false }, request_id: 'r' },
      }),
    });

    await page.goto('/');
    await page.getByTestId('instrument-search-input').fill('999999');
    await page.getByTestId('instrument-search-submit').click();

    await expect(page.getByTestId('search-empty')).toContainText('非成分股不可添加');
  });
});
