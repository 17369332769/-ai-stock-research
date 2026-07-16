import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useApiResource } from '@/lib/hooks/useApiResource';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function Harness({
  requestKey,
  load,
}: {
  requestKey: string;
  load: (key: string) => Promise<string>;
}) {
  const resource = useApiResource(() => load(requestKey), [requestKey]);
  return (
    <div>
      <span data-testid="key">{requestKey}</span>
      <span data-testid="state">
        {resource.loading ? 'loading' : resource.error ? 'error' : resource.data ?? 'empty'}
      </span>
    </div>
  );
}

describe('useApiResource 请求上下文隔离', () => {
  it('依赖切换的首次渲染就隐藏旧数据，旧请求晚返回也不能覆盖新请求', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const load = (key: string) => (key === 'csi300' ? first.promise : second.promise);
    const view = render(<Harness requestKey="csi300" load={load} />);

    await act(async () => first.resolve('沪深300列表'));
    expect(screen.getByTestId('state')).toHaveTextContent('沪深300列表');

    view.rerender(<Harness requestKey="extra" load={load} />);
    expect(screen.getByTestId('key')).toHaveTextContent('extra');
    expect(screen.getByTestId('state')).toHaveTextContent('loading');
    expect(screen.getByTestId('state')).not.toHaveTextContent('沪深300列表');

    await act(async () => second.resolve('我的关注列表'));
    expect(screen.getByTestId('state')).toHaveTextContent('我的关注列表');
  });

  it('切换后先完成的旧请求不会出现在新上下文', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const view = render(
      <Harness requestKey="600519" load={(key) => (key === '600519' ? first.promise : second.promise)} />,
    );

    view.rerender(
      <Harness requestKey="000001" load={(key) => (key === '600519' ? first.promise : second.promise)} />,
    );
    await act(async () => first.resolve('贵州茅台旧快照'));
    expect(screen.getByTestId('state')).toHaveTextContent('loading');
    expect(screen.getByTestId('state')).not.toHaveTextContent('贵州茅台');

    await act(async () => second.resolve('平安银行新快照'));
    expect(screen.getByTestId('state')).toHaveTextContent('平安银行新快照');
  });
});
