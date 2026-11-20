import { authFetch } from './auth.js';
import { apiURL } from './paths.js';
import { AppError, languageHeader, responseError } from '../ui/preferences-store.js';

import type { Login, LoginState } from './generated/types.gen.js';

export { apiURL };

export type Pagination = { page: number; page_size: number; total: number };

export class ApiError extends AppError {
  constructor(
    readonly status: number,
    readonly requestId: string | null,
    descriptor: AppError['descriptor'],
  ) {
    super(descriptor);
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function page(value: unknown): Pagination | null {
  if (!object(value)) return null;
  const { page, page_size, total } = value;
  if (
    typeof page !== 'number' || !Number.isSafeInteger(page) || page < 1 ||
    typeof page_size !== 'number' || !Number.isSafeInteger(page_size) || page_size < 1 ||
    typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0
  ) return null;
  return { page, page_size, total };
}

export function apiError(payload: unknown, status: number): ApiError {
  const id = object(payload) && typeof payload.request_id === 'string' ? payload.request_id : null;
  return new ApiError(status, id, responseError(payload, status).descriptor);
}

export async function readResource<T>(response: Response) {
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw apiError(payload, response.status);
  if (!object(payload) || !Object.hasOwn(payload, 'data')) throw new AppError('common.requestFailed');
  const pagination = page(payload.pagination);
  if ('pagination' in payload && !pagination) throw new AppError('common.requestFailed');
  return { data: payload.data as T, pagination, headers: response.headers };
}

export async function requestResource<T>(path: string, init?: RequestInit) {
  return readResource<T>(await authFetch(apiURL(path), init));
}

export async function requestData<T>(path: string, init?: RequestInit): Promise<T> {
  return (await requestResource<T>(path, init)).data;
}

export async function signIn(body: Login) {
  const response = await fetch(apiURL('auth/login'), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'Accept-Language': languageHeader() },
    body: JSON.stringify(body),
  });
  return (await readResource<LoginState>(response)).data;
}
