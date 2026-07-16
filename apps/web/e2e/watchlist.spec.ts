import { expect, test } from '@playwright/test';

import { FRESH_QUOTE, TRADING_MARKET } from '../tests/fixtures';
import { BACKFILL_JOB_QUEUED, installApi } from './mock-api';

test.describe('003 研究池发现与URL恢复', () => {
  test('摘要卡形成对应筛选，搜索防抖后写入URL，返回上下文保持', async ({ page }) => {
    await installApi(page);
    await page.goto('/');

    await expect(page.getByTestId('watchlist-row')).toHaveCount(2);
    const summary = page.getByTestId('workbench-summary');
    await summary.getByRole('button', { name: /行情异常/ }).click();
    await expect(page).toHaveURL(/quote=abnormal/);
    await expect(page.getByTestId('watchlist-row')).toHaveCount(1);
    await expect(page.getByTestId('watchlist-row')).toHaveAttribute('data-symbol', '000001');

    await page.getByTestId('watchlist-search').fill('茅台');
    await expect(page).toHaveURL(/q=%E8%8C%85%E5%8F%B0/, { timeout: 2_000 });
    await expect(page.getByTestId('watchlist-empty')).toContainText('当前筛选没有匹配');

    await summary.getByRole('button', { name: /行情异常/ }).click();
    const link = page.getByTestId('watchlist-symbol-link').first();
    await expect(link).toHaveAttribute('href', /return_to=/);
  });

  test('桌面表格支持精确行情状态、排序、展开指标和可见结果数', async ({ page }) => {
    await installApi(page);
    await page.goto('/?sort=change_percent&order=desc&page_size=25');

    await expect(page.getByTestId('badge-fresh')).toBeVisible();
    await expect(page.getByTestId('badge-quote_stale').first()).toBeVisible();
    await expect(page.getByTestId('watchlist-count')).toContainText('显示 2 只');
    await page.getByTestId('sort-symbol').click();
    await expect(page).toHaveURL(/sort=symbol/);
    await expect(page.getByTestId('watchlist-row').first()).toHaveAttribute('data-symbol', '000001');

    await page.getByTestId('row-expand').first().click();
    await expect(page.getByTestId('row-details')).toContainText('今开');
    await expect(page.getByTestId('row-details')).toContainText('异动强度');
  });

  test('详情页上一只下一只沿用研究池范围、筛选与排序上下文', async ({ page }) => {
    await installApi(page);
    const returnTo = '/?scope=all&sort=symbol&order=asc&page_size=25';
    await page.goto(`/stocks/600519?return_to=${encodeURIComponent(returnTo)}`);

    const previous = page.getByRole('link', { name: '上一只' });
    await expect(previous).toHaveAttribute('href', /\/stocks\/000001/);
    await expect(previous).toHaveAttribute('href', /return_to=/);
    await expect(page.getByRole('link', { name: '返回筛选结果' })).toHaveAttribute(
      'href',
      returnTo,
    );
  });
});

test.describe('003 添加、移除与恢复闭环', () => {
  test('范围外股票可加入我的关注，并立即显示成功与回补进度', async ({ page }) => {
    let added = false;
    await installApi(page, {
      search: () => ({
        status: 200,
        body: {
          data: [{ symbol: '600999', name: '招商证券', exchange: 'SSE', industry: '证券' }],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
      addWatchlist: () => {
        added = true;
        return {
          status: 202,
          body: {
            data: {
              watchlist_item: { symbol: '600999', display_order: 0 },
              backfill_job: { ...BACKFILL_JOB_QUEUED, symbol: '600999' },
            },
            request_id: 'r',
          },
        };
      },
      watchlist: (ctx) => {
        const scope = ctx.url.searchParams.get('scope');
        if (scope === 'extra' && added) {
          return {
            status: 200,
            body: {
              data: [{
                symbol: '600999',
                name: '招商证券',
                display_order: 0,
                pool_source: 'extra',
                can_remove: true,
                is_current_universe_member: false,
                industry: '证券',
                quote: FRESH_QUOTE,
                market: TRADING_MARKET,
                analysis_status: 'waiting',
                backfill_job: { ...BACKFILL_JOB_QUEUED, symbol: '600999' },
              }],
              page: { next_cursor: null, has_more: false },
              request_id: 'r',
            },
          };
        }
        return { status: 200, body: { data: [], page: { next_cursor: null, has_more: false }, request_id: 'r' } };
      },
    });

    await page.goto('/');
    await page.getByText('添加到我的关注').click();
    await page.getByTestId('instrument-search-input').fill('600999');
    await page.getByTestId('instrument-search-submit').click();
    await page.getByTestId('add-600999').click();

    await expect(page).toHaveURL(/scope=extra/);
    await expect(page).toHaveURL(/q=600999/);
    await expect(page.getByRole('status').filter({ hasText: '已加入我的关注' })).toBeVisible();
    await expect(page.getByTestId('backfill-progress').first()).toBeVisible();
    await expect(page.getByTestId('watchlist-row')).toHaveAttribute('data-symbol', '600999');
  });

  test('移除前说明保留历史，成功后可撤销', async ({ page }) => {
    await installApi(page);
    await page.goto('/?scope=extra');
    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('历史行情、分析和预测会继续保留');
      await dialog.accept();
    });

    await page.getByTestId('row-remove').click();
    await expect(page.getByRole('status').filter({ hasText: '已移出我的关注' })).toBeVisible();
    await page.getByRole('button', { name: '撤销' }).click();
    await expect(page.getByRole('status').filter({ hasText: '已恢复到我的关注' })).toBeVisible();
  });
});
