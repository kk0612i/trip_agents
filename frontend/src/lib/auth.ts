import { ApiError, requestJson } from './api';
import type { AuthResponse, AuthUser } from './types';
export type { AuthResponse, AuthUser } from './types';

/** 仅本地演示与旧认证数据使用宽松形状，在线响应使用严格 AuthResponse。 */
export interface CompatibleAuthResponse extends Partial<AuthResponse> {
  token?: string;
  email?: string;
}

const tokenKey = 'trip-agents:auth-token';
const userKey = 'trip-agents:auth-user';
const localUsersKey = 'trip-agents:demo-users';

export function getAuthToken(): string | null {
  return localStorage.getItem(tokenKey);
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(userKey);
    if (!raw) return null;
    const value = JSON.parse(raw) as AuthUser;
    return typeof value.id === 'string' && typeof value.email === 'string' ? value : null;
  } catch { return null; }
}

export function saveAuth(response: CompatibleAuthResponse, fallbackEmail: string): AuthUser {
  const token = response.access_token || response.token;
  const user = response.user || { id: fallbackEmail, email: response.email || fallbackEmail };
  if (token) localStorage.setItem(tokenKey, token);
  localStorage.setItem(userKey, JSON.stringify(user));
  return user;
}

export function clearAuth(): void {
  localStorage.removeItem(tokenKey);
  localStorage.removeItem(userKey);
}

function localUsers(): Record<string, { id: string; password: string }> {
  try { return JSON.parse(localStorage.getItem(localUsersKey) || '{}') as Record<string, { id: string; password: string }>; }
  catch { return {}; }
}

function saveLocalUsers(users: Record<string, { id: string; password: string }>): void {
  localStorage.setItem(localUsersKey, JSON.stringify(users));
}

function validateCredentials(email: string, password: string): void {
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new ApiError('INVALID_ARGUMENT', '请输入有效的邮箱地址。');
  if (password.length < 8) throw new ApiError('INVALID_ARGUMENT', '密码至少需要 8 位。');
}

export async function login(email: string, password: string, mode: 'demo' | 'api'): Promise<AuthUser> {
  const normalizedEmail = email.trim().toLowerCase();
  validateCredentials(normalizedEmail, password);
  if (mode === 'demo') {
    const record = localUsers()[normalizedEmail];
    if (!record || record.password !== password) throw new ApiError('AUTH_INVALID', '邮箱或密码不正确。');
    return saveAuth({ token: `demo-${record.id}`, user: { id: record.id, email: normalizedEmail } }, normalizedEmail);
  }
  return saveAuth(await requestJson<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ email: normalizedEmail, password }) }), normalizedEmail);
}

export async function register(email: string, password: string, mode: 'demo' | 'api'): Promise<AuthUser> {
  const normalizedEmail = email.trim().toLowerCase();
  validateCredentials(normalizedEmail, password);
  if (mode === 'demo') {
    const users = localUsers();
    if (users[normalizedEmail]) throw new ApiError('EMAIL_EXISTS', '该邮箱已注册，请直接登录。');
    const id = crypto.randomUUID();
    users[normalizedEmail] = { id, password };
    saveLocalUsers(users);
    return saveAuth({ token: `demo-${id}`, user: { id, email: normalizedEmail } }, normalizedEmail);
  }
  return saveAuth(await requestJson<AuthResponse>('/auth/register', { method: 'POST', body: JSON.stringify({ email: normalizedEmail, password }) }), normalizedEmail);
}
