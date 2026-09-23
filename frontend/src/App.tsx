import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, Link, useNavigate, useParams } from 'react-router-dom';
import { Compass, History, LogOut, MapPin, MessageCircle, PanelLeft, Plus, RefreshCw, Route as RouteIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ChatPanel } from '@/components/workspace/chat-panel';
import { ItineraryPanel } from '@/components/workspace/itinerary-panel';
import { useWorkspace } from '@/hooks/use-workspace';
import { useItinerary } from '@/hooks/use-itinerary';
import { readSessionIndex, rememberSession } from '@/lib/session-index';
import { AuthGate, AuthPage, useAuth } from '@/components/auth/auth-page';

function WorkspaceRoute() {
  const { sessionId } = useParams();
  return <Workspace key={sessionId || 'home'} sessionId={sessionId} />;
}

function Workspace({ sessionId }: { sessionId?: string }) {
  const navigate = useNavigate();
  const { user, signOut } = useAuth();
  const workspace = useWorkspace(sessionId);
  const itinerary = useItinerary(workspace.session, workspace.runs, workspace.mode);
  const [entries, setEntries] = useState(() => readSessionIndex(workspace.mode));
  const [mobileView, setMobileView] = useState('chat');
  const [versionSelection, setVersionSelection] = useState(0);
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null);
  const [creating, setCreating] = useState(false);
  const [creationError, setCreationError] = useState<string | null>(null);
  const needsSession = workspace.mode === 'api' && !sessionId;
  const latest = workspace.runs.at(-1);
  const readOnly = itinerary.selectedNo !== (workspace.session?.current_version_no ?? itinerary.currentNo);

  useEffect(() => {
    if (workspace.session) setEntries(rememberSession(workspace.mode, workspace.session, itinerary.currentRequest));
  }, [workspace.session, workspace.mode, itinerary.currentRequest]);

  async function newSession() {
    if (creating) return;
    setCreating(true); setCreationError(null);
    try { navigate(`/sessions/${await workspace.createSession()}`); }
    catch (error) { setCreationError(error instanceof Error ? error.message : '暂时无法创建会话，请重试。'); }
    finally { setCreating(false); }
  }

  function continueEditing(text = '') {
    itinerary.select(workspace.session?.current_version_no ?? null);
    setVersionSelection(value => value + 1);
    setDraft({ text, nonce: Date.now() });
    setMobileView('chat');
  }

  return <div className="travel-app">
    <header className="travel-header">
      <Link to="/" className="travel-brand"><span><Compass size={22} /></span><strong>Trip Agents</strong><small>旅行工作台</small></Link>
      <div className="travel-header-right"><span className="environment-label">{workspace.mode === 'demo' ? '本地演示' : '在线会话'}</span><span className="travel-user" title={user?.email}>{user?.email}</span><Button variant="ghost" size="icon" title="刷新当前旅行" aria-label="刷新当前旅行" disabled={needsSession || workspace.isLoading} onClick={() => { workspace.refresh(); itinerary.retry(); }}><RefreshCw size={16} /></Button><Button variant="ghost" size="icon" title="退出登录" aria-label="退出登录" onClick={() => { signOut(); navigate('/auth'); }}><LogOut size={16} /></Button></div>
    </header>
    <nav className="mobile-workspace-tabs" aria-label="工作台视图">{[
      { id: 'trips', label: '我的旅行', icon: PanelLeft }, { id: 'chat', label: '对话', icon: MessageCircle }, { id: 'itinerary', label: '当前行程', icon: RouteIcon },
    ].map(({ id, label, icon: Icon }) => <button key={id} aria-pressed={mobileView === id} onClick={() => setMobileView(id)}><Icon size={16} />{label}</button>)}</nav>
    {creationError && <div className="workspace-alert" role="alert">{creationError}<button onClick={() => void newSession()}>重新创建</button></div>}
    <main className="travel-layout" data-mobile-view={mobileView}>
      <aside className="travel-sidebar" aria-label="我的旅行">
        <div className="sidebar-title"><h1>我的旅行</h1><span>{entries.length}</span></div>
        <Button className="new-trip-button" variant="outline" onClick={() => void newSession()} disabled={creating}><Plus size={17} />{creating ? '正在创建' : '新建旅行'}</Button>
        <div className="session-list">
          {entries.length === 0 && <p className="sidebar-empty">暂无旅行</p>}
          {entries.map(entry => <Link key={entry.id} className={`session-link ${entry.id === workspace.session?.session_id ? 'is-active' : ''}`} to={`/sessions/${entry.id}`} onClick={() => setMobileView('chat')} aria-current={entry.id === workspace.session?.session_id ? 'page' : undefined}>
            <MapPin size={16} /><div><strong>{entry.title}</strong><small>{entry.version ? `版本 ${entry.version}` : '需求收集中'}</small></div>
          </Link>)}
        </div>
        <section className="version-list" aria-label="版本历史">
          <h2><History size={14} />版本历史</h2>
          {itinerary.versions.length === 0 ? <p>尚无已保存版本</p> : itinerary.versions.map(version => <button key={version.version_no} className={itinerary.selectedNo === version.version_no ? 'is-active' : ''} onClick={() => { itinerary.select(version.version_no); setVersionSelection(value => value + 1); setMobileView('itinerary'); }}>
            <span className="version-dot" /><span><strong>版本 {version.version_no}</strong><small>{version.summary}</small></span><em>{version.version_no === itinerary.currentNo ? '当前' : '查看'}</em>
          </button>)}
          {itinerary.hasMore && <Button variant="ghost" size="sm" onClick={itinerary.loadMore}>更早版本</Button>}
        </section>
        <footer className="sidebar-footer"><span className="online-dot" />{workspace.mode === 'demo' ? '保存在此浏览器' : '此浏览器的旅行记录'}</footer>
      </aside>
      <div className="conversation-column">
        {readOnly && <div className="readonly-banner">{itinerary.selectedNo ? `正在查看版本 ${itinerary.selectedNo}，只读` : '本轮已保存新版本，请先核对'}<button onClick={() => continueEditing()}>查看当前版本并继续修改</button></div>}
        {needsSession ? <section className="api-start"><span className="empty-compass"><Compass size={28} /></span><h2>下一站，想去哪儿？</h2><Button onClick={() => void newSession()} disabled={creating}><Plus size={16} />新建旅行</Button></section> : <ChatPanel
          runs={workspace.runs} isBusy={workspace.isBusy} isLoading={workspace.isLoading} readOnly={readOnly}
          progress={workspace.progress} error={workspace.error} onSubmit={workspace.submit}
          onRetry={workspace.retry} onLoadMore={workspace.loadMore} hasMore={workspace.hasMore}
          mode={workspace.mode} composerDraft={draft} baseVersion={workspace.session?.current_version_no ?? null}
        />}
      </div>
      <ItineraryPanel key={workspace.session?.session_id || 'empty'}
        itinerary={itinerary} latest={latest} request={latest?.result.trip_request || workspace.request}
        versionSelection={versionSelection}
        progress={workspace.progress} busy={workspace.isBusy} loading={workspace.isLoading}
        candidates={workspace.candidates} recommendations={workspace.recommendations} searchResult={workspace.searchResult}
        candidatesArePrevious={workspace.candidatesArePrevious} mode={workspace.mode}
        onRetry={workspace.retry} onContinue={() => continueEditing()} onAsk={text => { setDraft({ text, nonce: Date.now() }); setMobileView('chat'); }}
        onAdd={async name => { if (readOnly) return; await workspace.submit(`请把${name}添加到当前行程。`); }} canAdd={!readOnly && !workspace.isBusy && !!itinerary.version}
      />
    </main>
  </div>;
}

export default function App() {
  return <AuthGate><Routes><Route path="/auth" element={<AuthPage />} /><Route path="/" element={<WorkspaceRoute />} /><Route path="/sessions/:sessionId" element={<WorkspaceRoute />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes></AuthGate>;
}
