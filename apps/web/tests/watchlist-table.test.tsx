import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { WatchlistTable } from '@/components/WatchlistTable';
import { WATCHLIST } from './fixtures';

function rowSymbols(): string[] {
  return screen
    .getAllByTestId('watchlist-row')
    .map((row) => row.getAttribute('data-symbol') ?? '');
}

describe('自选股表格：排序与搜索（spec §13.1）', () => {
  it('默认按 display_order 排列', () => {
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);
    expect(rowSymbols()).toEqual(['600519', '000001']);
  });

  it('点击代码列切换升序 / 降序', async () => {
    const user = userEvent.setup();
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);

    await user.click(screen.getByTestId('sort-symbol'));
    expect(rowSymbols()).toEqual(['000001', '600519']);

    await user.click(screen.getByTestId('sort-symbol'));
    expect(rowSymbols()).toEqual(['600519', '000001']);
  });

  it('按涨跌幅排序使用 API 给的 change_percent', async () => {
    const user = userEvent.setup();
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);

    await user.click(screen.getByTestId('sort-change_percent'));
    // 平安银行 -1.21% 在前，茅台 +0.33% 在后
    expect(rowSymbols()).toEqual(['000001', '600519']);
  });

  it('按新鲜度排序时新鲜行在前，过期行在后', async () => {
    const user = userEvent.setup();
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);

    await user.click(screen.getByTestId('sort-freshness'));
    expect(rowSymbols()).toEqual(['600519', '000001']);
  });

  it('搜索按代码或名称过滤', async () => {
    const user = userEvent.setup();
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);

    await user.type(screen.getByTestId('watchlist-search'), '平安');
    expect(rowSymbols()).toEqual(['000001']);
    expect(screen.getByTestId('watchlist-count')).toHaveTextContent('共 2 只，显示 1 只');

    await user.clear(screen.getByTestId('watchlist-search'));
    await user.type(screen.getByTestId('watchlist-search'), '600519');
    expect(rowSymbols()).toEqual(['600519']);
  });

  it('搜索无结果时提示，不显示空表格行', async () => {
    const user = userEvent.setup();
    render(<WatchlistTable items={WATCHLIST} onRemove={vi.fn()} onMove={vi.fn()} />);

    await user.type(screen.getByTestId('watchlist-search'), '不存在的股票');
    expect(screen.queryAllByTestId('watchlist-row')).toHaveLength(0);
    expect(screen.getByTestId('watchlist-empty')).toHaveTextContent('没有匹配的自选股');
  });

  it('超过50只时只渲染当前页并支持翻页', async () => {
    const user = userEvent.setup();
    const manyItems = Array.from({ length: 120 }, (_, index) => ({
      ...WATCHLIST[0]!,
      symbol: String(index + 1).padStart(6, '0'),
      name: `股票${index + 1}`,
      display_order: index,
      quote: null,
    }));

    render(<WatchlistTable items={manyItems} onRemove={vi.fn()} onMove={vi.fn()} />);

    expect(rowSymbols()).toHaveLength(50);
    expect(rowSymbols()[0]).toBe('000001');
    expect(screen.getByTestId('watchlist-page-indicator')).toHaveTextContent('第 1 页，共 3 页');

    await user.click(screen.getByTestId('watchlist-page-next'));

    expect(rowSymbols()).toHaveLength(50);
    expect(rowSymbols()[0]).toBe('000051');
    expect(screen.getByTestId('watchlist-page-indicator')).toHaveTextContent('第 2 页，共 3 页');
  });

  it('移除与排序回调把 symbol 传给页面（页面负责调 API）', async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    const onMove = vi.fn();
    render(<WatchlistTable items={WATCHLIST} onRemove={onRemove} onMove={onMove} />);

    await user.click(screen.getAllByTestId('row-remove')[0]!);
    expect(onRemove).toHaveBeenCalledWith('600519');

    await user.click(screen.getAllByTestId('row-move-down')[0]!);
    expect(onMove).toHaveBeenCalledWith('600519', 'down');
  });

  it('自选股为空时提示从沪深300添加', () => {
    render(<WatchlistTable items={[]} onRemove={vi.fn()} onMove={vi.fn()} />);
    expect(screen.getByTestId('watchlist-empty')).toHaveTextContent('沪深300');
  });
});
