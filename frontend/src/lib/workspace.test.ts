import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { emptyResult, type ItineraryVersion, type RunView, type SessionView } from './types';
import { useItinerary } from '../hooks/use-itinerary';

vi.stubEnv('VITE_DATA_MODE', 'api');
const { useWorkspace } = await import('../hooks/use-workspace');

const sessions: Record<string, SessionView> = Object.fromEntries(['session-a', 'session-b'].map((id) => [id, {
  session_id: id, trip_id: null, current_version_no: null, trip_request: null, latest_run_id: null,
  active_run_id: null, pending_question: null, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z',
}]));

function queued(id = 'session-a'): RunView {
  return { run_id: 'run-a', session_id: id, client_request_id: 'request-a', message: '长沙三天', status: 'queued',
    intent: null, base_version_no: null, response: null, pending_question: null, missing_fields: [], result: emptyResult(),
    error: null, created_at: '2026-09-20T10:00:00Z', started_at: null, finished_at: null };
}

class QuietEventSource {
  close = vi.fn(); addEventListener = vi.fn(); onerror = null;
}

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let workspace: ReturnType<typeof useWorkspace>;
let itinerary: ReturnType<typeof useItinerary>;

function version(no: number): ItineraryVersion {
  return { trip_id: '42', version_no: no, source_run_id: `version-run-${no}`, created_at: '',
    trip_request: { destination: '长沙', origin: null, days: 3, start_date: null, budget: null, traveler_count: 1, preferences: [], constraints: [], pace: 'balanced' },
    itinerary: { summary: `长沙版本 ${no}`, days: [], total_cost: null, currency: 'CNY' }, routes: [], validation: { passed: true, issues: [], checked_at: '' } };
}

function Harness({ id }: { id?: string }) {
  workspace = useWorkspace(id);
  itinerary = useItinerary(workspace.session, workspace.runs, workspace.mode);
  return null;
}

async function render(id?: string) {
  await act(async () => { root.render(createElement(QueryClientProvider, { client }, createElement(Harness, { id }))); });
  await flush();
}

async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 35)); });
}

