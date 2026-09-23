import { ApiError } from './api';
import { emptyResult, isTerminal, type ItineraryVersion, type Page, type Progress, type RunSubmission, type RunView, type SearchCandidate, type SearchResult, type SessionView, type TripRequest, type VersionSummary } from './types';
import { versionFromRun } from './versions';

export const DEMO_SESSION_ID = '01000000-0000-4000-8000-000000000001';
const prefix = 'trip-agents:demo:v1:';
const duration = 2400;

/** 本地策划样例，仅用于界面演示；价格、营业时间与室内属性均无实时依据。 */
export const demoCandidates: SearchCandidate[] = [
  { place_id: 'demo-hunan-museum', name: '湖南博物院', category: '博物馆', address: '长沙市开福区东风路 50 号', city: '长沙', longitude: 112.99309, latitude: 28.21857, source: 'demo', estimated_cost: null, opening_hours: null, indoor: null },
  { place_id: 'demo-yuelu-academy', name: '岳麓书院', category: '历史文化', address: '长沙市岳麓区麓山路 273 号', city: '长沙', longitude: 112.94037, latitude: 28.17869, source: 'demo', estimated_cost: null, opening_hours: null, indoor: null },
  { place_id: 'demo-orange-island', name: '橘子洲', category: '自然风光', address: '长沙市岳麓区橘子洲头', city: '长沙', longitude: 112.95944, latitude: 28.17476, source: 'demo', estimated_cost: null, opening_hours: null, indoor: null },
  { place_id: 'demo-taiping-street', name: '太平老街', category: '历史街区', address: '长沙市天心区太平街', city: '长沙', longitude: 112.97047, latitude: 28.1958, source: 'demo', estimated_cost: null, opening_hours: null, indoor: null },
];

interface DemoSnapshot {
  schema: 1;
  session: SessionView;
  runs: RunView[];
  planned: RunView | null;
}

function defaultRequest(): TripRequest {
  return { destination: null, origin: null, start_date: null, days: null, budget: null, traveler_count: 1, preferences: [], constraints: [], pace: 'balanced' };
}

function newSession(id: string): SessionView {
  const now = new Date().toISOString();
  return { session_id: id, trip_id: null, current_version_no: null, trip_request: null, latest_run_id: null, active_run_id: null, pending_question: null, created_at: now, updated_at: now };
}

function searchResult(candidates = demoCandidates): SearchResult {
  const reasons: Record<string, string> = {
    'demo-hunan-museum': '从马王堆文物了解湖湘历史，适合人文兴趣。',
    'demo-yuelu-academy': '走访千年学府，感受湖湘文化与古建筑。',
    'demo-orange-island': '沿湘江散步，感受长沙的江景与城市轮廓。',
    'demo-taiping-street': '穿行老街与巷弄，感受长沙的街巷文化。',
  };
  return {
    candidates,
    recommendations: candidates.map((place) => ({ place_id: place.place_id, reason: reasons[place.place_id] })),
    data_sources: ['本地长沙样例'], unmet_conditions: ['价格、营业时间与预约政策尚未核实'],
    summary: '本地长沙样例，供比较候选景点；尚未生成每日行程。', limit_reached: false,
  };
}

function makeRun(session: SessionView, submission: RunSubmission): RunView {
  return {
    run_id: crypto.randomUUID(), session_id: session.session_id, client_request_id: submission.client_request_id,
    message: submission.message, status: 'queued', intent: null, base_version_no: submission.expected_version_no,
    response: null, pending_question: null, missing_fields: [], result: emptyResult(), error: null,
    created_at: new Date().toISOString(), started_at: null, finished_at: null,
  };
}

