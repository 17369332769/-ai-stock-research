import { expect, test } from '@playwright/test';

import { SYSTEM_STATUS_DEGRADED } from '../tests/fixtures';
import { apiError, installApi } from './mock-api';

/** E2E-7 数据源故障降级（spec §8 / §15） */

test.describe('E2E-7 数据源故障降级', () => {
  test('行情源 424 失败时页面不崩溃：显示数据源失败，历史文档仍可访问', async ({ page }) => {
    await installApi(page, {
      snapshot: () => apiError('PROVIDER_UNAVAILABLE', 'AKShare 行情源连续失败', 424),
    });

    await page.goto('/stocks/600519');

    // 明确的失败状态
    const failed = page.getByTestId('state-provider_failed').first();
    await expect(failed).toContainText('数据源失败');
    await expect(failed).toContainText('AKShare 行情源连续失败');

    // 不把缺失行情伪装成实时
    await expect(page.getByTestId('badge-realtime')).toHaveCount(0);
    await expect(page.getByTestId('quote-missing')).toContainText('暂无行情数据');

    // 已有历史数据仍可访问（spec §15）
    await expect(page.getByTestId('documents-list')).toBeVisible();
    await expect(page.getByTestId('analogs-table')).toBeVisible();
    await expect(page.getByTestId('scorecard').first()).toBeVisible();
  });

  test('自选股页在行情源失败时给出数据源失败状态与重试入口', async ({ page }) => {
    await installApi(page, {
      watchlist: () => apiError('PROVIDER_UNAVAILABLE', '行情源不可用', 424),
    });

    await page.goto('/');
    await expect(page.getByTestId('state-provider_failed').first()).toContainText('数据源失败');
    await expect(page.getByRole('button', { name: '重试' })).toBeVisible();
  });

  test('数据源状态页展示具体失败源、最后成功时间与模型连接状态（spec §13.1）', async ({ page }) => {
    await installApi(page, {
      systemStatus: () => ({ status: 200, body: SYSTEM_STATUS_DEGRADED }),
    });

    await page.goto('/settings/data-sources');

    // 页面级失败提示，点名失败源
    await expect(page.getByTestId('state-provider_failed').first()).toContainText('AKShare 行情');

    // 失败源卡片：状态、最后成功时间、连续失败次数、失败原因
    const failedCard = page.locator('[data-testid="data-source-card"][data-source-key="akshare_quotes"]');
    await expect(failedCard).toHaveAttribute('data-status', 'failed');
    await expect(failedCard.getByTestId('source-status')).toContainText('失败');
    await expect(failedCard.getByTestId('source-last-success')).toContainText('09:20');
    await expect(failedCard.getByTestId('source-failures')).toContainText('5');
    await expect(failedCard.getByTestId('source-error')).toContainText('PROVIDER_UNAVAILABLE');

    // 正常源仍标记为正常
    const okCard = page.locator('[data-testid="data-source-card"][data-source-key="cn_disclosure"]');
    await expect(okCard.getByTestId('source-status')).toContainText('正常');

    // 模型不可用 + 原因（PSI 漂移）
    await expect(page.getByTestId('state-model_unavailable').first()).toBeVisible();
    const model = page.locator('[data-testid="model-card"][data-model-key="a_share_today_lightgbm"]');
    await expect(model.getByTestId('model-status')).toContainText('不可用');
    await expect(model.getByTestId('model-reason')).toContainText('PSI');

    // 未优于基准的模型在状态页也标注
    const baselineModel = page.locator('[data-testid="model-card"][data-model-key="a_share_5d_lightgbm"]');
    await expect(baselineModel.getByTestId('model-baseline-flag')).toContainText('未优于基准');
  });

  test('数据源状态接口本身失败时，页面明确降级而不是空白', async ({ page }) => {
    await installApi(page, {
      systemStatus: () => apiError('PROVIDER_UNAVAILABLE', '状态服务不可用', 424),
    });

    await page.goto('/settings/data-sources');
    await expect(page.getByTestId('state-provider_failed').first()).toContainText('数据源失败');
  });
});
