import { useState } from 'react';
import { AlertCircle, Check, CheckCircle2, Circle, Clock3, LoaderCircle, Map, MapPin, MessageCircle, RotateCcw, Route as RouteIcon, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PlacesPanel } from './places-panel';
import { MapPanel } from './map-panel';
import type { useItinerary } from '@/hooks/use-itinerary';
import type { ItineraryVersion, Progress, Recommendation, RunView, SearchCandidate, SearchResult, TripRequest } from '@/lib/types';

interface Props {
  itinerary: ReturnType<typeof useItinerary>;
  latest?: RunView;
  request: TripRequest | null;
  progress: Progress | null;
  busy: boolean;
  loading: boolean;
  candidates: SearchCandidate[];
  recommendations: Recommendation[];
  searchResult: SearchResult | null;
  candidatesArePrevious: boolean;
  mode: 'demo' | 'api';
  onRetry: () => Promise<void>;
  onContinue: () => void;
  onAsk: (text: string) => void;
  onAdd: (name: string) => Promise<void>;
  canAdd: boolean;
  versionSelection: number;
}

const fields: Record<string, string> = { destination: '目的地', origin: '出发地', start_date: '出发日期', days: '旅行天数', budget: '预算', preferences: '偏好', traveler_count: '出行人数' };
const money = (value: number | null) => value === null ? '待核实' : `¥${value.toLocaleString('zh-CN')}`;

