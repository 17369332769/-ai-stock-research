import { expect, test } from '@playwright/test';

import { ANOMALY_WITHOUT_EVIDENCE, CLOSED_MARKET, SNAPSHOT, STALE_QUOTE } from '../tests/fixtures';
import { installApi } from './mock-api';

/**
 * E2E-3 研究页
 * E2E-8 行情过期（180 秒红线）
 * E2E-9 休市
 */

test.describe('E2E-3 研究页', () => {
  test('信息顺序固定：行情 → 异动 → 预测 → 公告新闻 → 历史相似行情 → 模型成绩（spec §3.2）', async ({
    page,
  }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    await expect(page.getByTestId('research-page')).toBeVisible();

    // 顶部：最新价、涨跌幅、行情时间、数据源、新鲜度
    await expect(page.getByTestId('quote-price')).toContainText('1,215.04');
    await expect(page.getByTestId('quote-change-percent')).toContainText('+0.33%');
    await expect(page.getByTestId('quote-observed-at')).toContainText('09:50');
    await expect(page.getByTestId('quote-source')).toContainText('eastmoney_via_akshare');
    await expect(page.getByTestId('quote-freshness')).toContainText('新鲜');

    // 区块顺序
    const sections = page.locator('[data-order]');
    await expect(sections).toHaveCount(5);
    const ids = await sections.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-section')),
    );
    expect(ids).toEqual(['anomaly', 'prediction', 'documents', 'analogs', 'scorecard']);

    // DOM 顺序即视觉顺序
    const orders = await sections.evaluateAll((nodes) =>
      nodes.map((node) => Number(node.getAttribute('data-order'))),
    );
    expect(orders).toEqual([1, 2, 3, 4, 5]);
  });

  test('每条 AI 结论都有可点击证据（spec §3.2 红线）', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    const evidence = page.getByTestId('evidence-link').first();
    await expect(evidence).toBeVisible();
    await expect(evidence).toHaveAttribute('href', /cninfo\.com\.cn/);
    await expect(page.getByTestId('evidence-quote').first()).toContainText('净利润同比增长');

    // 公告与新闻区块可见，且带原文链接
    await expect(page.getByTestId('documents-list')).toBeVisible();
    await expect(page.getByTestId('document-link').first()).toHaveAttribute('href', /http/);
  });

  test('无证据的异动结论显示「原因未知」与固定文本', async ({ page }) => {
    await installApi(page, {
      analyses: (ctx) => ({
        status: 200,
        body: {
          data: ctx.type === 'anomaly' ? [ANOMALY_WITHOUT_EVIDENCE] : [],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
    });

    await page.goto('/stocks/600519');

    await expect(page.getByTestId('evidence-unknown').first()).toContainText('原因未知');
    await expect(page.getByTestId('analysis-direction').first()).toContainText('未知');
    await expect(page.getByTestId('analysis-summary').first()).toContainText('未找到可验证事件原因');
  });

  test('无文档时展示「无文档」状态', async ({ page }) => {
    await installApi(page, {
      documents: () => ({
        status: 200,
        body: { data: [], page: { next_cursor: null, has_more: false }, request_id: 'r' },
      }),
    });

    await page.goto('/stocks/600519');
    await expect(page.getByTestId('state-no_documents')).toContainText('无文档');
  });

  test('页面不出现任何交易入口或收益承诺', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');
    await expect(page.getByTestId('research-page')).toBeVisible();

    const text = (await page.locator('body').innerText()).replace(/\s/g, '');
    for (const forbidden of ['买入', '卖出', '下单', '委托', '开户', '加仓', '减仓', '持仓收益', '稳赚', '保本']) {
      expect(text).not.toContain(forbidden);
    }
    await expect(page.getByRole('button', { name: /买|卖|下单|交易/ })).toHaveCount(0);
  });
});

test.describe('E2E-8 行情过期（>180 秒）', () => {
  test('整页显示「行情可能已过期」，且不把旧行情标记为实时', async ({ page }) => {
    await installApi(page, {
      snapshot: () => ({ status: 200, body: { ...SNAPSHOT, quote: STALE_QUOTE } }),
    });

    await page.goto('/stocks/600519');

    // 整页状态条
    await expect(page.getByTestId('state-quote_stale')).toContainText('行情可能已过期');
    await expect(page.getByTestId('state-quote_stale')).toContainText('180');
    // 头部徽标
    await expect(page.getByTestId('badge-quote_stale')).toBeVisible();
    // 红线：不得标记实时
    await expect(page.getByTestId('badge-realtime')).toHaveCount(0);
    await expect(page.getByTestId('price-label')).not.toContainText('实时');
    await expect(page.getByTestId('quote-freshness')).toContainText('已过期');
  });
});

test.describe('E2E-9 休市', () => {
  test('显示「休市」与最新交易日，最新收盘价不标成实时', async ({ page }) => {
    await installApi(page, {
      snapshot: () => ({ status: 200, body: { ...SNAPSHOT, market: CLOSED_MARKET } }),
    });

    await page.goto('/stocks/600519');

    await expect(page.getByTestId('badge-market_closed')).toContainText('休市');
    await expect(page.getByTestId('state-market_closed')).toBeVisible();
    await expect(page.getByTestId('latest-trading-day')).toContainText('2026');
    await expect(page.getByTestId('price-label')).toContainText('最新收盘价');
    await expect(page.getByTestId('badge-realtime')).toHaveCount(0);

    const text = await page.getByTestId('quote-header').innerText();
    expect(text).not.toContain('实时');
  });
});
