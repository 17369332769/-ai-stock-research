import { expect, test } from '@playwright/test';

import { PREDICTION_5D, SCORECARD_5D, SCORECARD_TODAY, SETTLED_PREDICTION } from '../tests/fixtures';
import { apiError, installApi } from './mock-api';

/** E2E-5 成绩单（模型维度 + 股票维度） */

test.describe('E2E-5 预测成绩单', () => {
  test('模型维度：全部 / 最近100次 / 最近20次，含基准对照与未优于基准标记', async ({ page }) => {
    const windows: string[] = [];

    await installApi(page, {
      scorecard: (ctx) => {
        windows.push(ctx.window ?? '');
        const base = ctx.modelKey === 'a_share_today_lightgbm' ? SCORECARD_TODAY : SCORECARD_5D;
        const window = ctx.window === 'all' ? 'all' : Number(ctx.window);
        return { status: 200, body: { data: { ...base, window }, request_id: 'r' } };
      },
    });

    await page.goto('/scorecard');

    // 默认 100 窗口
    await expect(page.getByTestId('model-scorecards')).toHaveAttribute('data-window', '100');
    await expect(page.getByTestId('scorecard')).toHaveCount(2);

    const fiveDay = page.locator('[data-testid="scorecard"][data-model-key="a_share_5d_lightgbm"]');
    await expect(fiveDay.getByTestId('scorecard-eligible-count')).toContainText('100');
    await expect(fiveDay.getByTestId('scorecard-settled-count')).toContainText('98');
    await expect(fiveDay.getByTestId('scorecard-pending-count')).toContainText('2');
    await expect(fiveDay.getByTestId('scorecard-direction-accuracy')).toContainText('54.0%');
    await expect(fiveDay.getByTestId('scorecard-mae')).toContainText('0.0180');
    await expect(fiveDay.getByTestId('scorecard-brier')).toContainText('0.2470');
    await expect(fiveDay.getByTestId('scorecard-baseline-direction-accuracy')).toContainText('52.0%');
    await expect(fiveDay.getByTestId('scorecard-baseline-brier')).toContainText('0.2500');
    // 真实展示"未优于基准"
    await expect(fiveDay.getByTestId('scorecard-baseline-flag')).toContainText('未优于基准');

    // 切换窗口
    await page.getByTestId('window-20').click();
    await expect(page.getByTestId('model-scorecards')).toHaveAttribute('data-window', '20');

    await page.getByTestId('window-all').click();
    await expect(page.getByTestId('model-scorecards')).toHaveAttribute('data-window', 'all');

    expect(windows).toContain('100');
    expect(windows).toContain('20');
    expect(windows).toContain('all');
  });

  test('股票维度：逐条预测、实际结果、方向命中与绝对误差；未结算标注待结算', async ({ page }) => {
    await installApi(page, {
      predictionHistory: () => ({
        status: 200,
        body: {
          data: [SETTLED_PREDICTION, PREDICTION_5D],
          page: { next_cursor: null, has_more: false },
          request_id: 'r',
        },
      }),
    });

    await page.goto('/scorecard');

    const rows = page.getByTestId('prediction-history-row');
    await expect(rows).toHaveCount(2);

    // 已结算行
    const settled = rows.filter({ has: page.locator('[data-testid="history-actual-return"]') }).first();
    await expect(settled.getByTestId('history-actual-return')).toContainText('+1.28%');
    await expect(settled.getByTestId('history-direction-correct')).toContainText('未命中');

    // 未结算行标注待结算（不进入任何前端统计）
    await expect(page.getByText('待结算').first()).toBeVisible();

    // 概率与区间在历史表中同样成对出现
    await expect(page.getByTestId('history-probability').first()).toContainText('38.0%');
    await expect(page.getByTestId('history-interval').first()).toContainText('~');

    // 免责声明
    await expect(page.getByTestId('disclaimer').first()).toContainText('仅供研究，不构成投资建议');
  });

  test('没有可用模型时展示模型不可用', async ({ page }) => {
    await installApi(page, {
      systemStatus: () => ({
        status: 200,
        body: { sources: [], models: [], agent: null },
      }),
    });

    await page.goto('/scorecard');
    await expect(page.getByTestId('state-model_unavailable')).toContainText('模型不可用');
  });

  test('系统状态不可用（503）时成绩单页面明确降级', async ({ page }) => {
    await installApi(page, {
      systemStatus: () => apiError('MODEL_UNAVAILABLE', '没有可用模型版本', 503),
    });

    await page.goto('/scorecard');
    await expect(page.getByTestId('state-model_unavailable')).toBeVisible();
  });
});