function RequestSummary({ request, missing }: { request: TripRequest; missing: string[] }) {
  return <section className="requirements" aria-label="当前需求">
    <h3>当前需求<span>尚未形成新版本</span></h3>
    <dl>{[
      ['目的地', request.destination || '未提供'], ['出发地', request.origin || '未提供'],
      ['旅行天数', request.days ? `${request.days} 天` : '未提供'], ['出发日期', request.start_date || '未提供'],
      ['预算', request.budget === null ? '未设置' : money(request.budget)], ['偏好', request.preferences.join('、') || '暂无'],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {missing.length > 0 && <p className="missing-fields"><AlertCircle size={14} />还需要补充：{missing.map(field => fields[field] || field).join('、')}</p>}
  </section>;
}

function VersionTimeline({ version, mode }: { version: ItineraryVersion; mode: Props['mode'] }) {
  const [selectedDay, setSelectedDay] = useState(version.itinerary.days[0]?.day_index);
  const day = version.itinerary.days.find(item => item.day_index === selectedDay) || version.itinerary.days[0];
  return <section className="official-itinerary" aria-label={`行程版本 ${version.version_no}`}>
    <div className="itinerary-title"><div><span className="section-eyebrow">{mode === 'demo' ? '本地演示行程' : '已保存行程'}</span><h2>{version.trip_request.destination} {version.trip_request.days} 日游</h2></div><span className="version-label">版本 {version.version_no}</span></div>
    <p className="itinerary-description">{version.itinerary.summary}</p>
    <div className="itinerary-facts"><div><span>预计总花费</span><strong>{money(version.itinerary.total_cost)}</strong></div><div><span>旅行节奏</span><strong>{({ relaxed: '轻松', balanced: '适中', compact: '紧凑' })[version.trip_request.pace]}</strong></div></div>
    <div className={`validation-state ${version.validation.passed ? '' : 'has-warning'}`}><CheckCircle2 size={14} />{mode === 'demo' ? '演示结构已检查 · 真实路线待核实' : version.validation.passed ? '路线校验已通过' : '路线校验未通过'}</div>
    {version.validation.issues.length > 0 && <div className="route-warnings">{version.validation.issues.map((issue, index) => <p key={index}><AlertCircle size={13} />{issue.day_index ? `第 ${issue.day_index} 天：` : ''}{issue.message}</p>)}</div>}
    <div className="day-tabs" role="tablist" aria-label="每日行程">{version.itinerary.days.map(item => <button key={item.day_index} role="tab" id={`day-${item.day_index}`} aria-controls="day-timeline" aria-selected={item.day_index === day?.day_index} onClick={() => setSelectedDay(item.day_index)}>第 {item.day_index} 天</button>)}</div>
    {day && <div role="tabpanel" id="day-timeline" aria-labelledby={`day-${day.day_index}`}>
      <div className="day-meta"><span>{day.date || `第 ${day.day_index} 天`}</span><span>当日预算 {money(day.total_cost)}</span></div>
      <ol className="itinerary-timeline">{day.items.map((item, index) => {
        const route = index ? version.routes.find(route => route.from_item_id === day.items[index - 1].item_id && route.to_item_id === item.item_id) : null;
        return <li key={item.item_id}>
          {index > 0 && <div className="timeline-transit"><RouteIcon size={12} />{route ? `${route.mode === 'walking' ? '步行' : '驾车'} ${route.duration_minutes} 分钟 · ${route.distance_km} 公里` : `交通约 ${item.travel_from_previous_minutes} 分钟`}{mode === 'demo' && ' · 样例'}</div>}
          <div className="timeline-stop"><time>{item.start_time.slice(0, 5)}</time><span className="timeline-dot" /><div><h3>{item.name}</h3><p><MapPin size={12} />{item.address || '地址待确认'}</p><p><Clock3 size={12} />停留 {item.duration_minutes} 分钟<span>费用 {money(item.estimated_cost)}</span></p>{item.notes && <div className="stop-note">{item.notes}</div>}</div></div>
        </li>;
      })}</ol>
      {day.warnings.map((warning, index) => <p className="day-warning" key={index}><AlertCircle size={13} />{warning}</p>)}
    </div>}
  </section>;
}

export function ItineraryPanel(props: Props) {
  const { itinerary, latest, request, busy, progress, mode } = props;
  const [choice, setChoice] = useState<{ view: string; runId?: string; selection: number } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const defaultView = latest?.status === 'completed' && latest.intent === 'direct_search' ? 'search' : 'itinerary';
  const [seenSelection, setSeenSelection] = useState(props.versionSelection);
  if (seenSelection !== props.versionSelection) {
    setSeenSelection(props.versionSelection);
    setChoice({ view: 'itinerary', runId: latest?.run_id, selection: props.versionSelection });
  }
  const view = choice?.runId === latest?.run_id ? choice?.view || defaultView : defaultView;
  const choose = (value: string) => setChoice({ view: value, runId: latest?.run_id, selection: props.versionSelection });
  const steps = ['understand', 'search', 'plan', 'validate', 'save'];
  const stage = steps.indexOf(progress?.stage || 'queued');
  const failed = latest?.status === 'failed' && !busy;
  const pending = latest?.status === 'needs_input' && !busy;
  const savedOnFailure = failed ? latest.result.saved_version : null;
  const baseNo = latest?.base_version_no ?? itinerary.currentNo;
  const nextNo = (baseNo ?? 0) + 1;

  async function retry() {
    setActionError(null); setRetrying(true);
    try { await props.onRetry(); } catch (error) { setActionError(error instanceof Error ? error.message : '重试失败'); }
    finally { setRetrying(false); }
  }

  return <aside className="itinerary-column" aria-label="当前行程">
    <header className="itinerary-panel-heading"><div><RouteIcon size={18} /><h2>当前行程</h2></div>{itinerary.selectedNo !== null && <span>版本 {itinerary.selectedNo}</span>}</header>
    <div className="itinerary-scroll">
      {(busy || failed || pending) && <section className={`run-state ${failed ? 'is-error' : pending ? 'is-pending' : ''}`} aria-live="polite">
        <h3>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <AlertCircle size={16} />}{busy ? (latest?.intent === 'direct_search' ? '正在搜索景点' : baseNo ? `正在生成版本 ${nextNo}` : '正在规划你的旅行') : failed ? (latest.intent === 'revise' || latest.intent === 'create' ? `版本 ${nextNo} 生成未完成` : '本轮请求未完成') : '等待补充信息'}</h3>
        {itinerary.currentNo !== null && <p>当前正式行程：版本 {itinerary.currentNo}{itinerary.selectedNo !== itinerary.currentNo ? ` · 正在查看版本 ${itinerary.selectedNo}` : ''}</p>}
        {busy && <><p>{progress?.message || '消息已提交，等待处理'}</p><ol className="progress-steps">{['解析旅行需求', '搜索候选景点', '规划每日路线', '校验时间和预算', '保存行程版本'].map((label, index) => <li key={label} className={index === stage ? 'active' : index < stage ? 'done' : ''}>{index < stage ? <Check size={13} /> : index === stage ? <LoaderCircle className="animate-spin" size={13} /> : <Circle size={11} />}{label}</li>)}</ol></>}
        {pending && <p>{latest.pending_question || '本轮修改尚未完成'}</p>}
        {failed && <><p>{latest.error?.message || '暂时无法完成，请继续修改。'}</p>{savedOnFailure && <p>服务端已保存版本 {savedOnFailure.version_no}，请先查看保存结果。</p>}<div className="run-actions">{savedOnFailure ? <Button variant="outline" size="sm" onClick={() => { itinerary.select(savedOnFailure.version_no); choose('itinerary'); }}>查看已保存版本 {savedOnFailure.version_no}</Button> : latest.error?.retryable && <Button variant="outline" size="sm" onClick={() => void retry()} disabled={retrying}><RotateCcw size={14} />重新尝试</Button>}<Button variant="ghost" size="sm" onClick={props.onContinue}><MessageCircle size={14} />继续修改</Button></div></>}
      </section>}
      {actionError && <div className="workspace-alert" role="alert">{actionError}</div>}
      {itinerary.error && <div className="workspace-alert" role="alert">行程版本读取失败：{itinerary.error.message}<button onClick={itinerary.retry}>重新加载</button></div>}
      {request && (!itinerary.version || pending || busy || failed) && <RequestSummary request={request} missing={pending ? latest.missing_fields : []} />}
      <nav className="result-tabs" aria-label="行程内容">
        <button aria-pressed={view === 'itinerary'} onClick={() => choose('itinerary')}><RouteIcon size={14} />行程</button>
        <button aria-pressed={view === 'search'} onClick={() => choose('search')}><Search size={14} />搜索结果{props.candidates.length > 0 && <span>{props.candidates.length}</span>}</button>
        <button aria-pressed={view === 'map'} onClick={() => choose('map')}><Map size={14} />地图</button>
      </nav>
      {view === 'itinerary' ? itinerary.loading || props.loading ? <div className="panel-loading"><LoaderCircle className="animate-spin" size={20} />正在读取行程</div> : itinerary.version ? <VersionTimeline key={`${itinerary.version.trip_id}:${itinerary.version.version_no}`} version={itinerary.version} mode={mode} /> : <div className="itinerary-empty"><span className="empty-route"><RouteIcon size={30} strokeWidth={1.4} /></span><h3>{busy ? '你的行程正在路上' : '还没有行程'}</h3><p>告诉我你想去哪里、玩几天、从哪里出发。</p><blockquote>例如：从广州出发去长沙玩三天，喜欢博物馆和本地美食。</blockquote></div> : view === 'search' ? <>
        {itinerary.currentNo !== null && <div className="search-version-note">当前正式行程：版本 {itinerary.currentNo} · 搜索不会修改行程</div>}
        {props.candidatesArePrevious && <p className="search-version-note">当前展示上一轮已完成的搜索结果。</p>}
        {!!props.searchResult?.unmet_conditions.length && <p className="search-version-note">待核实：{props.searchResult.unmet_conditions.join('；')}</p>}
        <PlacesPanel candidates={props.candidates} recommendations={props.recommendations} selectedId={selectedId} onSelect={setSelectedId} onAsk={props.onAsk} isLoading={busy || props.loading} mode={mode} destination={props.candidates[0]?.city}
          onAdd={async name => { setActionError(null); try { await props.onAdd(name); choose('itinerary'); } catch (error) { setActionError(error instanceof Error ? error.message : '添加失败'); } }} canAdd={props.canAdd} />
      </> : <div className="itinerary-map"><MapPanel candidates={props.candidates} selectedId={selectedId} onSelect={setSelectedId} mode={mode} /></div>}
    </div>
  </aside>;
}
