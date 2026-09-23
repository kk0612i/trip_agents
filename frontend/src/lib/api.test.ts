import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, createSubmission, observeRunEvents } from './api';
import { emptyResult, type RunView } from './types';

describe('提交与错误边界', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('网络失败不自动 POST，原提交对象可以使用同一编号确认结果', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(new Response(JSON.stringify({ run_id: 'run-1' }), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    const submission = createSubmission('  长沙三天  ', null);
    await expect(api.submit('session-1', submission)).rejects.toMatchObject({ code: 'NETWORK_ERROR', retryable: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await api.submit('session-1', submission);
    const first = JSON.parse(fetchMock.mock.calls[0][1].body);
    const second = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(first).toEqual(second);
    expect(Object.keys(first).sort()).toEqual(['client_request_id', 'expected_version_no', 'message']);
    expect(first.message).toBe('长沙三天');
  });

  it.each(['SESSION_BUSY', 'VERSION_CONFLICT'])('%s 保留明确错误码和排查信息', async (code) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ request_id: 'trace-123', error: { code, message: 'server message', retryable: false, details: { active_run_id: 'active' } } }), { status: 409 })));
    await expect(api.submit('session-1', createSubmission('长沙', 2))).rejects.toMatchObject({ code, status: 409, requestId: 'trace-123', details: { active_run_id: 'active' } });
  });

  it('失败查询不能变成空候选成功响应', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('gateway unavailable', { status: 502 })));
    await expect(api.getRun('run-1')).rejects.toBeInstanceOf(ApiError);
  });

  it('4000 个 Unicode 字符按码点计数，空消息和超长消息不能提交', () => {
    expect(createSubmission('😀'.repeat(4000), null).message.length).toBe(8000);
    expect(() => createSubmission('😀'.repeat(4001), null)).toThrow(ApiError);
    expect(() => createSubmission('   ', null)).toThrow(ApiError);
  });
});

class FakeEventSource extends EventTarget {
  static latest: FakeEventSource;
  close = vi.fn();
  onerror: (() => void) | null = null;
  constructor(public url: string) { super(); FakeEventSource.latest = this; }
  emit(type: string, seq: number, data: unknown, runId = 'run-1') {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify({ run_id: runId, seq, occurred_at: new Date().toISOString(), data }), lastEventId: `${runId}:${seq}` }));
  }
}

function terminal(status: 'completed' | 'needs_input' | 'failed'): RunView {
  return {
    run_id: 'run-1', session_id: 'session-1', client_request_id: 'request-1', message: '长沙', status, intent: null,
    base_version_no: null, response: '完成', pending_question: status === 'needs_input' ? '几天？' : null,
    missing_fields: [], result: emptyResult(), error: status === 'failed' ? { code: 'INTERNAL_ERROR', message: '失败', retryable: false, details: {} } : null,
    created_at: new Date().toISOString(), started_at: null, finished_at: new Date().toISOString(),
  };
}

describe('阶段事件与终态', () => {
  beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource));
  afterEach(() => vi.unstubAllGlobals());

  it('重复、错序和其他运行的事件不能重复更新当前进度', () => {
    const onProgress = vi.fn();
    const stop = observeRunEvents('run-1', { onProgress, onTerminal: vi.fn() });
    const source = FakeEventSource.latest;
    source.emit('progress', 2, { stage: 'search', message: '查询景点' });
    source.emit('progress', 2, { stage: 'search', message: '重复' });
    source.emit('progress', 1, { stage: 'understand', message: '旧阶段' });
    source.emit('progress', 3, { stage: 'search', message: '其他会话' }, 'run-other');
    expect(onProgress).toHaveBeenCalledTimes(1);
    stop();
    expect(source.close).toHaveBeenCalled();
  });

  it.each(['completed', 'needs_input', 'failed'] as const)('%s 关闭连接且拒绝之后的进度', (status) => {
    const onProgress = vi.fn(); const onTerminal = vi.fn();
    observeRunEvents('run-1', { onProgress, onTerminal });
    const source = FakeEventSource.latest;
    source.emit(`run.${status}`, 3, terminal(status));
    source.emit('progress', 4, { stage: 'search', message: '迟到进度' });
    source.emit(`run.${status}`, 3, terminal(status));
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(onProgress).not.toHaveBeenCalled();
    expect(source.close).toHaveBeenCalledOnce();
  });

  it('事件名称与数据状态冲突时不发布假终态', () => {
    const onTerminal = vi.fn();
    observeRunEvents('run-1', { onProgress: vi.fn(), onTerminal });
    FakeEventSource.latest.emit('run.completed', 1, terminal('failed'));
    expect(onTerminal).not.toHaveBeenCalled();
    expect(FakeEventSource.latest.close).not.toHaveBeenCalled();
  });
});
