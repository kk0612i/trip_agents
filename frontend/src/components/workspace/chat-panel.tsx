import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ArrowUp, Compass, LoaderCircle, RotateCcw, MessageCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { RunView } from '@/lib/types'

/** 对话面板只处理展示与输入，运行生命周期和请求重试由上层统一管理。 */
export interface ChatPanelProps {
  runs: RunView[]
  readOnly?: boolean
  baseVersion: number | null
  isBusy: boolean
  isLoading: boolean
  progress: { stage: string; message: string } | null
  error: Error | null
  onSubmit: (message: string) => Promise<void>
  onRetry: () => Promise<void>
  onLoadMore: () => void
  hasMore: boolean
  mode: 'demo' | 'api'
  /** 外部景点操作仅填入草稿，由用户确认后发送。 */
  composerDraft?: { text: string; nonce: number } | null
}

const STARTERS = ['长沙，三天慢慢逛', '找一些值得去的博物馆', '想去适合散步的地方']

/** 渲染已经确认的运行结果；阶段进度单独展示，不模拟逐字输出或内部推理。 */
function RunMessages({ run }: { run: RunView }) {
  const reply = run.response || run.pending_question
  return (
    <div className="message-turn">
      <article className="message message-user" aria-label="我的消息">
        <div className="message-bubble">{run.message}</div>
      </article>
      {reply && (
        <article className="message message-assistant" aria-label="旅行助手的回复">
          <span className="assistant-avatar" aria-hidden="true"><Compass size={17} /></span>
          <div className="assistant-message-content">
            <span className="message-author">旅行助手</span>
            <div className="message-bubble">{reply}</div>
          </div>
        </article>
      )}
      {run.status === 'failed' && !reply && (
        <article className="message message-assistant message-failed" aria-label="运行失败">
          <span className="assistant-avatar" aria-hidden="true"><Compass size={17} /></span>
          <div className="assistant-message-content">
            <span className="message-author">旅行助手</span>
            <div className="message-bubble">{run.error?.message || '这次搜索未完成，请稍后重试。'}</div>
          </div>
        </article>
      )}
    </div>
  )
}

