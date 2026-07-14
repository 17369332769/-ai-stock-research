import { expect, test } from '@playwright/test';

import { PREDICTION_5D, PREDICTION_TODAY } from '../tests/fixtures';
import { apiError, installApi } from './mock-api';

/** E2E-4 预测 */

test.describe('E2E-4 预测', () => {
  test('概率与区间同时出现，含免责声明、模型版本与数据截止时间', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    const today = page.getByTestId('prediction-today_close');
    const next5d = page.getByTestId('prediction-next_5d');
    await expect(today).toBeVisible();
    await expect(next5d).toBeVisible();

    // 概率 + 区间必须同时出现（红线）
    await expect(next5d.getByTestId('prediction-probability')).toContainText('38.0%');
    await expect(next5d.getByTestId('prediction-interval')).toContainText('-4.10%');
    await expect(next5d.getByTestId('prediction-interval')).toContainText('+1.90%');
    await expect(next5d.getByTestId('prediction-expected-return')).toContainText('-1.10%');
    await expect(next5d.getByTestId('prediction-model-version')).toContainText('2026.07.14.1');
    await expect(next5d.getByTestId('prediction-data-cutoff')).toContainText('09:50');

    // 免责声明
    await expect(next5d.getByTestId('disclaimer')).toContainText('仅供研究，不构成投资建议');
    await expect(today.getByTestId('disclaimer')).toContainText('仅供研究，不构成投资建议');

    // 不允许只显示"看涨/看跌"
    const text = await page.getByTestId('prediction-next_5d').innerText();
    expect(text).not.toMatch(/看涨|看跌/);
  });

  test('模型未优于基准 → 显示「未优于基准」且置信度为低（spec §9.4）', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    const next5d = page.getByTestId('prediction-next_5d');
    await expect(next5d.getByTestId('baseline-flag')).toContainText('未优于基准');
    await expect(next5d.getByTestId('prediction-confidence')).toContainText('置信度：低');
    await expect(next5d.getByTestId('prediction-confidence')).toHaveAttribute(
      'data-confidence',
      'low',
    );

    // 优于基准的模型保留其置信度
    const today = page.getByTestId('prediction-today_close');
    await expect(today.getByTestId('baseline-flag')).toContainText('优于基准');
    await expect(today.getByTestId('prediction-confidence')).toContainText('置信度：中');
  });

  test('展开可查看滚动验证样本数、方向命中率、MAE、Brier 与基准（spec §3.3）', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    const details = page.getByTestId('prediction-next_5d').getByTestId('prediction-validation');
    await details.getByText('查看滚动验证表现').click();

    await expect(details.getByTestId('validation-settled-count')).toContainText('98');
    await expect(details.getByTestId('validation-direction-accuracy')).toContainText('54.0%');
    await expect(details.getByTestId('validation-mae')).toContainText('0.0180');
    await expect(details.getByTestId('validation-brier')).toContainText('0.2470');
    await expect(details.getByTestId('validation-baseline-direction-accuracy')).toContainText('52.0%');
  });

  test('模型不可用（503）→ 显示模型不可用，仍显示免责声明', async ({ page }) => {
    await installApi(page, {
      predictionLatest: () => apiError('MODEL_UNAVAILABLE', '没有可用模型版本', 503),
    });

    await page.goto('/stocks/600519');

    await expect(page.getByTestId('state-model_unavailable').first()).toContainText('模型不可用');
    await expect(page.getByTestId('prediction-probability')).toHaveCount(0);
    await expect(page.getByTestId('disclaimer').first()).toContainText('仅供研究，不构成投资建议');

    // 行情与文档不受影响
    await expect(page.getByTestId('quote-price')).toBeVisible();
    await expect(page.getByTestId('documents-list')).toBeVisible();
  });

  test('今日预测 09:45 前不可用（422 样本不足）→ 显示无预测', async ({ page }) => {
    await installApi(page, {
      predictionLatest: (ctx) =>
        ctx.horizon === 'today_close'
          ? apiError('INSUFFICIENT_DATA', '今日预测最早在 09:45 生成', 422)
          : { status: 200, body: PREDICTION_5D },
    });

    await page.goto('/stocks/600519');

    const today = page.getByTestId('prediction-today_close');
    await expect(today.getByTestId('state-no_prediction')).toContainText('无预测');
    await expect(today).toContainText('09:45');
    await expect(today.getByTestId('disclaimer')).toBeVisible();

    // 一周预测正常展示
    await expect(
      page.getByTestId('prediction-next_5d').getByTestId('prediction-probability'),
    ).toBeVisible();
  });

  test('回补进行中（202）→ 不显示未经训练的预测', async ({ page }) => {
    await installApi(page, {
      predictionLatest: () => ({
        status: 202,
        body: {
          data: {
            id: 'job-e2e-1',
            status: 'running',
            completed_steps: 1,
            total_steps: 3,
            current_step: 'minute_bars',
            error_code: null,
          },
          request_id: 'r',
        },
      }),
    });

    await page.goto('/stocks/600519');

    await expect(page.getByTestId('state-initial_backfill').first()).toContainText('首次回补');
    await expect(page.getByTestId('prediction-probability')).toHaveCount(0);
    await expect(page.getByTestId('prediction-interval')).toHaveCount(0);
  });

  test('今日预测的参考价固定为昨收（spec §7.4）', async ({ page }) => {
    await installApi(page);
    await page.goto('/stocks/600519');

    await expect(
      page.getByTestId('prediction-today_close').getByTestId('prediction-reference-price'),
    ).toContainText('1,211.03'); // 昨收
    expect(PREDICTION_TODAY.reference_price).toBe(1211.03);
  });
});
