/**
 * API 客户端。
 *
 * 唯一职责：发请求、解信封、把 HTTP 与错误码归一成 ApiError。
 * 不做任何业务计算（spec §5.1）。
 */

import {
  isErrorCode,
  type ClientErrorCode,
  type ErrorEnvelope,
  type ListEnvelope,
  type PageInfo,
} from './types';

export const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000/api/v1';

export const EMPTY_PAGE: PageInfo = { next_cursor: null, has_more: false };

export function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  const base = fromEnv && fromEnv.length > 0 ? fromEnv : DEFAULT_API_BASE_URL;
  return base.replace(/\/+$/, '');
}

export class ApiError extends Error {
  readonly code: ClientErrorCode;
  readonly status: number;
  readonly requestId: string | null;

  constructor(params: {
    code: ClientErrorCode;
    message: string;
    status: number;
    requestId?: string | null;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.code = params.code;
    this.status = params.status;
    this.requestId = params.requestId ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

type QueryValue = string | number | boolean | null | undefined;

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = `${apiBaseUrl()}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs.length > 0 ? `${url}?${qs}` : url;
}

function isErrorEnvelope(body: unknown): body is ErrorEnvelope {
  if (typeof body !== 'object' || body === null) return false;
  const error = (body as { error?: unknown }).error;
  return typeof error === 'object' && error !== null && 'code' in error;
}

function hasKey<K extends string>(body: unknown, key: K): body is Record<K, unknown> {
  return typeof body === 'object' && body !== null && !Array.isArray(body) && key in body;
}

/** 原始响应：body 未解信封。 */
interface RawBody {
  status: number;
  body: unknown;
  requestId: string | null;
}

async function request(
  path: string,
  init: RequestInit & { query?: Record<string, QueryValue> } = {},
): Promise<RawBody> {
  const { query, ...rest } = init;
  const url = buildUrl(path, query);

  let response: Response;
  try {
    response = await fetch(url, {
      ...rest,
      headers: {
        Accept: 'application/json',
        ...(rest.body ? { 'Content-Type': 'application/json' } : {}),
        ...rest.headers,
      },
      cache: 'no-store',
    });
  } catch {
    throw new ApiError({
      code: 'NETWORK_ERROR',
      message: `无法连接研究服务（${apiBaseUrl()}）`,
      status: 0,
    });
  }

  const text = await response.text();
  let body: unknown = null;
  if (text.length > 0) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      const { code, message, request_id: requestId } = body.error;
      throw new ApiError({
        code: isErrorCode(code) ? code : 'UNKNOWN',
        message,
        status: response.status,
        requestId,
      });
    }
    throw new ApiError({
      code: 'UNKNOWN',
      message: `请求失败（HTTP ${response.status}）`,
      status: response.status,
    });
  }

  const requestId = hasKey(body, 'request_id') ? String(body.request_id) : null;
  return { status: response.status, body, requestId };
}

/**
 * spec §7 的响应形态并不统一：列表 / 成绩单 / 回补作业带 {data, request_id} 信封，
 * 而 §7.2 snapshot、§7.4 predictions/latest 的样例是裸对象。两种都要能解析。
 */
function unwrapItem<T>(body: unknown): T {
  if (hasKey(body, 'data') && hasKey(body, 'request_id')) {
    return body.data as T;
  }
  return body as T;
}

export interface ItemResponse<T> {
  status: number;
  data: T;
  requestId: string | null;
}

export interface ListResponse<T> {
  status: number;
  items: T[];
  page: PageInfo;
  requestId: string | null;
}

/** 取单个对象（自动解信封）。 */
export async function apiGet<T>(
  path: string,
  query?: Record<string, QueryValue>,
): Promise<T> {
  const raw = await request(path, { method: 'GET', query });
  return unwrapItem<T>(raw.body);
}

/** 取单个对象并保留 HTTP 状态（202 首次回补进行中需要）。 */
export async function apiGetItem<T>(
  path: string,
  query?: Record<string, QueryValue>,
): Promise<ItemResponse<T>> {
  const raw = await request(path, { method: 'GET', query });
  return { status: raw.status, data: unwrapItem<T>(raw.body), requestId: raw.requestId };
}

/** 取列表（保留 page 游标信息）。 */
export async function apiGetList<T>(
  path: string,
  query?: Record<string, QueryValue>,
): Promise<ListResponse<T>> {
  const raw = await request(path, { method: 'GET', query });
  const { body } = raw;

  if (Array.isArray(body)) {
    return { status: raw.status, items: body as T[], page: EMPTY_PAGE, requestId: raw.requestId };
  }

  const envelope = (body ?? {}) as Partial<ListEnvelope<T>>;
  return {
    status: raw.status,
    items: Array.isArray(envelope.data) ? envelope.data : [],
    page: envelope.page ?? EMPTY_PAGE,
    requestId: raw.requestId,
  };
}

export async function apiPost<T>(path: string, body?: unknown): Promise<ItemResponse<T>> {
  const raw = await request(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: raw.status, data: unwrapItem<T>(raw.body), requestId: raw.requestId };
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<ItemResponse<T>> {
  const raw = await request(path, {
    method: 'PATCH',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: raw.status, data: unwrapItem<T>(raw.body), requestId: raw.requestId };
}

export async function apiDelete<T>(path: string): Promise<ItemResponse<T>> {
  const raw = await request(path, { method: 'DELETE' });
  return { status: raw.status, data: unwrapItem<T>(raw.body), requestId: raw.requestId };
}