/** 旅行对话输入支持中文组合输入、明确的提交状态与失败后保留草稿。 */
export function ChatPanel({
  runs, isBusy, isLoading, progress, error, onSubmit, onRetry,
  onLoadMore, hasMore, mode, composerDraft, readOnly = false, baseVersion,
}: ChatPanelProps) {
  const [draft, setDraft] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedToBottom = useRef(true)
  const composingRef = useRef(false)
  const submittingRef = useRef(false)
  const busy = isBusy || isSubmitting || isRetrying
  const messageLength = Array.from(draft.trim()).length
  const canSubmit = messageLength > 0 && messageLength <= 4000 && !busy && !isLoading && !readOnly
  const latestRun = runs.at(-1)

  useEffect(() => {
    if (!composerDraft) return
    setDraft(composerDraft.text)
    setLocalError(null)
    textareaRef.current?.focus()
  }, [composerDraft])

  useEffect(() => {
    // 只在最新一轮变化时滚动，向前加载历史消息时不会跳回底部。
    const viewport = scrollRef.current
    if (viewport) { viewport.scrollTop = viewport.scrollHeight; pinnedToBottom.current = true }
  }, [latestRun?.run_id, latestRun?.response, latestRun?.status, progress?.message])

  useEffect(() => {
    const viewport = scrollRef.current
    if (!viewport || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (pinnedToBottom.current && viewport.clientHeight > 0) viewport.scrollTop = viewport.scrollHeight
    })
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [])

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!canSubmit || submittingRef.current || composingRef.current) return
    const message = draft.trim()
    // ref 同步上锁，覆盖 React 更新前的连续回车或连续点击。
    submittingRef.current = true
    setIsSubmitting(true)
    setLocalError(null)
    try {
      await onSubmit(message)
      setDraft((current) => current.trim() === message ? '' : current)
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : '消息没有发送成功，请重试。')
    } finally {
      submittingRef.current = false
      setIsSubmitting(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return
    // 中文输入法的 Enter 用于确认候选词，不能把未完成的组合输入当作提交。
    if (event.nativeEvent.isComposing || composingRef.current || event.keyCode === 229) return
    event.preventDefault()
    void submit()
  }

  async function retry() {
    if (busy || submittingRef.current) return
    submittingRef.current = true
    setIsRetrying(true)
    setLocalError(null)
    try {
      await onRetry()
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : '重试失败，请稍后再试。')
    } finally {
      submittingRef.current = false
      setIsRetrying(false)
    }
  }

  const visibleError = error?.message || localError

  return (
    <section className="chat-panel workspace-panel" aria-labelledby="chat-heading">
      <header className="panel-heading chat-heading">
        <div className="panel-title"><span className="panel-icon"><MessageCircle size={18} /></span><h2 id="chat-heading">对话</h2></div>
        <span className="connection-status"><span />{mode === 'demo' ? '体验模式' : '在线会话'}</span>
      </header>
      <div className="message-list" ref={scrollRef} onScroll={event => { const element = event.currentTarget; if (element.clientHeight > 0) pinnedToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 60 }} role="log" aria-label="对话记录" aria-live="polite" aria-busy={isLoading}>
        {hasMore && <Button className="load-history" variant="ghost" size="sm" onClick={onLoadMore} disabled={isLoading}>更早的消息</Button>}
        {isLoading && runs.length === 0 ? (
          <div className="panel-loading" role="status"><LoaderCircle className="animate-spin" size={20} /><span>正在加载会话</span></div>
        ) : runs.length === 0 ? (
          <div className="chat-empty">
            <span className="empty-compass"><Compass size={26} /></span>
            <h3>下一站，想去哪儿？</h3>
            <p>城市、天数，或一个想去的地方。</p>
            <div className="conversation-starters">{STARTERS.map((starter) => (
              <button type="button" key={starter} disabled={busy} onClick={() => { setDraft(starter); textareaRef.current?.focus() }}>
                <span>{starter}</span><ArrowUp size={14} />
              </button>
            ))}</div>
          </div>
        ) : runs.map((run) => <RunMessages key={run.run_id} run={run} />)}
        {isBusy && <div className="run-progress" role="status"><LoaderCircle className="animate-spin" size={14} /><span>{progress?.message || (latestRun?.status === 'queued' ? '等待开始' : '正在处理你的需求')}</span></div>}
      </div>
      <div className="chat-composer-area">
        {baseVersion !== null && <div className="conversation-hint">{readOnly ? '历史版本只读' : `本次修改基于版本 ${baseVersion}`}</div>}
        {visibleError && <div className="chat-error" role="alert"><p>{visibleError}</p><Button variant="ghost" size="sm" disabled={busy || readOnly} onClick={() => void retry()}><RotateCcw size={14} />{latestRun?.status === 'failed' && (!latestRun.error?.retryable || latestRun.result.saved_version) ? '刷新状态' : '重试'}</Button></div>}
        <form className="chat-composer" onSubmit={(event) => void submit(event)}>
          <label className="sr-only" htmlFor="trip-message">旅行需求</label>
          <textarea
            ref={textareaRef} id="trip-message" value={draft} rows={3}
            placeholder={readOnly ? '返回当前版本后继续修改' : '输入你的要求…'} disabled={isLoading || readOnly}
            onChange={(event) => { setDraft(event.target.value); setLocalError(null) }}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
            onKeyDown={handleKeyDown}
            aria-invalid={messageLength > 4000}
            aria-describedby={messageLength > 4000 ? 'message-length-error' : undefined}
          />
          <div className="composer-actions">
            <span className={messageLength > 4000 ? 'message-counter is-invalid' : 'message-counter'} id="message-length-error">{messageLength > 3500 ? `${messageLength} / 4000` : ''}</span>
            <Tooltip><TooltipTrigger asChild><Button type="submit" size="icon" className="send-message" disabled={!canSubmit} aria-label="发送消息">{busy ? <LoaderCircle className="animate-spin" size={18} /> : <ArrowUp size={20} />}</Button></TooltipTrigger><TooltipContent>发送消息</TooltipContent></Tooltip>
          </div>
        </form>
      </div>
    </section>
  )
}
