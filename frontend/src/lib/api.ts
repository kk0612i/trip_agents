import { isTerminal, type ItineraryVersion, type VersionSummary, type Page, type Progress, type PublicError, type RunEvent, type RunReceipt, type RunSubmission, type RunView, type SessionView } from './types';

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

function authToken(): string | null {
  try { return localStorage.getItem('trip-agents:auth-token'); } catch { return null; }
}

/** 同时保留 HTTP 状态和排查编号，界面不需要匹配服务端中文错误文本。 */
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
    public readonly details: Record<string, unknown> = {},
    public readonly status = 0,
    public readonly requestId: string | null = null,
  ) { super(message); this.name = 'ApiError'; }
}

/** 网络失败不能证明 POST 未受理；调用者必须保留原始请求编号。 */
export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const timeout = AbortSignal.timeout(20_000);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init, signal, cache: 'no-store',
      headers: { Accept: 'application/json', ...(authToken() ? { Authorization: `Bearer ${authToken()}` } : {}), ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError('NETWORK_ERROR', '连接服务失败，尚不能确认消息是否受理。请恢复连接后重试。', true);
  }
  let body: unknown;
  try { body = await response.json(); }
  catch {
    throw new ApiError('INVALID_RESPONSE', '服务返回了无法识别的响应，请检查接口服务。', false, {}, response.status, response.headers.get('X-Request-Id'));
  }
  if (!response.ok) {
    const envelope = body as { error?: Partial<PublicError>; request_id?: string; detail?: string };
    const error = envelope?.error;
    const code = error?.code || 'HTTP_ERROR';
    const messages: Record<string, string> = {
      SESSION_BUSY: '当前会话仍有任务运行，请等待完成后再发送。',
      VERSION_CONFLICT: '行程版本已更新。已刷新会话，请核对最新内容后重新发送。',
      IDEMPOTENCY_CONFLICT: '这次提交的编号与先前内容冲突，请刷新后重新发送。',
    };
    const statusMessages: Record<number, string> = { 401: '邮箱或密码不正确。', 409: '该邮箱已注册，请直接登录。' };
    throw new ApiError(code, messages[code] || error?.message || envelope?.detail || statusMessages[response.status] || `请求失败（HTTP ${response.status}）`, error?.retryable ?? false,
      error?.details || {}, response.status, envelope?.request_id || response.headers.get('X-Request-Id'));
  }
  return body as T;
}

/** 只构造契约允许的三个字段，网络重试复用整个对象。 */
export function createSubmission(message: string, version: number | null): RunSubmission {
  const normalized = message.trim();
  if (!normalized || Array.from(normalized).length > 4000) {
    throw new ApiError('INVALID_ARGUMENT', '消息需要包含 1 至 4000 个字符。');
  }
  return { client_request_id: crypto.randomUUID(), message: normalized, expected_version_no: version };
}

export const api = {
  getVersion: (id: string, version: number, signal?: AbortSignal) => requestJson<ItineraryVersion>(`/trips/${encodeURIComponent(id)}/versions/${version}`, { signal }),
  getVersions: (id: string, cursor: string | null, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '20' });
    if (cursor) params.set('cursor', cursor);
    return requestJson<Page<VersionSummary>>(`/trips/${encodeURIComponent(id)}/versions?${params}`, { signal });
  },
  createSession: () => requestJson<SessionView>('/sessions', { method: 'POST', body: '{}' }),
  getSession: (id: string, signal?: AbortSignal) => requestJson<SessionView>(`/sessions/${encodeURIComponent(id)}`, { signal }),
  getRun: (id: string, signal?: AbortSignal) => requestJson<RunView>(`/runs/${encodeURIComponent(id)}`, { signal }),
  getRuns: (id: string, cursor: string | null, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '20' });
    if (cursor) params.set('cursor', cursor);
    return requestJson<Page<RunView>>(`/sessions/${encodeURIComponent(id)}/runs?${params}`, { signal });
  },
  submit: (id: string, submission: RunSubmission) => requestJson<RunReceipt>(`/sessions/${encodeURIComponent(id)}/runs`, {
    method: 'POST', body: JSON.stringify(submission),
  }),
};

export interface EventHandlers {
  onProgress: (progress: Progress) => void;
  onTerminal: (run: RunView) => void;
  onDisconnect?: () => void;
}

/** 每次订阅只观察一个运行；重复重放、其他运行和非法事件都不影响当前状态。 */
export function observeRunEvents(runId: string, handlers: EventHandlers): () => void {
  const stream = new EventSource(`${baseUrl}/runs/${encodeURIComponent(runId)}/events`);
  let closed = false;
  let lastSeq = 0;
  const close = () => { closed = true; stream.close(); };
  const accept = (type: string, event: MessageEvent<string>) => {
    if (closed) return;
    let envelope: RunEvent;
    try { envelope = JSON.parse(event.data) as RunEvent; } catch { return; }
    if (envelope.run_id !== runId || !Number.isSafeInteger(envelope.seq) || envelope.seq <= lastSeq ||
      event.lastEventId !== `${runId}:${envelope.seq}`) return;
    if (type === 'progress') {
      const progress = envelope.data as Progress;
      if (!progress || !['understand', 'search', 'plan', 'validate', 'save'].includes(progress.stage) || typeof progress.message !== 'string') return;
      lastSeq = envelope.seq;
      handlers.onProgress(progress);
    } else if (type === 'run.started') {
      lastSeq = envelope.seq;
      handlers.onProgress({ stage: 'understand', message: '正在理解旅行需求' });
    } else {
      const run = envelope.data as RunView;
      if (!run || run.run_id !== runId || !isTerminal(run.status) || type !== `run.${run.status}`) return;
      lastSeq = envelope.seq;
      close();
      handlers.onTerminal(run);
    }
  };
  for (const type of ['run.started', 'progress', 'run.completed', 'run.needs_input', 'run.failed']) {
    stream.addEventListener(type, (event) => accept(type, event as MessageEvent<string>));
  }
  // 原生 EventSource 自动带 Last-Event-ID 重连；GET 轮询始终保留作为权威兜底。
  stream.onerror = () => { if (!closed) handlers.onDisconnect?.(); };
  return close;
}
