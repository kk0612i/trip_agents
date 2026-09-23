import type { SessionView, TripRequest } from './types';

export interface SessionEntry { id: string; title: string; updatedAt: string; version: number | null }
const key = (mode: string) => `trip-agents:session-index:${mode}:${mode === 'api' ? import.meta.env.VITE_API_BASE_URL || '/api/v1' : 'local'}`;
export function readSessionIndex(mode: string): SessionEntry[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key(mode)) || '[]');
    return Array.isArray(value) ? value.filter((item): item is SessionEntry => typeof item?.id === 'string' && typeof item?.title === 'string' && typeof item?.updatedAt === 'string') : [];
  } catch { return []; }
}
export function rememberSession(mode: string, session: SessionView, officialRequest?: TripRequest): SessionEntry[] {
  const previous = readSessionIndex(mode);
  const request = officialRequest || session.trip_request;
  const previousTitle = session.trip_id && !officialRequest ? previous.find(item => item.id === session.session_id)?.title : null;
  const title = previousTitle || (request?.destination ? `${request.destination}${request.days ? ` ${request.days} 日游` : '之旅'}` : '新旅行');
  const entries = [{ id: session.session_id, title, updatedAt: session.updated_at, version: session.current_version_no }, ...previous.filter(item => item.id !== session.session_id)]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  try { localStorage.setItem(key(mode), JSON.stringify(entries)); } catch { /* 导航索引写入失败不影响服务端会话。 */ }
  return entries;
}