describe('API 工作台会话与提交恢复', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('EventSource', QuietEventSource);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.spyOn(api, 'getSession').mockImplementation(async (id) => sessions[id]);
    vi.spyOn(api, 'getRuns').mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(api, 'getRun').mockResolvedValue(queued());
    vi.spyOn(api, 'getVersion').mockImplementation(async (_id, no) => version(no));
    vi.spyOn(api, 'getVersions').mockResolvedValue({ items: [1, 2].map(no => ({ trip_id: '42', version_no: no, source_run_id: `version-run-${no}`, summary: `长沙版本 ${no}`, created_at: '' })), next_cursor: null });
    vi.spyOn(api, 'submit').mockResolvedValue({ run_id: 'run-a', session_id: 'session-a', status: 'queued', status_url: '/runs/run-a', events_url: '/runs/run-a/events' });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    client.clear(); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });
  afterAll(() => vi.unstubAllEnvs());

  it('没有会话的 API 首页不读取或自动创建资源', async () => {
    const create = vi.spyOn(api, 'createSession');
    await render();
    expect(api.getSession).not.toHaveBeenCalled();
    expect(api.getRuns).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(workspace.isLoading).toBe(false);
    expect(workspace.session).toBeNull();
  });

  it('提交结果未知时禁止新消息，手动重试保留原始编号和版本', async () => {
    vi.mocked(api.submit).mockRejectedValueOnce(new ApiError('NETWORK_ERROR', '连接失败', true));
    await render('session-a');
    await act(async () => { await expect(workspace.submit('长沙三天')).rejects.toMatchObject({ code: 'NETWORK_ERROR' }); });
    const first = vi.mocked(api.submit).mock.calls[0][1];
    await act(async () => { await expect(workspace.submit('长沙四天')).rejects.toMatchObject({ code: 'SUBMISSION_UNCONFIRMED' }); });
    expect(api.submit).toHaveBeenCalledTimes(1);
    await act(async () => workspace.retry());
    expect(api.submit).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.submit).mock.calls[1][1]).toEqual(first);
    expect(localStorage.getItem('trip-agents:api:pending:/api/v1:session-a')).toBeNull();
  });

  it('切换会话后迟到的旧提交只影响原会话', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof api.submit>>) => void;
    vi.mocked(api.submit).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await render('session-a');
    let submitting!: Promise<void>;
    await act(async () => { submitting = workspace.submit('长沙三天'); });
    await render('session-b');
    await act(async () => {
      resolve({ run_id: 'run-a', session_id: 'session-a', status: 'queued', status_url: '/runs/run-a', events_url: '/runs/run-a/events' });
      await submitting;
    });
    expect(workspace.session?.session_id).toBe('session-b');
    expect(workspace.runs).toEqual([]);
    expect(workspace.isBusy).toBe(false);
  });

  it('刷新时从 active_run_id 恢复任务，并保持终态不被旧 GET 回退', async () => {
    vi.mocked(api.getSession).mockResolvedValue({ ...sessions['session-a'], active_run_id: 'run-a' });
    await render('session-a');
    await flush();
    expect(api.getRun).toHaveBeenCalledWith('run-a', expect.any(AbortSignal));
    expect(workspace.isBusy).toBe(true);
    const completed = { ...queued(), status: 'needs_input' as const, pending_question: '几天？', response: '几天？', finished_at: '2026-09-20T10:01:00Z' };
    await act(async () => {
      client.setQueryData(['workspace', 'api', 'run', 'session-a', 'run-a'], completed);
      await client.refetchQueries({ queryKey: ['workspace', 'api', 'run', 'session-a', 'run-a'] });
    });
    await flush();
    expect(workspace.runs.at(-1)?.status).toBe('needs_input');
  });

  it('查询错误优先重试读取，不因历史业务失败误提交新运行', async () => {
    const failed: RunView = { ...queued(), status: 'failed', response: '上游暂不可用', finished_at: '2026-09-20T10:01:00Z', error: { code: 'UPSTREAM_UNAVAILABLE', message: '上游暂不可用', retryable: true, details: {} } };
    vi.mocked(api.getRuns).mockResolvedValue({ items: [failed], next_cursor: null });
    await render('session-a');
    expect(workspace.runs).toHaveLength(1);
    vi.mocked(api.getRuns).mockRejectedValue(new ApiError('NETWORK_ERROR', '查询失败', true));
    await act(async () => { await client.invalidateQueries({ queryKey: ['workspace', 'api', 'history', 'session-a'] }); });
    await flush();
    expect(workspace.error?.message).toBe('查询失败');
    await act(async () => workspace.retry());
    expect(api.submit).not.toHaveBeenCalled();
  });

  it('明确的版本冲突刷新基准，用户重新发送使用新编号与最新版本', async () => {
    vi.mocked(api.getSession).mockResolvedValue({ ...sessions['session-a'], trip_id: '42', current_version_no: 2 });
    await render('session-a');
    vi.mocked(api.getSession).mockResolvedValue({ ...sessions['session-a'], trip_id: '42', current_version_no: 3 });
    vi.mocked(api.submit).mockRejectedValueOnce(new ApiError('VERSION_CONFLICT', '版本更新', false, { current_version_no: 3 }, 409));
    await act(async () => { await expect(workspace.submit('长沙三天')).rejects.toMatchObject({ code: 'VERSION_CONFLICT' }); });
    await flush();
    expect(workspace.session?.current_version_no).toBe(3);
    expect(localStorage.getItem('trip-agents:api:pending:/api/v1:session-a')).toBeNull();
    await act(async () => workspace.submit('长沙三天'));
    const [first, second] = vi.mocked(api.submit).mock.calls.map((call) => call[1]);
    expect(second.client_request_id).not.toBe(first.client_request_id);
    expect(second.expected_version_no).toBe(3);
  });

  it('独立读取正式版本和历史，即使产生该版本的聊天不在当前历史页', async () => {
    vi.mocked(api.getSession).mockResolvedValue({ ...sessions['session-a'], trip_id: '42', current_version_no: 2 });
    await render('session-a'); await flush();
    expect(itinerary.version?.version_no).toBe(2);
    expect(api.getVersion).toHaveBeenCalledWith('42', 2, expect.any(AbortSignal));
    await act(async () => itinerary.select(1)); await flush();
    expect(itinerary.version?.version_no).toBe(1);
    expect(itinerary.currentNo).toBe(2);
    expect(api.submit).not.toHaveBeenCalled();
  });

  it('保存后失败保留旧版本，用户仍可明确读取已保存的新版本', async () => {
    const failed: RunView = { ...queued(), status: 'failed', base_version_no: 1,
      result: { ...emptyResult(), saved_version: { trip_id: '42', version_no: 2 } },
      error: { code: 'RUN_INTERRUPTED', message: '保存后中断', retryable: true, details: {} } };
    vi.mocked(api.getSession).mockResolvedValue({ ...sessions['session-a'], trip_id: '42', current_version_no: 2 });
    vi.mocked(api.getRuns).mockResolvedValue({ items: [failed], next_cursor: null });
    await render('session-a'); await flush();
    expect(itinerary.currentNo).toBe(1);
    expect(itinerary.version?.version_no).toBe(1);
    await act(async () => workspace.retry());
    expect(api.submit).not.toHaveBeenCalled();
    await act(async () => itinerary.select(2)); await flush();
    expect(itinerary.version?.version_no).toBe(2);
    expect(itinerary.currentNo).toBe(1);
  });
});
