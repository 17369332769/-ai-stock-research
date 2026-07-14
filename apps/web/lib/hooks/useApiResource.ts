'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export interface ApiResource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** 首次加载是否已结束（用于区分 loading 与"确实为空"）。 */
  loaded: boolean;
  reload: () => void;
}

/**
 * 极简数据获取 hook：请求在浏览器侧发出，便于 E2E 用路由拦截注入固定夹具，
 * 不依赖真实后端启动（spec §16.1：测试禁止访问公网）。
 */
export function useApiResource<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [nonce, setNonce] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    fetcherRef
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(cause);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        setLoaded(true);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, error, loading, loaded, reload };
}