function seed(): DemoSnapshot {
  const session = newSession(DEMO_SESSION_ID);
  const request: TripRequest = { ...defaultRequest(), destination: '长沙', days: 3, preferences: ['历史人文', '自然风光'] };
  const run = makeRun(session, { client_request_id: crypto.randomUUID(), message: '想去长沙玩三天，喜欢历史人文和自然风光，先看看有哪些景点。', expected_version_no: null });
  Object.assign(run, {
    status: 'completed', intent: 'direct_search', started_at: run.created_at, finished_at: run.created_at,
    response: '先收集了 4 个长沙候选景点，兼顾历史人文和自然风光。你可以继续告诉我更偏好的方向。以下来自本地演示样例，费用、预约和开放时间需要另行核实。',
    result: { ...emptyResult(), trip_request: request, search: searchResult() },
  });
  Object.assign(session, { trip_request: request, latest_run_id: run.run_id });
  return { schema: 1, session, runs: [run], planned: null };
}

function save(snapshot: DemoSnapshot): void {
  try { localStorage.setItem(prefix + snapshot.session.session_id, JSON.stringify(snapshot)); }
  catch { throw new ApiError('LOCAL_STORAGE_UNAVAILABLE', '浏览器无法保存本地演示会话，请检查存储权限或剩余空间。'); }
}

/** 演示按持久化开始时间推进，关闭页面也不会制造一个永久运行中的任务。 */
function settle(snapshot: DemoSnapshot): DemoSnapshot {
  if (!snapshot.planned) return snapshot;
  const index = snapshot.runs.findIndex((run) => run.run_id === snapshot.planned?.run_id);
  if (index < 0) throw new ApiError('LOCAL_DATA_INVALID', '本地演示记录不完整，请新建会话。');
  const current = snapshot.runs[index];
  const elapsed = Date.now() - Date.parse(current.created_at);
  if (elapsed >= duration) {
    const final = snapshot.planned;
    snapshot.runs[index] = final;
    snapshot.session = {
      ...snapshot.session, latest_run_id: final.run_id, active_run_id: null,
      pending_question: final.pending_question,
      trip_request: final.result.trip_request ?? snapshot.session.trip_request,
      trip_id: final.status === 'completed' && final.result.saved_version ? final.result.saved_version.trip_id : snapshot.session.trip_id,
      current_version_no: final.status === 'completed' && final.result.saved_version ? final.result.saved_version.version_no : snapshot.session.current_version_no,
      updated_at: final.finished_at!,
    };
    snapshot.planned = null;
    save(snapshot);
  } else if (elapsed >= 350 && current.status === 'queued') {
    snapshot.runs[index] = { ...current, status: 'running', started_at: new Date(Date.parse(current.created_at) + 350).toISOString() };
    save(snapshot);
  }
  return snapshot;
}

function read(id: string): DemoSnapshot {
  let raw: string | null;
  try { raw = localStorage.getItem(prefix + id); }
  catch { throw new ApiError('LOCAL_STORAGE_UNAVAILABLE', '浏览器禁止读取本地演示会话。'); }
  if (!raw) {
    if (id !== DEMO_SESSION_ID) throw new ApiError('SESSION_NOT_FOUND', '未找到这个本地演示会话，请新建会话。');
    const snapshot = seed(); save(snapshot); return snapshot;
  }
  let snapshot: DemoSnapshot;
  try { snapshot = JSON.parse(raw) as DemoSnapshot; }
  catch { throw new ApiError('LOCAL_DATA_INVALID', '本地演示记录损坏，请新建会话。'); }
  if (snapshot.schema !== 1 || snapshot.session?.session_id !== id || !Array.isArray(snapshot.runs)) {
    throw new ApiError('LOCAL_DATA_INVALID', '本地演示记录格式不兼容，请新建会话。');
  }
  return settle(snapshot);
}

function failure(run: RunView, request: TripRequest, message: string): RunView {
  return {
    ...run, status: 'failed', intent: 'other', response: message,
    result: { ...emptyResult(), trip_request: request },
    error: { code: 'CAPABILITY_UNAVAILABLE', message, retryable: false, details: {} },
  };
}

