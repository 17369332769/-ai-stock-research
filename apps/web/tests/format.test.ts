import { describe, expect, it } from 'vitest';

import {
  EMPTY_PLACEHOLDER,
  changeTone,
  formatAgeSeconds,
  formatDate,
  formatDateTime,
  formatMetric,
  formatPrice,
  formatProbability,
  formatRatioAsPercent,
} from '@/lib/format';

describe('格式化只做渲染，不做业务计算（spec §5.1）', () => {
  it('比率按 API 给的小数原样换算成百分比', () => {
    expect(formatRatioAsPercent(0.0033)).toBe('+0.33%');
    expect(formatRatioAsPercent(-0.011)).toBe('-1.10%');
    expect(formatRatioAsPercent(0)).toBe('0.00%');
  });

  it('概率不带正负号', () => {
    expect(formatProbability(0.38)).toBe('38.0%');
    expect(formatProbability(0.615, 1)).toBe('61.5%');
  });

  it('指标原样保留小数', () => {
    expect(formatMetric(0.247)).toBe('0.2470');
    expect(formatMetric(0.018)).toBe('0.0180');
  });

  it('价格按两位小数展示', () => {
    expect(formatPrice(1215.04)).toBe('1,215.04');
  });

  it('age_seconds 只做可读化，不判断是否过期', () => {
    expect(formatAgeSeconds(45)).toBe('45 秒');
    expect(formatAgeSeconds(640)).toBe('10 分 40 秒');
    expect(formatAgeSeconds(7200)).toBe('2 小时 0 分');
  });

  it('时间按 Asia/Shanghai 渲染', () => {
    expect(formatDateTime('2026-07-14T09:50:00+08:00')).toContain('09:50:00');
    expect(formatDate('2026-07-10')).toContain('2026');
  });

  it('缺失值统一占位，不臆造 0', () => {
    expect(formatPrice(null)).toBe(EMPTY_PLACEHOLDER);
    expect(formatRatioAsPercent(undefined)).toBe(EMPTY_PLACEHOLDER);
    expect(formatProbability(null)).toBe(EMPTY_PLACEHOLDER);
    expect(formatMetric(null)).toBe(EMPTY_PLACEHOLDER);
    expect(formatAgeSeconds(null)).toBe(EMPTY_PLACEHOLDER);
    expect(formatDateTime(null)).toBe(EMPTY_PLACEHOLDER);
    expect(formatDateTime('not-a-date')).toBe(EMPTY_PLACEHOLDER);
  });

  it('涨跌着色只依据 API 给的收益率符号', () => {
    expect(changeTone(0.01)).toBe('up');
    expect(changeTone(-0.01)).toBe('down');
    expect(changeTone(0)).toBe('flat');
    expect(changeTone(null)).toBe('flat');
  });
});
