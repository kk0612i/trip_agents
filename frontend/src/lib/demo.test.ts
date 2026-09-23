import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createSubmission } from './api';
import { demo, DEMO_SESSION_ID } from './demo';

describe('本地演示的能力和状态边界', () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-20T10:00:00Z')); });
  afterEach(() => vi.useRealTimers());

  it('首屏只有带 demo 来源的长沙样例，费用与行程不伪造', () => {
    const seed = demo.getRuns(DEMO_SESSION_ID).items[0];
    expect(seed.result.search?.candidates).toHaveLength(4);
    expect(seed.result.search?.candidates.every((candidate) => candidate.source === 'demo' && candidate.estimated_cost === null)).toBe(true);
    expect(seed.result.itinerary).toBeNull();
    expect(seed.result.saved_version).toBeNull();
  });

  it('新会话为空，需求追问结束当前运行，补充消息创建新运行', () => {
    const session = demo.createSession();
    expect(demo.getRuns(session.session_id).items).toEqual([]);
    const first = demo.submit(session.session_id, createSubmission('我想去长沙', null));
    expect(first.status).toBe('queued');
    vi.advanceTimersByTime(3000);
    expect(demo.getRun(session.session_id, first.run_id)).toMatchObject({ status: 'needs_input', intent: 'create', missing_fields: ['days'] });
    expect(demo.getSession(session.session_id).active_run_id).toBeNull();
    const next = demo.submit(session.session_id, createSubmission('三天，想看博物馆', null));
    vi.advanceTimersByTime(3000);
    const final = demo.getRun(session.session_id, next.run_id);
    expect(final.run_id).not.toBe(first.run_id);
    expect(final.result.trip_request).toMatchObject({ destination: '长沙', days: 3 });
    expect(final.result.search?.candidates.map((place) => place.name)).toEqual(['湖南博物院']);
  });

  it('明确搜索长沙博物馆时不要求天数，也不虚构旅行天数', () => {
    const session = demo.createSession();
    const run = demo.submit(session.session_id, createSubmission('找长沙的博物馆', null));
    vi.advanceTimersByTime(3000);
    const final = demo.getRun(session.session_id, run.run_id);
    expect(final).toMatchObject({ status: 'completed', intent: 'direct_search', pending_question: null, missing_fields: [] });
    expect(final.result.trip_request).toMatchObject({ destination: '长沙', days: null });
    expect(final.result.search?.candidates.map((place) => place.name)).toEqual(['湖南博物院']);
  });

  it('搜索缺目的地时只追问目的地，补充后不再追问天数', () => {
    const session = demo.createSession();
    const first = demo.submit(session.session_id, createSubmission('推荐几个景点', null));
    vi.advanceTimersByTime(3000);
    expect(demo.getRun(session.session_id, first.run_id)).toMatchObject({ status: 'needs_input', intent: 'direct_search', missing_fields: ['destination'] });
    const next = demo.submit(session.session_id, createSubmission('长沙', null));
    vi.advanceTimersByTime(3000);
    const final = demo.getRun(session.session_id, next.run_id);
    expect(final.status).toBe('completed');
    expect(final.result.trip_request).toMatchObject({ destination: '长沙', days: null });
    expect(final.result.search?.candidates).toHaveLength(4);
  });

  it('无需常驻计时器，重新读取即可恢复已经完成的演示运行', () => {
    const session = demo.createSession();
    const run = demo.submit(session.session_id, createSubmission('长沙三天', null));
    vi.setSystemTime(new Date('2026-09-20T10:05:00Z'));
    expect(demo.getSession(session.session_id).active_run_id).toBeNull();
    expect(demo.getRun(session.session_id, run.run_id).status).toBe('completed');
  });

  it('幂等命中先于会话忙碌，同编号不同内容被拒绝', () => {
    const session = demo.createSession();
    const submission = createSubmission('长沙三天', null);
    const run = demo.submit(session.session_id, submission);
    expect(demo.submit(session.session_id, submission).run_id).toBe(run.run_id);
    expect(() => demo.submit(session.session_id, { ...submission, message: '上海三天' })).toThrow('重复提交编号');
    expect(() => demo.submit(session.session_id, createSubmission('长沙两天', null))).toThrow('当前演示仍在处理中');
    expect(demo.getRuns(session.session_id).items).toHaveLength(1);
  });

  it.each(['去上海玩三天', '帮我预订酒店', '长沙明天天气怎么样'])('未开放请求“%s”不会返回伪搜索或行程', (message) => {
    const run = demo.submit(DEMO_SESSION_ID, createSubmission(message, null));
    vi.advanceTimersByTime(3000);
    const final = demo.getRun(DEMO_SESSION_ID, run.run_id);
    expect(final.status).toBe('failed');
    expect(final.error?.code).toBe('CAPABILITY_UNAVAILABLE');
    expect(final.result.search).toBeNull();
    expect(final.result.itinerary).toBeNull();
    expect(demo.getRuns(DEMO_SESSION_ID).items[1].result.search?.candidates).toHaveLength(4);
  });

  it('损坏的本地数据必须显式报错', () => {
    localStorage.setItem(`trip-agents:demo:v1:${DEMO_SESSION_ID}`, '{broken json');
    expect(() => demo.getSession(DEMO_SESSION_ID)).toThrow('本地演示记录损坏');
  });

  it('保存新版本、搜索与失败均保留旧版本快照，刷新后可恢复', () => {
    const session = demo.createSession();
    const id = session.session_id;
    demo.submit(id, createSubmission('从广州出发去长沙玩三天，喜欢博物馆和本地美食', null));
    vi.advanceTimersByTime(3000);
    expect(demo.getSession(id)).toMatchObject({ current_version_no: 1, trip_request: { origin: '广州' } });
    const first = demo.getVersion(id, 1);
    demo.submit(id, createSubmission('把岳麓书院换成橘子洲，第二天不要安排太满', 1));
    expect(demo.getVersion(id, 1)).toEqual(first);
    vi.advanceTimersByTime(3000);
    expect(demo.getSession(id).current_version_no).toBe(2);
    expect(demo.getVersion(id, 2).itinerary.days).toHaveLength(3);
    expect(demo.getVersion(id, 2).trip_request.days).toBe(3);
    expect(demo.getVersion(id, 2).itinerary.days[1].items).toHaveLength(1);
    expect(demo.getVersion(id, 2).itinerary.days.flatMap(day => day.items).some(item => item.name === '岳麓书院')).toBe(false);
    expect(demo.getVersion(id, 1)).toEqual(first);
    demo.submit(id, createSubmission('长沙有哪些博物馆', 2));
    vi.advanceTimersByTime(3000);
    expect(demo.getSession(id).current_version_no).toBe(2);
    demo.submit(id, createSubmission('请把太平老街添加到当前行程。', 2));
    vi.advanceTimersByTime(3000);
    expect(demo.getSession(id)).toMatchObject({ current_version_no: 3, trip_request: { destination: '长沙', days: 3 } });
    demo.submit(id, createSubmission('预订酒店', 3));
    vi.advanceTimersByTime(3000);
    expect(demo.getSession(id).current_version_no).toBe(3);
    expect(demo.getVersions(id, null).items.map(version => version.version_no)).toEqual([3, 2, 1]);
    expect(() => demo.submit(id, createSubmission('修改行程', 1))).toThrow('行程版本已更新');
  });
});