/** 只做可解释的样例规则匹配，不能把这些规则当成模型理解或真实搜索。 */
function planResult(run: RunView, previous: TripRequest | null, pendingSearch = false, previousVersion: ItineraryVersion | null = null): RunView {
  const request = { ...(previous ?? defaultRequest()), preferences: [...(previous?.preferences ?? [])], constraints: [...(previous?.constraints ?? [])] };
  const message = run.message;
  const destination = message.match(/(?:想去|去|(?<!添加)到|目的地[是为：:]?)\s*([\u4e00-\u9fa5]{2,6}?)(?=旅游|旅行|玩|逛|看|\d|[，。！？、\s]|$)/)?.[1];
  const origin = message.match(/从([\u4e00-\u9fa5]{2,6}?)出发/);
  if (origin) request.origin = origin[1];
  const mentionedOtherCity = message.replace(origin?.[0] || '', '').match(/北京|上海|广州|深圳|杭州|南京|成都|重庆|西安|武汉|苏州|桂林|三亚|厦门|青岛|天津|昆明|大理|丽江|拉萨|长沙县/);
  if (mentionedOtherCity || (destination && destination !== '长沙')) {
    request.destination = mentionedOtherCity?.[0] ?? destination ?? null;
    return failure(run, request, '当前本地演示只提供长沙候选景点，暂不支持其他目的地。可新建长沙会话，或接入真实 API 后查询。');
  }
  if (/长沙/.test(message)) request.destination = '长沙';
  // “第二天”指某一天，不是把旅行总天数改成两天。
  const dayMatch = message.match(/(?<![第\d])(\d{1,2}|[一二两三四五六七八九十])\s*[天日]/);
  if (dayMatch) {
    const chinese: Record<string, number> = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
    const days = chinese[dayMatch[1]] ?? Number(dayMatch[1]);
    if (days < 1 || days > 14) return failure(run, request, '旅行天数需要在 1 至 14 天之间，请调整后再发送。');
    request.days = days;
  }
  const budget = message.match(/预算\s*(\d+(?:\.\d{1,2})?)/);
  if (budget) request.budget = Number(budget[1]);
  if (/博物|历史|人文|文化/.test(message)) request.preferences = ['历史人文'];
  if (/自然|风景|风光|爬山|散步/.test(message)) request.preferences.push('自然风光');
  if (/美食/.test(message)) request.preferences.push('本地美食');
  if (/不要.*太满|轻松|慢慢|宽松/.test(message)) request.pace = 'relaxed';
  request.preferences = [...new Set(request.preferences)];
  if (/天气|酒店|住宿|预订|订票|导出/.test(message)) {
    return failure(run, request, '本地演示暂不支持天气、酒店、预订和导出，请继续修改旅行需求。');
  }
  // 演示仅按明确搜索词识别意图；查景点只需目的地，创建旅行才追问天数。
  const directSearch = !/添加到.*行程|换成|修改|生成|规划|安排/.test(message) && (pendingSearch || /找|搜索|查询|推荐|看看|看下|有哪些|有什么/.test(message));
  const missing = !request.destination ? 'destination' : !directSearch && !request.days ? 'days' : null;
  if (missing) {
    const question = missing === 'destination' ? '这次想去哪里？本地演示目前提供长沙样例。' : '打算在长沙玩几天？也可以一起告诉我感兴趣的景点类型。';
    return { ...run, status: 'needs_input', intent: directSearch ? 'direct_search' : 'create', response: question, pending_question: question,
      missing_fields: [missing], result: { ...emptyResult(), trip_request: request } };
  }
  if (request.destination !== '长沙') return failure(run, request, '这个会话的目的地没有长沙样例。请明确告诉我“去长沙”，或新建会话。');
  let candidates = demoCandidates;
  if (/只.*博物|博物馆|博物院/.test(message)) candidates = demoCandidates.filter((place) => place.category === '博物馆');
  else if (/只.*自然|只.*风景|只.*风光|爬山/.test(message)) candidates = demoCandidates.filter((place) => /自然|山岳/.test(place.category));
  const result = searchResult(candidates);
  if (!directSearch) {
    // 本地版本只是可交互的界面夹具，不调用模型、路线服务或真实数据库。
    const itinerary = previousVersion ? structuredClone(previousVersion.itinerary) : {
      summary: `长沙 ${request.days} 日游`, currency: 'CNY' as const, total_cost: null,
      days: Array.from({ length: request.days! }, (_, index) => ({
        day_index: index + 1, date: null, total_cost: null, walking_distance_km: 0,
        warnings: ['演示安排；交通时长、费用及预约政策待核实'],
        items: [demoCandidates[index % demoCandidates.length], demoCandidates[(index + 1) % demoCandidates.length]].map((place, itemIndex) => ({
          item_id: `demo-${index}-${itemIndex}`, place_id: place.place_id, name: place.name,
          start_time: itemIndex === 0 ? '09:00:00' : '14:00:00', duration_minutes: 120,
          travel_from_previous_minutes: itemIndex === 0 ? 0 : 30, address: place.address,
          notes: place.category === '博物馆' ? '出发前确认开放日期与预约要求。' : '开放时间与门票以官方公布为准。', estimated_cost: null,
        })),
      })),
    };
    while (itinerary.days.length > request.days!) itinerary.days.pop();
    if (itinerary.days.length !== request.days) return failure(run, request, '演示修改暂不支持增加天数，请新建旅行。');
    if (/岳麓书院.*换成.*橘子洲/.test(message)) {
      for (const day of itinerary.days) for (const item of day.items) if (item.place_id === 'demo-yuelu-academy') {
        const place = demoCandidates[2]; Object.assign(item, { place_id: place.place_id, name: place.name, address: place.address });
      }
    }
    if (/第二天.*不要.*太满/.test(message) && itinerary.days[1]) itinerary.days[1].items = itinerary.days[1].items.slice(0, 1);
    const addition = /添加到.*行程/.test(message) ? demoCandidates.find(place => message.includes(place.name)) : null;
    if (addition && !itinerary.days.some(day => day.items.some(item => item.place_id === addition.place_id))) {
      itinerary.days[0].items.push({ item_id: crypto.randomUUID(), place_id: addition.place_id, name: addition.name, start_time: '17:00:00', duration_minutes: 60, travel_from_previous_minutes: 30, address: addition.address, notes: '演示添加；请核实开放时间。', estimated_cost: null });
    }
    itinerary.summary = `长沙 ${request.days} 日游`;
    return { ...run, status: 'completed', intent: previousVersion ? 'revise' : 'create', response: `已保存本地演示版本 ${(run.base_version_no ?? 0) + 1}。时间安排仅为样例，费用和真实路线尚未核实。`,
      result: { ...emptyResult(), trip_request: request, search: result, itinerary, routes: [],
        validation: { passed: true, checked_at: new Date().toISOString(), issues: [{ severity: 'warning', code: 'DEMO_ONLY', message: '仅通过演示结构检查，未进行真实路线和预算校验。', day_index: null }] },
        saved_version: { trip_id: previousVersion?.trip_id || `demo-${run.session_id}`, version_no: (run.base_version_no ?? 0) + 1 } } };
  }
  return { ...run, status: 'completed', intent: 'direct_search', response: `已整理 ${candidates.length} 个长沙候选景点，来自本地演示样例。可在列表和地图中比较；费用、预约及营业时间尚未核实。`,
    result: { ...emptyResult(), trip_request: request, search: result } };
}

