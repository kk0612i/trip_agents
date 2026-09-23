import { describe, expect, it } from 'vitest';
import { officialVersionNo, versionFromRun } from './versions';
import { emptyResult, type RunView, type SessionView } from './types';

const session: SessionView = { session_id: 's', trip_id: '42', current_version_no: 1, trip_request: null, latest_run_id: null, active_run_id: null, pending_question: null, created_at: '', updated_at: '' };
const run: RunView = { run_id: 'r', session_id: 's', client_request_id: 'c', message: '', status: 'running', intent: 'revise', base_version_no: 1, response: null, pending_question: null, missing_fields: [], result: emptyResult(), error: null, created_at: '', started_at: null, finished_at: null };
describe('正式行程与运行结果隔离', () => {
  it.each(['running', 'needs_input', 'failed', 'completed'] as const)('%s 没有保存回执时不替换正式版本', status => {
    const result = { ...run, status };
    expect(officialVersionNo(session, [result])).toBe(1);
    expect(versionFromRun(result)).toBeNull();
  });
  it('完成并确认保存后提升版本，即使会话查询尚未刷新', () => {
    expect(officialVersionNo(session, [{ ...run, status: 'completed', result: { ...emptyResult(), saved_version: { trip_id: '42', version_no: 2 } } }])).toBe(2);
  });
  it('失败但已保存不会由刷新指针自动提升，也不把失败草稿用作已保存快照', () => {
    const failed: RunView = { ...run, status: 'failed', result: { ...emptyResult(), saved_version: { trip_id: '42', version_no: 2 } } };
    expect(officialVersionNo({ ...session, current_version_no: 2 }, [failed])).toBe(1);
    expect(versionFromRun(failed)).toBeNull();
    expect(officialVersionNo({ ...session, current_version_no: 1 }, [{ ...failed, base_version_no: null, result: { ...failed.result, saved_version: { trip_id: '42', version_no: 1 } } }])).toBeNull();
  });
  it('历史分页载入的旧结果不会使当前版本回退', () => {
    expect(officialVersionNo({ ...session, current_version_no: 3 }, [{ ...run, status: 'completed', result: { ...emptyResult(), saved_version: { trip_id: '42', version_no: 1 } } }])).toBe(3);
  });
});
