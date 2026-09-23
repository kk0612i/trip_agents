import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, createSubmission, observeRunEvents } from '../lib/api';
import { demo, DEMO_SESSION_ID } from '../lib/demo';
import { isTerminal, type Progress, type RunSubmission, type RunView, type SessionView } from '../lib/types';

const mode: 'demo' | 'api' = import.meta.env.VITE_DATA_MODE === 'api' ? 'api' : 'demo';
const pendingPrefix = `trip-agents:api:pending:${import.meta.env.VITE_API_BASE_URL || '/api/v1'}:`;

function pendingSubmission(id: string): RunSubmission | null {
  try {
    const raw = localStorage.getItem(pendingPrefix + id);
    if (!raw) return null;
    const pending = JSON.parse(raw) as RunSubmission;
    if (typeof pending.client_request_id !== 'string' || typeof pending.message !== 'string' ||
      !(pending.expected_version_no === null || Number.isInteger(pending.expected_version_no))) {
      throw new Error('invalid pending submission');
    }
    return pending;
  } catch { throw new ApiError('LOCAL_DATA_INVALID', '无法读取上次待确认提交，请检查浏览器存储。'); }
}

function storePending(id: string, submission: RunSubmission | null): void {
  try {
    if (submission) localStorage.setItem(pendingPrefix + id, JSON.stringify(submission));
    else localStorage.removeItem(pendingPrefix + id);
  } catch { throw new ApiError('LOCAL_STORAGE_UNAVAILABLE', '无法保存消息确认信息，请检查浏览器存储后重试。'); }
}