export const demo = {
  getVersion(id: string, no: number): ItineraryVersion {
    const version = read(id).runs.map(versionFromRun).find(item => item?.version_no === no);
    if (!version) throw new ApiError('VERSION_NOT_FOUND', '没有找到该行程版本。');
    return version;
  },
  getVersions(id: string, cursor: string | null): Page<VersionSummary> {
    const versions = read(id).runs.map(versionFromRun).filter((item): item is ItineraryVersion => item !== null).reverse();
    const start = cursor ? versions.findIndex(item => String(item.version_no) === cursor) + 1 : 0;
    const items = versions.slice(start, start + 20).map(version => ({ trip_id: version.trip_id, version_no: version.version_no, source_run_id: version.source_run_id, summary: version.itinerary.summary, created_at: version.created_at }));
    return { items, next_cursor: start + 20 < versions.length ? String(items.at(-1)!.version_no) : null };
  },
  createSession(): SessionView {
    const session = newSession(crypto.randomUUID());
    save({ schema: 1, session, runs: [], planned: null });
    return session;
  },
  getSession(id: string): SessionView { return read(id).session; },
  getRuns(id: string, cursor: string | null = null): Page<RunView> {
    const runs = [...read(id).runs].reverse();
    const start = cursor ? runs.findIndex((run) => run.run_id === cursor) + 1 : 0;
    if (cursor && start === 0) throw new ApiError('INVALID_ARGUMENT', '历史游标已失效，请刷新会话。');
    const items = runs.slice(start, start + 20);
    return { items, next_cursor: start + 20 < runs.length ? items[items.length - 1].run_id : null };
  },
  getRun(sessionId: string, runId: string): RunView {
    const run = read(sessionId).runs.find((item) => item.run_id === runId);
    if (!run) throw new ApiError('RUN_NOT_FOUND', '没有找到该演示运行。');
    return run;
  },
  submit(id: string, submission: RunSubmission): RunView {
    const snapshot = read(id);
    const existing = snapshot.runs.find((run) => run.client_request_id === submission.client_request_id);
    if (existing) {
      if (existing.message !== submission.message || existing.base_version_no !== submission.expected_version_no) {
        throw new ApiError('IDEMPOTENCY_CONFLICT', '重复提交编号对应的内容不同。');
      }
      return existing;
    }
    if (snapshot.session.active_run_id) throw new ApiError('SESSION_BUSY', '当前演示仍在处理中，请稍后再发送。');
    if (submission.expected_version_no !== snapshot.session.current_version_no) throw new ApiError('VERSION_CONFLICT', '行程版本已更新，请刷新后再发送。');
    const run = makeRun(snapshot.session, submission);
    const previousRun = snapshot.runs.find((item) => item.run_id === snapshot.session.latest_run_id);
    // 回答搜索轮次的目的地追问时，保留该轮的搜索意图，不额外要求天数。
    const pendingSearch = previousRun?.status === 'needs_input' && previousRun.intent === 'direct_search';
    const previousVersion = snapshot.runs.map(versionFromRun).find(version => version?.version_no === snapshot.session.current_version_no) ?? null;
    const final = planResult(run, snapshot.session.trip_request, pendingSearch, previousVersion);
    final.started_at = new Date(Date.parse(run.created_at) + 350).toISOString();
    final.finished_at = new Date(Date.parse(run.created_at) + duration).toISOString();
    snapshot.runs.push(run);
    snapshot.planned = final;
    snapshot.session.active_run_id = run.run_id;
    snapshot.session.updated_at = run.created_at;
    save(snapshot);
    return run;
  },
  progress(run: RunView): Progress | null {
    if (isTerminal(run.status)) return null;
    const elapsed = Date.now() - Date.parse(run.created_at);
    return elapsed < 1000
      ? { stage: 'understand', message: '演示：整理旅行需求' }
      : elapsed < 1500 ? { stage: 'search', message: '演示：读取本地长沙样例' }
        : elapsed < 2000 ? { stage: 'plan', message: '演示：整理每日安排' }
          : { stage: 'validate', message: '演示：检查样例结构' };
  },
};
