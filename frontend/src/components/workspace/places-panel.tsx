import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpRight, Clock3, MapPin, Plus, Search, SlidersHorizontal, Ticket, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { getPlaceImage } from '@/lib/place-images'
import type { Recommendation, SearchCandidate } from '@/lib/types'

/** 景点候选列表与地图共享 place_id，过滤只改变列表，不修改搜索结果。 */
export interface PlacesPanelProps {
  candidates: SearchCandidate[]
  recommendations: Recommendation[]
  selectedId: string | null
  /** 每次主动选择递增，允许重复点击同一地点重新定位列表。 */
  selectionRevision?: number
  onSelect: (id: string) => void
  onAsk: (message: string) => void
  isLoading: boolean
  mode: 'demo' | 'api'
  destination?: string | null
  onAdd?: (name: string) => Promise<void>
  canAdd?: boolean
}

/** 图片失败时保留稳定尺寸，避免网络图片异常使条目重新排版。 */
function PlaceImage({ candidate, className = '' }: { candidate: SearchCandidate; className?: string }) {
  const [failed, setFailed] = useState(false)
  const image = getPlaceImage(candidate)
  return (
    <div className={`place-image ${className}`}>
      {image && !failed ? <img src={image.url} alt={candidate.name} loading="lazy" onError={() => setFailed(true)} /> : <MapPin size={24} aria-hidden="true" />}
    </div>
  )
}

