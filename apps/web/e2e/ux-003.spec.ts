import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { DAILY_BARS } from '../tests/fixtures';
import { BACKFILL_JOB_QUEUED, installApi } from './mock-api';

test.describe('003 渐进加载与作业闭环', () => {
  test('历史接口缓慢时，顶部股票行情先显示', async ({ page }) => {
    await installApi(page);
    await page.route('http://127.0.0.1:8000/api/v1/stocks/*/bars*', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: DAILY_BARS,
          page: { next_cursor: null, has_more: false },
          request_id: 'slow-bars',
        }),
      });
    });

    await page.goto('/stocks/600519');
    await expect(page.getByTestId('quote-price')).toContainText('1,215.04');
    await expect(page.getByText('正在加载历史行情')).toBeAttached();
    await expect(page.getByTestId('historical-line-chart')).toBeVisible({ timeout: 4_000 });
  });

  test('手动分析从排队自动轮询到完成，不要求反复点击', async ({ page }) => {
    let jobReads = 0;
    const requests = await installApi(page, {
      analysisRefresh: () => ({
        status: 202,
        body: {
          data: {
            ...BACKFILL_JOB_QUEUED,
            id: 'analysis-job-003',
            job_type: 'analysis_refresh',
            total_steps: 1,
            current_step: 'analyze',
          },
          request_id: 'r',
        },
      }),
      job: () => {
        jobReads += 1;
        return {
          status: 200,
          body: {
            data: {
              ...BACKFILL_JOB_QUEUED,
              id: 'analysis-job-003',
              job_type: 'analysis_refresh',
              status: 'succeeded',
              completed_steps: 1,
              total_steps: 1,
              current_step: 'analyze',
            },
            request_id: 'r',
          },
        };
      },
    });

    await page.goto('/stocks/600519');
    await page.getByTestId('analysis-refresh').first().click();
    await expect(page.getByTestId('analysis-refresh').first()).toContainText(/分析排队中|正在更新分析/);
    await expect(page.getByTestId('analysis-refresh').first()).toContainText('生成或更新分析', {
      timeout: 5_000,
    });
    expect(jobReads).toBeGreaterThan(0);
    expect(requests.some((request) => request.includes('/analyses/refresh'))).toBe(true);
    expect(requests.some((request) => request.includes('/jobs/analysis-job-003'))).toBe(true);
  });
});

test.describe('003 响应式实测', () => {
  for (const viewport of [
    { width: 320, height: 800 },
    { width: 390, height: 844 },
    { width: 768, height: 900 },
    { width: 1440, height: 1000 },
  ]) {
    test(`${viewport.width}px 不产生整页横向溢出`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installApi(page);
      await page.goto('/');
      await expect(page.getByTestId('watchlist')).toBeVisible();

      const size = await page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        bodyScroll: document.body.scrollWidth,
      }));
      expect(size.scroll).toBeLessThanOrEqual(size.client);
      expect(size.bodyScroll).toBeLessThanOrEqual(size.client);

      if (viewport.width <= 600) {
        await expect(page.getByTestId('watchlist-table')).toBeHidden();
        await expect(page.getByTestId('watchlist-mobile-card').first()).toBeVisible();
        const firstCardTop = await page.getByTestId('watchlist-mobile-card').first().evaluate(
          (element) => element.getBoundingClientRect().top + window.scrollY,
        );
        expect(firstCardTop).toBeLessThan(950);
        const targetHeights = await page
          .locator('.research-controls button:visible, .research-controls input:visible, .research-controls summary:visible')
          .evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().height));
        expect(targetHeights.every((height) => height >= 44)).toBe(true);
      } else {
        await expect(page.getByTestId('watchlist-table')).toBeVisible();
      }
    });
  }
});

test.describe('003 键盘与无障碍', () => {
  test('跳转链接、当前导航和图表数据表可用键盘访问', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    await page.keyboard.press('Tab');
    await expect(page.locator('.skip-link')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content')).toBeFocused();
    await expect(
      page
        .getByRole('navigation', { name: '主导航' })
        .getByRole('link', { name: '研究池', exact: true }),
    ).toHaveAttribute('aria-current', 'page');

    const dataTableSummary = page.getByText('查看历史行情数据表').first();
    await dataTableSummary.focus();
    await expect(dataTableSummary).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('table', { name: /日线数据/ })).toBeVisible();
  });

  for (const url of ['/', '/stocks/600519', '/scorecard', '/settings/data-sources']) {
    test(`${url} 通过 axe 主要页面扫描`, async ({ page }) => {
      await installApi(page);
      await page.goto(url);
      await expect(page.locator('main')).toBeVisible();
      const results = await new AxeBuilder({ page }).analyze();
      expect(
        results.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          targets: violation.nodes.map((node) => node.target),
        })),
      ).toEqual([]);
    });
  }
});