/** 状态均绑定会话键，异步完成的旧请求不会覆盖用户刚切换到的新会话。 */
export function useWorkspace(sessionId?: string) {
  const id = sessionId || (mode === 'demo' ? DEMO_SESSION_ID : undefined);
  const client = useQueryClient();
  const sessionKey = ['workspace', mode, 'session', id] as const;
  const historyKey = ['workspace', mode, 'history', id] as const;
  const [submissionError, setSubmissionError] = useState<{ id: string; error: Error } | null>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);
  const [tracked, setTracked] = useState<{ sessionId: string; runId: string } | null>(null);
  const [eventProgress, setEventProgress] = useState<{ runId: string; value: Progress } | null>(null);
  const inFlight = useRef(new Set<string>());
  const refreshedTerminals = useRef(new Set<string>());

  const sessionQuery = useQuery({
    queryKey: sessionKey, enabled: !!id,
    queryFn: ({ signal }) => mode === 'demo' ? demo.getSession(id!) : api.getSession(id!, signal),
    retry: 1,
    refetchInterval: (query) => query.state.data?.active_run_id ? (mode === 'demo' ? 400 : 2000) : false,
  });
  const historyQuery = useInfiniteQuery({
    queryKey: historyKey, enabled: !!id,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => mode === 'demo' ? demo.getRuns(id!, pageParam) : api.getRuns(id!, pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    retry: 1,
  });
  const session = sessionQuery.data ?? null;
  const runId = session?.active_run_id || (tracked && tracked.sessionId === id ? tracked.runId : null) || session?.latest_run_id;
  const runKey = ['workspace', mode, 'run', id, runId] as const;
  const runQuery = useQuery({
    queryKey: runKey, enabled: !!id && !!runId,
    queryFn: async ({ signal }) => {
      const received = mode === 'demo' ? demo.getRun(id!, runId!) : await api.getRun(runId!, signal);
      // SSE 已发布的终态不可被更早发起、较晚返回的 GET 覆盖。
      const cached = client.getQueryData<RunView>(runKey);
      return cached && isTerminal(cached.status) ? cached : received;
    },
    refetchInterval: (query) => query.state.data && isTerminal(query.state.data.status) ? false : (mode === 'demo' ? 400 : 2000),
    retry: 1,
  });
  const currentRun = runQuery.data;
  const runIsTerminal = !!currentRun && isTerminal(currentRun.status);

  useEffect(() => {
    if (tracked && tracked.sessionId === id && session?.latest_run_id === tracked.runId && !session.active_run_id) setTracked(null);
  }, [id, session?.latest_run_id, session?.active_run_id, tracked]);

  useEffect(() => {
    if (!id || mode !== 'api') return;
    try {
      if (pendingSubmission(id)) setSubmissionError({ id, error: new ApiError('SUBMISSION_UNCONFIRMED', '上次消息尚未确认受理结果，请点击重试使用原编号确认。', true) });
    } catch (error) { setSubmissionError({ id, error: error as Error }); }
  }, [id]);

  useEffect(() => {
    if (!id || !runId || mode !== 'api' || runIsTerminal) return;
    let alive = true;
    const stop = observeRunEvents(runId, {
      onProgress: (value) => { if (alive) setEventProgress({ runId, value }); },
      onTerminal: (run) => {
        // 先取消可能仍返回 running 的 GET，再发布不可回退的终态缓存。
        void client.cancelQueries({ queryKey: ['workspace', mode, 'run', id, runId] }).then(() => {
          if (alive && run.session_id === id) client.setQueryData(['workspace', mode, 'run', id, runId], run);
        });
      },
      onDisconnect: () => { void client.invalidateQueries({ queryKey: ['workspace', mode, 'run', id, runId] }); },
    });
    return () => { alive = false; stop(); };
  }, [client, id, runId, runIsTerminal]);

  useEffect(() => {
    if (!id || !currentRun || !isTerminal(currentRun.status) || refreshedTerminals.current.has(currentRun.run_id)) return;
    refreshedTerminals.current.add(currentRun.run_id);
    void client.invalidateQueries({ queryKey: ['workspace', mode, 'session', id] });
    void client.invalidateQueries({ queryKey: ['workspace', mode, 'history', id] });
  }, [client, id, currentRun]);

  const runs = useMemo(() => {
    // 服务端按提交序号倒序分页；反转后用于聊天的时间顺序，重复页边界按运行编号去重。
    const history = historyQuery.data?.pages.flatMap((page) => page.items) ?? [];
    const unique = new Map<string, RunView>();
    for (const run of [...history].reverse()) unique.set(run.run_id, run);
    if (currentRun) unique.set(currentRun.run_id, currentRun);
    return [...unique.values()];
  }, [historyQuery.data, currentRun]);
  const latest = runs.at(-1);
  const lastSearchRun = [...runs].reverse().find((run) => run.status === 'completed' && run.result.search !== null);
  const searchResult = lastSearchRun?.result.search ?? null;
  const queryError = sessionQuery.error || historyQuery.error || runQuery.error;
  const runError = latest?.status === 'failed' && latest.error
    ? new ApiError(latest.error.code, latest.error.message, latest.error.retryable, latest.error.details) : null;
  const error = (submissionError && submissionError.id === id ? submissionError.error : null) || queryError || runError;
  const isBusy = sendingId === id || !!session?.active_run_id || !!(currentRun && !isTerminal(currentRun.status));
  const progress = isBusy
    ? mode === 'demo' && currentRun ? demo.progress(currentRun)
      : eventProgress && eventProgress.runId === runId ? eventProgress.value
        : { stage: 'queued' as const, message: mode === 'demo' ? '演示：准备处理消息' : '消息已提交，等待处理' }
    : null;

  async function send(submission: RunSubmission): Promise<void> {
    if (!id) throw new ApiError('SESSION_REQUIRED', '请先新建会话。');
    if (inFlight.current.has(id)) throw new ApiError('SESSION_BUSY', '消息正在提交，请稍后。');
    const targetId = id;
    inFlight.current.add(targetId);
    setSendingId(targetId);
    setSubmissionError(null);
    try {
      if (mode === 'api') storePending(targetId, submission);
      const accepted = mode === 'demo' ? demo.submit(targetId, submission) : await api.submit(targetId, submission);
      if (mode === 'api') storePending(targetId, null);
      setTracked({ sessionId: targetId, runId: accepted.run_id });
      if (mode === 'demo') client.setQueryData(['workspace', mode, 'run', targetId, accepted.run_id], accepted);
      client.setQueryData<SessionView>(['workspace', mode, 'session', targetId], (old) => old ? {
        ...old, active_run_id: isTerminal(accepted.status) ? null : accepted.run_id,
      } : old);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['workspace', mode, 'session', targetId] }),
        client.invalidateQueries({ queryKey: ['workspace', mode, 'history', targetId] }),
        client.invalidateQueries({ queryKey: ['workspace', mode, 'run', targetId, accepted.run_id] }),
      ]);
    } catch (caught) {
      const error = caught instanceof Error ? caught : new Error('提交失败，请重试。');
      // 确定被拒绝的请求可重新编辑；不确定受理结果的请求保留原始 payload。
      if (mode === 'api' && error instanceof ApiError && error.status >= 400 && error.status < 500 && error.code !== 'INVALID_RESPONSE') {
        storePending(targetId, null);
      }
      if (error instanceof ApiError && (error.code === 'SESSION_BUSY' || error.code === 'VERSION_CONFLICT')) {
        await client.invalidateQueries({ queryKey: ['workspace', mode, 'session', targetId] });
        if (typeof error.details.active_run_id === 'string') setTracked({ sessionId: targetId, runId: error.details.active_run_id });
      }
      setSubmissionError({ id: targetId, error });
      throw error;
    } finally {
      inFlight.current.delete(targetId);
      setSendingId((current) => current === targetId ? null : current);
    }
  }

  async function submit(message: string): Promise<void> {
    try {
      if (!id || !session) throw new ApiError('SESSION_REQUIRED', '请先新建或等待会话加载完成。');
      const pending = mode === 'api' ? pendingSubmission(id) : null;
      if (pending && pending.message !== message.trim()) throw new ApiError('SUBMISSION_UNCONFIRMED', '上次消息的受理状态尚未确认，请先点击重试，再发送新消息。', true);
      await send(pending ?? createSubmission(message, session.current_version_no));
    } catch (caught) {
      if (id) setSubmissionError({ id, error: caught as Error });
      throw caught;
    }
  }

  async function retry(): Promise<void> {
    if (!id) return;
    const pending = mode === 'api' ? pendingSubmission(id) : null;
    if (pending) { await send(pending); return; }
    // 业务失败是新一轮运行；已保存副作用或不可重试错误必须由用户查看后另行决策。
    if (!queryError && !(submissionError && submissionError.id === id) && !isBusy &&
      latest?.status === 'failed' && latest.error?.retryable && !latest.result.saved_version) {
      await send(createSubmission(latest.message, session?.current_version_no ?? null));
      return;
    }
    setSubmissionError(null);
    await Promise.all([
      sessionQuery.refetch(), historyQuery.refetch(), ...(runId ? [runQuery.refetch()] : []),
    ]);
  }

  async function createSession(): Promise<string> {
    const created = mode === 'demo' ? demo.createSession() : await api.createSession();
    client.setQueryData(['workspace', mode, 'session', created.session_id], created);
    return created.session_id;
  }

  function refresh(): void {
    setSubmissionError(null);
    void client.invalidateQueries({ queryKey: sessionKey });
    void client.invalidateQueries({ queryKey: historyKey });
    if (runId) void client.invalidateQueries({ queryKey: runKey });
  }

  return {
    session, runs, candidates: searchResult?.candidates ?? [], recommendations: searchResult?.recommendations ?? [],
    request: session?.trip_request ?? null, searchResult,
    candidatesArePrevious: !!lastSearchRun && !!latest && lastSearchRun.run_id !== latest.run_id,
    isLoading: !!id && (sessionQuery.isPending || historyQuery.isPending), isBusy, progress, error,
    submit, retry, createSession, refresh, mode,
    loadMore: () => { if (historyQuery.hasNextPage && !historyQuery.isFetchingNextPage) void historyQuery.fetchNextPage(); },
    hasMore: !!historyQuery.hasNextPage,
  };
}