/** 票价和营业时间当前没有可靠数据，详情明确保留未知状态。 */
function PlaceDetail({ candidate, reason, onAsk, onClose, onRestoreFocus }: { candidate: SearchCandidate | null; reason?: string; onAsk: (message: string) => void; onClose: () => void; onRestoreFocus: () => void }) {
  const pendingAskRef = useRef<string | null>(null)
  const image = candidate ? getPlaceImage(candidate) : null
  return (
    <Dialog open={candidate !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="place-detail-dialog" onCloseAutoFocus={(event) => {
        event.preventDefault()
        const message = pendingAskRef.current
        pendingAskRef.current = null
        // 等待模态层退出并解除背景隐藏后，再让聊天输入框接收草稿和焦点。
        if (message) onAsk(message)
        else onRestoreFocus()
      }}>
        {candidate && <>
          <PlaceImage key={candidate.place_id} candidate={candidate} className="place-detail-image" />
          <DialogHeader>
            <span className="place-detail-category">{candidate.category || '景点'}</span>
            <DialogTitle>{candidate.name}</DialogTitle>
            <DialogDescription><MapPin size={14} />{candidate.address || candidate.city || '地址待确认'}</DialogDescription>
          </DialogHeader>
          {reason && <p className="place-detail-reason">{reason}</p>}
          <dl className="place-detail-facts">
            <div><dt><Ticket size={16} />门票参考</dt><dd>{candidate.estimated_cost == null ? '暂无可靠数据' : `¥${candidate.estimated_cost}`}</dd></div>
            <div><dt><Clock3 size={16} />开放时间</dt><dd>{candidate.opening_hours || '暂无可靠数据'}</dd></div>
            <div><dt><MapPin size={16} />地点来源</dt><dd>{candidate.source === 'demo' ? '本地示例数据' : '高德地图'}</dd></div>
          </dl>
          {image?.credit && <p className="image-credit">图片：<a href={image.source} target="_blank" rel="noreferrer" title="查看图片原始文件与许可信息">{image.credit}</a></p>}
          <Button className="place-preference-button" onClick={() => { pendingAskRef.current = `我想找更多类似${candidate.name}的${candidate.category || '景点'}。`; onClose() }}><Search size={16} />找类似的地方</Button>
        </>}
      </DialogContent>
    </Dialog>
  )
}

/** 提供可组合的关键词与类别筛选，并将条目选择同步到父级地图。 */
export function PlacesPanel({ candidates, recommendations, selectedId, selectionRevision = 0, onSelect, onAsk, isLoading, mode, destination, onAdd, canAdd }: PlacesPanelProps) {
  const [adding, setAdding] = useState(false)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('全部')
  const [detailId, setDetailId] = useState<string | null>(null)
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const cardRefs = useRef(new Map<string, HTMLElement>())
  const previousSelectionRef = useRef<{ id: string | null; revision: number } | null>(null)
  const pendingScrollRef = useRef<string | null>(null)
  const categories = useMemo(() => ['全部', ...new Set(candidates.map((candidate) => candidate.category).filter(Boolean))], [candidates])
  const activeCategory = categories.includes(category) ? category : '全部'
  const reasons = useMemo(() => new Map(recommendations.map((recommendation) => [recommendation.place_id, recommendation.reason])), [recommendations])
  const visibleCandidates = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    return candidates.filter((candidate) => {
      const matchesCategory = activeCategory === '全部' || candidate.category === activeCategory
      const searchable = [candidate.name, candidate.category, candidate.address, candidate.city].filter(Boolean).join(' ').toLocaleLowerCase()
      return matchesCategory && (!keyword || searchable.includes(keyword))
    })
  }, [activeCategory, candidates, query])
  const detail = candidates.find((candidate) => candidate.place_id === detailId) || null
  const city = destination || candidates[0]?.city

  useEffect(() => {
    // 主动选择同一地点也可重新定位；仅修改筛选时版本不变，不会自动撤销筛选。
    if (previousSelectionRef.current?.id === selectedId && previousSelectionRef.current.revision === selectionRevision) return
    if (selectedId === null) {
      previousSelectionRef.current = { id: null, revision: selectionRevision }
      pendingScrollRef.current = null
      return
    }
    const selected = candidates.find((candidate) => candidate.place_id === selectedId)
    if (!selected) return
    previousSelectionRef.current = { id: selectedId, revision: selectionRevision }
    pendingScrollRef.current = selectedId
    if (activeCategory !== '全部' && selected.category !== activeCategory) setCategory('全部')
    const keyword = query.trim().toLocaleLowerCase()
    const searchable = [selected.name, selected.category, selected.address, selected.city].filter(Boolean).join(' ').toLocaleLowerCase()
    if (keyword && !searchable.includes(keyword)) setQuery('')
  }, [activeCategory, candidates, query, selectedId, selectionRevision])

  const scrollToPendingSelection = useCallback(() => {
    const list = listRef.current
    const selected = pendingScrollRef.current ? cardRefs.current.get(pendingScrollRef.current) : null
    // 隐藏页签没有可靠尺寸，保留待滚动目标，等待列表重新显示后再定位。
    if (!list || !selected || list.clientHeight === 0 || list.clientWidth === 0) return
    const viewport = list.getBoundingClientRect()
    const item = selected.getBoundingClientRect()
    const top = item.top - viewport.top
    const bottom = item.bottom - viewport.top
    // 直接调整列表容器，避免 scrollIntoView 连带滚动整个页面或外层页签。
    if (top < 0 || item.height > list.clientHeight) list.scrollTop += top
    else if (bottom > list.clientHeight) list.scrollTop += bottom - list.clientHeight
    pendingScrollRef.current = null
  }, [])

  useEffect(() => { scrollToPendingSelection() }, [selectedId, selectionRevision, visibleCandidates, scrollToPendingSelection])

  useEffect(() => {
    const list = listRef.current
    if (!list || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(scrollToPendingSelection)
    observer.observe(list)
    return () => observer.disconnect()
  }, [scrollToPendingSelection])

  return (
    <section className="places-panel workspace-panel" aria-labelledby="places-heading">
      <header className="panel-heading places-heading"><div><span className="section-eyebrow">目的地灵感</span><h2 id="places-heading">{city ? `发现${city}` : '发现好去处'}</h2></div><span className="result-count">{candidates.length} 个地点</span></header>
      <div className="places-toolbar">
        <div className="place-search"><Search size={16} aria-hidden="true" /><label className="sr-only" htmlFor="place-search">搜索当前候选景点</label><input ref={searchRef} id="place-search" type="search" value={query} placeholder="搜索景点、街区…" onChange={(event) => setQuery(event.target.value)} />{query && <button type="button" aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} /></button>}</div>
        <div className="place-category-filters" role="group" aria-label="景点类别">{categories.map((item) => <button type="button" key={item} className={activeCategory === item ? 'category-filter is-active' : 'category-filter'} aria-pressed={activeCategory === item} onClick={() => setCategory(item)}>{item}</button>)}</div>
      </div>
      <div ref={listRef} className="place-list" aria-busy={isLoading}>
        {isLoading && candidates.length === 0 ? <div className="places-skeleton" role="status" aria-label="正在搜索景点">{[0, 1, 2].map((key) => <div key={key} className="place-skeleton"><div /><span /><span /></div>)}</div> : visibleCandidates.length === 0 ? (
          <div className="places-empty"><SlidersHorizontal size={25} /><h3>{candidates.length > 0 ? '没有匹配的地点' : '目的地，等你来发现'}</h3><p>{candidates.length > 0 ? '换个关键词，或者看看其他类别。' : '从一个想去的城市开始。'}</p>{candidates.length > 0 && <Button variant="outline" size="sm" onClick={() => { setQuery(''); setCategory('全部') }}>清除筛选</Button>}</div>
        ) : visibleCandidates.map((candidate) => {
          // 编号来自完整结果，筛选后仍与地图标记保持一致。
          const number = candidates.findIndex((item) => item.place_id === candidate.place_id) + 1
          const selected = selectedId === candidate.place_id
          const reason = reasons.get(candidate.place_id)
          return <article className={`place-card${selected ? ' is-selected' : ''}`} key={candidate.place_id} ref={(node) => {
            if (node) cardRefs.current.set(candidate.place_id, node)
            else cardRefs.current.delete(candidate.place_id)
          }}>
            <button type="button" className="place-select" aria-label={`在地图上查看${candidate.name}`} aria-pressed={selected} onClick={() => onSelect(candidate.place_id)}>
              <PlaceImage candidate={candidate} />
              <div className="place-card-content"><div className="place-name-row"><span className="place-number">{String(number).padStart(2, '0')}</span><h3>{candidate.name}</h3></div><span className="place-category">{candidate.category || '景点'}</span><p className="place-address"><MapPin size={12} /><span>{candidate.address || candidate.city || '地址待确认'}</span></p></div>
            </button>
            {reason && <p className="place-recommendation">{reason}</p>}
            {onAdd && <Button className="add-to-trip" variant="outline" size="sm" disabled={!canAdd || adding} onClick={async () => { setAdding(true); try { await onAdd(candidate.name) } finally { setAdding(false) } }}><Plus size={13} />添加到当前行程</Button>}
            <footer className="place-card-footer"><span>{candidate.source === 'demo' || mode === 'demo' ? '示例地点' : '高德地图'}</span><Tooltip><TooltipTrigger asChild><button type="button" className="place-details-button" aria-label={`查看${candidate.name}详情`} onClick={(event) => { detailTriggerRef.current = event.currentTarget; onSelect(candidate.place_id); setDetailId(candidate.place_id) }}>地点详情<ArrowUpRight size={13} /></button></TooltipTrigger><TooltipContent>查看地点信息</TooltipContent></Tooltip></footer>
          </article>
        })}
      </div>
      <footer className="places-bottom-note"><MapPin size={13} /><span>{query || activeCategory !== '全部' ? `${visibleCandidates.length} / ${candidates.length} 个地点` : '想去的地方，慢慢收集'}</span></footer>
      <PlaceDetail candidate={detail} reason={detail ? reasons.get(detail.place_id) : undefined} onAsk={onAsk} onClose={() => setDetailId(null)} onRestoreFocus={() => {
        // 普通关闭回到打开详情的按钮；结果更新移除按钮时，回到稳定的搜索入口。
        if (detailTriggerRef.current?.isConnected) detailTriggerRef.current.focus()
        else searchRef.current?.focus()
      }} />
    </section>
  )
}
