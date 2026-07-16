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

interface ResourceState<T> {
  generation: number;
  data: T | null;
  error: unknown;
  loading: boolean;
  loaded: boolean;
}

function sameInputs(left: readonly unknown[], right: readonly unknown[]): boolean {
  return left.length === right.length && left.every((value, index) => Object.is(value, right[index]));
}

/**
 * 极简数据获取 hook：请求在浏览器侧发出，便于 E2E 用路由拦截注入固定夹具，
 * 不依赖真实后端启动（spec §16.1：测试禁止访问公网）。
 */
export function useApiResource<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
): ApiResource<T> {
  const [nonce, setNonce] = useState(0);
  const requestSequence = useRef(0);
  const inputsRef = useRef<readonly unknown[]>([...deps, nonce]);
  const generationRef = useRef(0);

  const currentInputs = [...deps, nonce];
  if (!sameInputs(inputsRef.current, currentInputs)) {
    inputsRef.current = currentInputs;
    generationRef.current += 1;
  }
  const currentGeneration = generationRef.current;
  const [state, setState] = useState<ResourceState<T>>({
    generation: currentGeneration,
    data: null,
    error: null,
    loading: true,
    loaded: false,
  });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    const generation = generationRef.current;
    let cancelled = false;
    setState({ generation, data: null, error: null, loading: true, loaded: false });

    fetcherRef
      .current()
      .then((value) => {
        if (cancelled || requestSequence.current !== requestId) return;
        setState({ generation, data: value, error: null, loading: false, loaded: true });
      })
      .catch((cause: unknown) => {
        if (cancelled || requestSequence.current !== requestId) return;
        setState({ generation, data: null, error: cause, loading: false, loaded: true });
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  if (state.generation !== currentGeneration) {
    return { data: null, error: null, loading: true, loaded: false, reload };
  }
  return { ...state, reload };
}
